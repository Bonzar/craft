import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

from prod_runner import operate
from receive_authorized import activate_exact
from receiver_gate import handle
from reusable_proof import Intent, OneUse
import test_reusable_proof as fixture


class CompleteBridge(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root=Path(d.name)
        self.production=self.root/'production'
        self.test=self.root/'test'
        self.config=self.root/'config'
        self.config.mkdir()
        for env in (self.production,self.test):
            (env/'releases').mkdir(parents=True)
        (self.config/'service-hook.sh').write_text('fixture')
        (self.test/'kept').write_text('isolated')
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.ledger=OneUse(self.db)
        fixture.TestReusableProof.setUpClass()
        self.issuer=fixture.TestReusableProof()
        self.issuer.setUp()
        self.policy=self.issuer.policy
        self.sent=[]

    def artifact(self, revision, run_id):
        path=self.root/(revision+'.tar.gz')
        entries={'prototypes/assertion_memory/http_server.py':b'# fixture module only\n',
                 'apps/graph_viewer/web/dist/index.html':b'<html>fixture</html>',
                 'REVISION':revision.encode()+b'\n','QA_PROFILE':b'assertion-memory-synthetic-qa-v1\n'}
        entries['SHA256SUMS']=''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name,data in entries.items()).encode()
        with tarfile.open(path,'w:gz') as tar:
            for name,data in entries.items():
                ti=tarfile.TarInfo(name); ti.size=len(data); tar.addfile(ti,io.BytesIO(data))
        return path,Intent(run_id,'12','13','14','15','16',revision,hashlib.sha256(path.read_bytes()).hexdigest())

    def remote(self, hook, *, tamper=False, deny=False):
        def exchange(msg):
            if deny: return {'status':'rejected'}
            intent=Intent(**msg['intent'])
            if msg['op']=='challenge':
                return {'status':'ok','nonce':self.ledger.issue(self.policy,intent,2000)}
            permit=self.ledger.verify_and_consume(msg['token'],self.issuer.jwks,self.policy,intent,msg['nonce'],2000)
            return {'status':'ok',**permit}
        def send(command, envelope, archive):
            self.sent.append(command)
            body=json.dumps(envelope).encode()+b'\n'
            if archive:
                body+=archive.read()
                if tamper: body+=b'x'
            return handle(command,io.BytesIO(body),
                lambda rev,sha,f: activate_exact(rev,sha,f,root=self.production,config=self.config,hook=hook),
                authorize=exchange)
        return send

    def publish(self, sh, pair):
        archive,intent=pair
        self.issuer.claims['run_id']=intent.run_id
        return operate(sh,self.policy,intent,archive,get_token=lambda aud:self.issuer.token(aud=aud))

    def test_exact_release_identities_isolation_smoke_and_rollback(self):
        events=[]
        def hook(script,action,release):
            events.append((action,release.name))
            if action=='smoke' and release.name.startswith('b'):
                raise RuntimeError('new_release_fails_smoke')
        old=self.artifact('a'*40,'11')
        self.publish(self.remote(hook),old)
        current=self.production/'current'
        prior=current.readlink()
        self.assertEqual(prior.name,f'{old[1].revision}-{old[1].sha256}')
        new=self.artifact('b'*40,'21')
        with self.assertRaisesRegex(RuntimeError,'smoke'):
            self.publish(self.remote(hook),new)
        self.assertEqual(current.readlink(),prior)
        self.assertEqual((self.test/'kept').read_text(),'isolated')
        self.assertFalse((self.test/'current').exists())
        self.assertEqual(events[-2:],[('restore',prior.name),('smoke',prior.name)])
        with self.assertRaisesRegex(ValueError,'already consumed'):
            self.publish(self.remote(hook),old)

    def test_denial_ignores_legacy_pin_and_tamper_before_activation(self):
        pair=self.artifact('c'*40,'31')
        (self.config/'allow-activation').write_text(f'{pair[1].revision} {pair[1].sha256}\n')
        with self.assertRaisesRegex(ValueError,'denied'):
            self.publish(self.remote(lambda *a:self.fail('must not invoke hooks'),deny=True),pair)
        with self.assertRaisesRegex(ValueError,'archive_digest'):
            self.publish(self.remote(lambda *a:self.fail('must not invoke hooks'),tamper=True),pair)
        self.assertFalse((self.production/'current').exists())
        self.assertFalse(any((self.production/'releases').iterdir()))
