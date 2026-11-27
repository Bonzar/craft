"""Offline behavioral tests for rebuilding different bytes, testing once, promoting exactly them."""
from dataclasses import replace
import hashlib
import io
import json
import unittest
import zipfile

import github_graph_preview as gh
from rollback_rebuild_origin import RebuiltOrigin
from rollback_rebuild_promotion import (
    DeployControl, MEMBER, STEPS, TEST_JOB, WORKFLOW, ZIP_LIMIT, promote, seal_test,
)


class PromotionTest(unittest.TestCase):
    def setUp(self):
        self.sha='a'*40
        self.origin=RebuiltOrigin('base','base-v1','assertion-memory-v0.1.25','b'*40,
                                  '5','81','c'*40,'75','d'*64,'e'*64)
        self.control=DeployControl(20,self.sha,'85','prod','configured-release-base-test')
        self.receiver=dict(stage='test',profile='base',release_id='5',tag=self.origin.tag,
                           revision=self.origin.revision,archive_sha='d'*64,payload_sha='e'*64,
                           activation='verified',smoke='verified')
        self.expected=seal_test(replace(self.control,stage='test'),self.origin,self.receiver)
        self.output=json.dumps(self.expected)
        self.run=dict(id=85,workflow_id=20,path=WORKFLOW,head_branch='main',head_sha=self.sha,
                      event='workflow_dispatch',run_attempt=1,status='in_progress',
                      repository={'id':gh.REPO_ID},head_repository={'id':gh.REPO_ID})
        self.job=dict(name=TEST_JOB,run_id=85,head_sha=self.sha,status='completed',conclusion='success',
                      steps=[dict(name=name,status='completed',conclusion='success') for name in STEPS])
        self.jobs=dict(total_count=1,jobs=[self.job])
        name='rollback-rebuild-test-85-attempt-1'
        self.artifact=dict(id=76,name=name,expired=False,
                           workflow_run=dict(id=85,head_sha=self.sha,
                                             repository_id=gh.REPO_ID,head_repository_id=gh.REPO_ID))
        self.set_zip(self.output)
        root=gh.ROOT+'/actions/runs/85'
        self.data={root:self.run,root+'/attempts/1/jobs?per_page=100':self.jobs,
                   root+'/artifacts?per_page=2&name='+name:{'total_count':1,'artifacts':[self.artifact]}}

    def set_zip(self,data,member=MEMBER,extra=False):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr(member,data)
            if extra:
                z.writestr('other',data)
        self.raw=out.getvalue()
        self.artifact.update(size_in_bytes=len(self.raw),digest='sha256:'+hashlib.sha256(self.raw).hexdigest())

    def gate(self,origin=None,control=None,output=None):
        def download(aid,limit):
            self.assertEqual((aid,limit),('76',ZIP_LIMIT))
            return self.raw
        return promote(self.data.__getitem__,download,control or self.control,
                       origin or self.origin,self.output if output is None else output)

    def test_rebuilt_digest_passes_only_with_same_run_test(self):
        result=self.gate()
        self.assertEqual(result.kind,'rollback_rebuild_promotion')
        self.assertEqual((result.origin,result.test_receipt_id),(self.origin,'76'))

    def test_no_wrong_stage_or_failed_transport_test_seal(self):
        with self.assertRaises(gh.Reject):
            seal_test(self.control,self.origin,self.receiver)
        c=replace(self.control,stage='test')
        for field,value in (('activation','accepted'),('smoke','failed'),('payload_sha','0'*64),
                            ('revision','f'*40),('stage','prod')):
            with self.subTest(field=field),self.assertRaises(gh.Reject):
                seal_test(c,self.origin,self.receiver|{field:value})

    def test_original_or_different_rebuild_payload_does_not_promote(self):
        for values in (dict(archive_sha='f'*64),dict(payload_sha='f'*64),dict(artifact_id='77'),
                       dict(build_run_id='82'),dict(revision='f'*40),dict(kind='original')):
            with self.subTest(values=values),self.assertRaises(gh.Reject):
                self.gate(origin=replace(self.origin,**values))

    def test_wrong_run_attempt_path_repo_or_ambiguous_job_refused(self):
        for field,value in (('run_attempt',2),('event','workflow_run'),('head_branch','feature'),
                            ('workflow_id',21),('path','other'),('status','waiting'),
                            ('head_repository',{'id':21})):
            old=self.run[field]
            self.run[field]=value
            with self.subTest(field=field),self.assertRaises(gh.Reject):
                self.gate()
            self.run[field]=old
        self.jobs.update(total_count=2,jobs=[self.job,self.job])
        with self.assertRaises(gh.Reject):
            self.gate()

    def test_missing_or_failed_test_step_and_expired_artifact_fail_closed(self):
        for step in self.job['steps']:
            step['conclusion']='failure'
            with self.subTest(step=step['name']),self.assertRaises(gh.Reject):
                self.gate()
            step['conclusion']='success'
        for field,value in (('expired',True),('id',0),('digest','sha256:'+'f'*64),
                            ('workflow_run',self.artifact['workflow_run']|{'id':82})):
            old=self.artifact[field]
            self.artifact[field]=value
            with self.subTest(field=field),self.assertRaises(gh.Reject):
                self.gate()
            self.artifact[field]=old

    def test_rejects_copied_output_bad_zip_and_duplicate_fields(self):
        with self.assertRaises(gh.Reject):
            self.gate(output=self.output.replace('"attempt": 1','"attempt": true'))
        self.set_zip(self.output,extra=True)
        with self.assertRaises(gh.Reject):
            self.gate()
        self.set_zip('{"attempt": 1, '+self.output[1:])
        with self.assertRaises(gh.Reject):
            self.gate()
        self.set_zip(self.output.replace('"run_id": "85"','"run_id": "82"'))
        with self.assertRaises(gh.Reject):
            self.gate()


if __name__ == '__main__':
    unittest.main()
