from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import transaction as t


class RecordingTestRunner:
    def __init__(self, root, request):
        self.root = root
        self.current = t.TEST_ROOT + '/releases/' + request.revision + '-' + request.archive
        self.manifest, self.chain = request.visual_manifest, request.baseline_chain
        self.held = False
        self.events = []
        self.smoke_ok = True

    def path(self, path):
        return self.root / path.removeprefix('/').replace('/', '_')

    def require(self): assert self.held

    @contextmanager
    def exclusive_test_deploy_lease(self):
        assert not self.held
        self.held = True
        try: yield
        finally: self.held = False

    def receipt_status(self, name):
        self.require()
        p = self.root / name
        return p.read_text().split(':')[0] if p.exists() else 'absent'

    def observe(self, p, feature):
        self.require()
        f, r = self.path(p), self.path(t.REGISTRY)
        return t.Observed('test',self.current,self.manifest,self.chain,
                          t.digest(f.read_bytes()) if f.exists() else None,
                          t.digest(r.read_bytes()) if r.exists() and feature=='devices' else None,True)

    def begin(self, name, request):
        self.require()
        with (self.root/name).open('x') as f: f.write('pending:'+request)

    def initialize_absent_registry(self, p):
        self.require()
        assert p==t.REGISTRY
        f=self.path(p)
        with f.open('xb') as h: h.write(b'fictional independent registry')
        return t.digest(f.read_bytes())

    def install_if_absent(self,p,c):
        self.require()
        with self.path(p).open('xb') as f: f.write(c)

    def remove_if_digest(self,p,d):
        self.require()
        assert t.digest(self.path(p).read_bytes())==d
        self.path(p).unlink()

    def restart_exact_test_units(self, units):
        self.require()
        self.events.append(units)

    def read_only_snapshot(self):
        return {p.name:p.read_bytes() for p in self.root.iterdir()},list(self.events)

    def readonly_test_smoke(self,feature,operation):
        self.require()
        return self.smoke_ok

    def finish(self,name):
        self.require()
        assert self.receipt_status(name)=='pending'
        (self.root/name).write_text('complete')


class LocalTransactions(unittest.TestCase):
    def request(self, feature):
        b = t.DEVICE_BYTES if feature=='devices' else b'[Service]\nExecStart=fictional-test-only\n'
        return t.Request('test',feature,'apply','a'*40,'b'*64,'c'*64,'d'*64,
                         'e'*64,None,t.digest(b),None), b

    def verified(self,req):
        return lambda receipt, request_hash: (receipt,request_hash)==(req.receipt_id,t.digest(req.canonical()))

    def no_change(self,runner,req,candidate,error,verify=None):
        before=runner.read_only_snapshot()
        with self.assertRaisesRegex(t.Refused,error):
            t.execute(req,candidate,verify or self.verified(req),runner)
        self.assertEqual(runner.read_only_snapshot(),before)

    def test_graph_and_device_each_apply_replay_inverse(self):
        for feature in ('graph','devices'):
            with self.subTest(feature=feature),TemporaryDirectory() as tmp:
                req,b=self.request(feature)
                runner=RecordingTestRunner(Path(tmp),req)
                before=t.execute(req,b,self.verified(req),runner)
                self.assertEqual(runner.events,[t.FEATURES[feature][1]])
                self.no_change(runner,req,b,'receipt_already_claimed')
                inverse=replace(req,operation='inverse',receipt_id='f'*64,existing_digest=req.candidate_digest,
                                registry_identity=before.registry_identity)
                after=t.execute(inverse,b,self.verified(inverse),runner)
                self.assertIsNone(after.existing_digest)
                self.assertEqual(after.registry_identity,before.registry_identity)
                self.no_change(runner,inverse,b,'receipt_already_claimed')

    def test_stale_wrong_lane_wrong_hash_wrong_review_no_changes(self):
        with TemporaryDirectory() as tmp:
            req,b=self.request('devices')
            runner=RecordingTestRunner(Path(tmp),req)
            self.no_change(runner,replace(req,stage='prod'),b,'invalid_exact_test_request')
            self.no_change(runner,req,b'altered','invalid_exact_test_request')
            self.no_change(runner,req,b,'unauthenticated_exact_test_receipt',lambda *_:False)
            runner.manifest='0'*64
            self.no_change(runner,req,b,'test_state_drift')

    def test_failed_smoke_preserves_pending_no_retry(self):
        with TemporaryDirectory() as tmp:
            req,b=self.request('graph')
            runner=RecordingTestRunner(Path(tmp),req)
            runner.smoke_ok=False
            with self.assertRaisesRegex(t.Refused,'test_smoke_uncertain'):
                t.execute(req,b,self.verified(req),runner)
            self.assertTrue((Path(tmp)/req.receipt_id).read_text().startswith('pending:'))
            self.no_change(runner,req,b,'receipt_already_claimed')
