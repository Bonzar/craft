import base64
from dataclasses import asdict
import io
import hashlib
import tempfile
from contextlib import nullcontext
from unittest.mock import patch
import json
from pathlib import Path
import sqlite3
import sys
from unittest import TestCase,main

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding,rsa

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'configured-base'))
import configured_base_test_gate as g
import test_reusable_proof as signing_fixture


class ConfiguredHost(TestCase):
    def setUp(self):
        cls=signing_fixture.TestReusableProof
        cls.setUpClass()
        self.key=cls.key
        self.jwks=cls.jwks
        self.intent=g.Intent('1','a'*40,'workflow_run','assertion-memory-v0.1.1','2','3','4','b'*40,'c'*64)
        self.policy=g.Policy('a'*40,'EN_TEST_ONLY','synthetic-vm')
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.ledger=g.Ledger(self.db)

    def write(self,frame,payload=b''):
        return io.BytesIO(json.dumps(frame).encode()+b'\n'+payload)

    def claims(self,nonce):
        p,i=self.policy,self.intent
        return dict(iss=g.ISSUER,aud=g.audience(p,i,nonce),sub='repo:'+g.REPOSITORY+':environment:'+g.ENV,
             environment=g.ENV,environment_node_id=p.environment_node_id,repository=g.REPOSITORY,
             repository_id=p.repository_id,repository_owner='Bonzar',repository_owner_id=str(g.OWNER_ID),
             workflow_ref=g.CALLER,workflow_sha=i.control_sha,sha=i.control_sha,ref='refs/heads/main',
             ref_type='branch',run_attempt='1',run_id=i.run,event_name=i.trigger,runner_environment='github-hosted',
             job_workflow_ref=g.REUSABLE+'@refs/heads/main',job_workflow_sha=p.reusable_sha,iat=2000,nbf=1400,exp=2300)

    def token(self,nonce,**update):
        payload=self.claims(nonce)|update
        def encode(x):return base64.urlsafe_b64encode(x).decode().rstrip('=')
        pair='.'.join(encode(json.dumps(x).encode()) for x in (dict(alg='RS256',kid='fixture'),payload))
        signature=self.key.sign(pair.encode(),padding.PKCS1v15(),hashes.SHA256())
        return pair+'.'+encode(signature)

    def test_exact_configured_claim_before_canonical_writer_and_anti_replay(self):
        staged=[]
        write=lambda revision,digest,stream:staged.append((revision,digest,stream.read()))
        envelope={'op':'challenge','intent':asdict(self.intent)}
        nonce=g.receive(self.write(envelope),self.policy,self.ledger,write,
                        keys=lambda:self.jwks,clock=lambda:2000)['nonce']
        self.assertEqual(staged,[])
        frame=envelope|{'op':'deploy','nonce':nonce}
        for claim,replacement in (
            ('environment','assertion-memory-test'),('workflow_ref','legacy'),
            ('event_name','workflow_dispatch'),('job_workflow_sha','f'*40),
            ('job_workflow_ref',g.REUSABLE+'@'+self.policy.reusable_sha),
            ('environment_node_id','wrong-id'),('workflow_sha','7'*40),
            ('aud','wrong-purpose'),('nbf',1399),('run_attempt','2'),
        ):
            with self.subTest(claim=claim),self.assertRaises(ValueError):
                g.receive(self.write(frame|{'token':self.token(nonce,**{claim:replacement})},b'payload'),
                          self.policy,self.ledger,write,keys=lambda:self.jwks,clock=lambda:2000)
        self.assertEqual(staged,[])
        token=self.token(nonce)
        result=g.receive(self.write(frame|{'token':token},b'payload'),
                         self.policy,self.ledger,write,keys=lambda:self.jwks,clock=lambda:2000)
        self.assertEqual(result,{'status':'ok','environment':'test','revision':'b'*40,'sha256':'c'*64})
        self.assertEqual(staged,[('b'*40,'c'*64,b'payload')])
        with self.assertRaises(ValueError):
            g.receive(self.write(frame|{'token':token},b'payload'),
                      self.policy,self.ledger,write,keys=lambda:self.jwks,clock=lambda:2000)

    def test_malformed_old_dialect_never_reaches_writer(self):
        for payload in (b'junk',b'{"op":"production"}\n',b'X'*22000):
            with self.assertRaises((ValueError,TypeError)):
                g.receive(io.BytesIO(payload),self.policy,self.ledger,lambda *a:self.fail('writer reached'))


class ProviderEntry(TestCase):
    def test_signed_controller_order_and_failed_scope_no_transport(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        import configured_base_test_provider as p
        from configured_provider_checks import Content, Control, Original
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            wire=b'controlled-payload'; (directory/'release.tar.gz').write_bytes(wire)
            ctx=Control('test','11','a'*40,'assertion-memory-v0.1.1',p.BASE)
            foreign=Control('prod','11','a'*40,'assertion-memory-v0.1.1',p.BASE)
            release=Original(ctx.tag,'b'*40,'12','13','14','c'*64)
            content=Content(hashlib.sha256(wire).hexdigest())
            env={'CONFIGURED_BASE_TEST_READY':'true','CONFIGURED_BASE_REUSABLE_SHA':'a'*40,
                 'CONFIGURED_BASE_ENV_NODE_ID':'EN_SAFE','CONFIGURED_BASE_VM_ID':'fiction'}
            receipt={'status':'ok','environment':'test','revision':release.revision,'sha256':content.sha256}
            def exchange(argv,packet,payload):
                self.assertEqual(argv,['synthetic'])
                self.assertEqual(packet['intent']['run'],'11')
                if packet['op']=='challenge':
                    self.assertIsNone(payload)
                    return {'status':'ok','nonce':'e'*64}
                self.assertEqual(packet['token'],'synthetic-token')
                self.assertEqual(payload.read(),wire)
                return receipt
            with patch.object(p,'route',return_value=nullcontext(['synthetic'])) as route, \
                 patch.object(p.ssh_transport,'preflight') as preflight, \
                 patch.object(p,'exchange',side_effect=exchange) as ssh, \
                 patch.object(p,'mint',return_value='synthetic-token') as mint:
                with self.assertRaises(ValueError):p.activate(foreign,release,content,directory,None,env)
                with self.assertRaises(ValueError):p.activate(ctx,release,Content('0'*64),directory,None,env)
                route.assert_not_called()
                self.assertEqual(p.activate(ctx,release,content,directory,None,env),receipt)
                p.smoke(ctx,release,content,directory,env)
                route.assert_called_once()
                preflight.assert_called_once_with(['synthetic'])
                self.assertEqual(ssh.call_count,2)
                self.assertTrue(mint.call_args.args[0].startswith('urn:assertion-memory:configured:base:test:v1:'))

if __name__=='__main__':main()
