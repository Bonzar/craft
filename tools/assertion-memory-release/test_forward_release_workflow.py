import dataclasses
from pathlib import Path
import tempfile
import unittest

from forward_release_train import Candidate, Reject
from forward_release_workflow import check, verified_github_claim


class WorkflowCheckpoint(unittest.TestCase):
    def setUp(self):
        c=self.c=Candidate('base','assertion-memory-v1.0.0','a'*40)
        self.w=dict(schema='forward-release-stage/v1',repository_id=1260097465,authority_revision='b'*40,
                    family='base',stage='build',tag=c.tag,revision=c.revision,release_id=12,workflow_run_id=44,payload_sha256=None,test_generation=None)

    def accept(self, witness, c=None, stage='build', authority='b'*40):
        return check(c or self.c,stage,12,44,authority,'reference',verify_signed=lambda *_:witness)

    def test_designated_workflow_proof_only(self):
        self.assertEqual(self.accept(self.w),'forward-admitted')
        for w in [None,self.w|{'workflow_run_id':45},self.w|{'release_id':13},
                  self.w|{'revision':'d'*40},self.w|{'family':'visual'},self.w|{'stage':'test'}]:
            with self.subTest(w=w), self.assertRaises(Reject):
                self.accept(w)
        with self.assertRaises(Reject):
            self.accept(self.w,authority=None)
        self.assertEqual(self.accept(None,dataclasses.replace(self.c,tag='assertion-memory-v0.1.36')),
                         'legacy-controller')

    def test_fail_closed_unstructured_host_response(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'subject.json'
            p.write_bytes(b'{}')
            with self.assertRaises(Reject):
                verified_github_claim(p,'b'*40,execute=lambda _:b'[]',authenticated_metadata=lambda *_:True)
            with self.assertRaises(Reject):
                verified_github_claim(p,'b'*40,execute=lambda _:self.fail('Must not call'),authenticated_metadata=lambda *_:False)

    def test_prod_proof_binds_payload_and_test_generation(self):
        receipt = self.w | dict(stage='prod',payload_sha256='d'*64,test_generation='e'*64)
        def run(payload,generation):
            return check(self.c,'prod',12,44,'b'*40,'proof',verify_signed=lambda *_:receipt,
                         payload_sha256=payload,test_generation=generation)
        self.assertEqual(run('d'*64,'e'*64),'forward-admitted')
        with self.assertRaises(Reject):
            run('d'*64,'f'*64)
        with self.assertRaises(Reject):
            run('d'*64,None)
