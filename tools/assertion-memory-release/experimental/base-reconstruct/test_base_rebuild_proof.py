import unittest
from dataclasses import replace
from copy import deepcopy

from base_rebuild_proof import (ROOT, REPOSITORY_ID, WORKFLOW, Proof, Reject,
                                verify_source_selection, verify_published)


class ProofTest(unittest.TestCase):
    control, payload, tag = 'a'*40, 'b'*40, 'assertion-memory-v0.1.16'
    digest = 'sha256:'+'c'*64

    def setUp(self):
        self.p = Proof(self.tag, 1, self.control, self.payload, 2, self.digest)
        self.data = {
            '/branches/main': {'protected': True, 'commit': {'sha': self.control}},
            '/actions/runs/2': {'id': 2, 'workflow_id': 3, 'path': WORKFLOW,
                'head_sha': self.control,'head_branch': 'main', 'event': 'workflow_dispatch',
                'status': 'completed', 'conclusion':'success','run_attempt': 1,
                'head_repository': {'id': REPOSITORY_ID}, 'repository': {'id': REPOSITORY_ID}},
            '/releases/tags/'+self.tag: {'id':1, 'tag_name':self.tag, 'draft':False, 'immutable':True,
                'assets':[{'id':5,'name':'assertion-memory-base-reconstructed.tar.gz',
                           'digest':self.digest,'state':'uploaded'}]},
            '/git/ref/tags/'+self.tag: {'ref':'refs/tags/'+self.tag,
                'object':{'type':'commit','sha':self.payload}},
        }
        for sha in (self.control,self.payload):
            self.data['/commits/'+sha]={'sha':sha}
            self.data['/compare/'+sha+'...main']={'status':'ahead','merge_base_commit':{'sha':sha}}

    def get(self,url):
        return self.data[url.removeprefix(ROOT)]

    def publish(self, verifier=lambda *_: True, proof=None):
        return verify_published(self.get, proof or self.p, workflow_id=3,
                                verify_archive_attestation=verifier)

    def test_old_payload_and_current_control_are_distinct(self):
        self.assertEqual(verify_source_selection(self.get,control_sha=self.control,
                         payload_sha=self.payload,tag=self.tag), self.payload)
        chosen = []
        self.assertEqual(self.publish(lambda *args: chosen.append(args) is None),5)
        self.assertEqual(chosen,[(self.p,3,5)])

    def test_stale_control_cannot_initiate_a_build(self):
        self.data['/branches/main']['commit']['sha']='f'*40
        with self.assertRaises(Reject):
            verify_source_selection(self.get,control_sha=self.control,payload_sha=self.payload,tag=self.tag)
        self.assertEqual(self.publish(),5)

    def test_fails_closed_on_divergence_or_wrong_run(self):
        for path, field, value in [
            ('/compare/'+self.payload+'...main','status','diverged'),
            ('/compare/'+self.payload+'...main','merge_base_commit',{'sha':self.control}),
            ('/actions/runs/2','run_attempt',2),('/actions/runs/2','head_sha',self.payload),
            ('/actions/runs/2','path','.github/workflows/fake.yml'),
            ('/actions/runs/2','head_repository',{'id':9}),
            ('/releases/tags/'+self.tag,'immutable',False),
            ('/git/ref/tags/'+self.tag,'object',{'type':'commit','sha':self.control}),
        ]:
            with self.subTest(field=field):
                before=deepcopy(self.data[path][field])
                self.data[path][field]=value
                with self.assertRaises(Reject):
                    self.publish()
                self.data[path][field]=before

    def test_companion_artifact_cannot_supply_base_proof(self):
        self.data['/releases/tags/'+self.tag]['assets'][0]['name']='sites-bridge.tar.gz'
        with self.assertRaises(Reject):
            self.publish()

    def test_digest_and_independent_attestation_required(self):
        with self.assertRaises(Reject):
            self.publish(proof=replace(self.p, archive_digest='sha256:'+'d'*64))
        with self.assertRaises(Reject):
            self.publish(lambda *_: False)


if __name__ == '__main__':
    unittest.main()
