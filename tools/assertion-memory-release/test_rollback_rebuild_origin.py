"""Offline hostile-provider cases; signatures/runner wiring need separate tests."""
import unittest
from dataclasses import replace

import github_graph_preview as gh
from rollback_rebuild_origin import Policy, SIGNER, WORKFLOW, rebuild_origin


class GateTest(unittest.TestCase):
    def setUp(self):
        self.old='b'*40
        self.builder='a'*40
        self.zipsha='c'*64
        self.tag='assertion-memory-v0.1.25'
        self.policy=Policy(99,self.builder,self.tag,self.old,'5','base','base-v1')
        self.data={}
        self.release=dict(id=5,draft=False,immutable=True,published_at='fixture',tag_name=self.tag)
        self.data[gh.ROOT+'/releases/tags/'+self.tag]=self.release
        self.data[gh.ROOT+'/releases/5']=self.release
        self.data[gh.ROOT+'/git/ref/tags/'+self.tag]=dict(ref='refs/tags/'+self.tag,
                                                        object=dict(type='commit',sha=self.old))
        self.compare=dict(status='ahead')
        self.data[gh.ROOT+'/compare/'+self.old+'...main']=self.compare
        self.run=dict(id=81,workflow_id=99,path=WORKFLOW,head_branch='main',head_sha=self.builder,
                      event='workflow_dispatch',run_attempt=1,status='completed',conclusion='success',
                      repository=dict(id=gh.REPO_ID),head_repository=dict(id=gh.REPO_ID))
        self.data[gh.ROOT+'/actions/runs/81']=self.run
        name='rollback-rebuild-base-5-'+self.old
        self.artifact=dict(id=91,name=name,expired=False,size_in_bytes=16,digest='sha256:'+self.zipsha,
                           workflow_run=dict(id=81,head_sha=self.builder,repository_id=gh.REPO_ID,
                                             head_repository_id=gh.REPO_ID))
        self.index=dict(total_count=1,artifacts=[self.artifact])
        self.data[gh.ROOT+'/actions/runs/81/artifacts?per_page=2&name='+name]=self.index
        self.claim=dict(schema='rollback-rebuild-v1',profile='base',layout='base-v1',tag=self.tag,
                        revision=self.old,release_id='5',control_sha=self.builder,build_run_id='81',
                        build_attempt=1,artifact_id='91',archive_sha=self.zipsha,payload_sha='d'*64)

    def gate(self, *, policy=None, tag=None, verifier=None):
        def verify(*args):
            self.assertEqual(args,('91',self.zipsha,gh.REPO_ID,SIGNER,self.builder))
            return self.claim
        return rebuild_origin(self.data.__getitem__,verifier or verify,policy or self.policy,tag or self.tag,'81')

    def test_rebuild_has_distinct_origin_and_keeps_exact_archive(self):
        result=self.gate()
        self.assertEqual((result.kind,result.revision,result.control_sha,result.archive_sha,result.payload_sha),
                         ('rollback_rebuild',self.old,self.builder,self.zipsha,'d'*64))

    def test_mutable_nonancestor_wrong_profile_not_admitted(self):
        self.release['immutable']=False
        with self.assertRaises(gh.Reject):self.gate()
        self.release['immutable']=True
        self.compare['status']='diverged'
        with self.assertRaises(gh.Reject):self.gate()
        self.compare['status']='ahead'
        with self.assertRaises(gh.Reject):self.gate(policy=replace(self.policy,profile='visual',
                                                                 layout='visual-packed-v1'))

    def test_unauthorized_version_or_different_revision(self):
        with self.assertRaises(gh.Reject):self.gate(tag='assertion-memory-v0.1.24')
        with self.assertRaises(gh.Reject):self.gate(policy=replace(self.policy,revision='e'*40))

    def test_original_workflow_old_source_ref_or_rerun_is_not_a_rebuild(self):
        for key,value in [('event','release'),('head_branch',self.tag),('head_sha',self.old),
                          ('path','.github/workflows/assertion-memory-base.yml'),('run_attempt',2),
                          ('run_attempt',True),('workflow_id',80),('conclusion','failure'),
                          ('repository',dict(id=31)),('head_repository',dict(id=31))]:
            with self.subTest(key=key):
                original=self.run[key]
                self.run[key]=value
                with self.assertRaises(gh.Reject):self.gate()
                self.run[key]=original

    def test_expired_ambiguous_wrong_run_or_unbounded_payload_refused(self):
        self.index['total_count']=2
        with self.assertRaises(gh.Reject):self.gate()
        self.index['total_count']=1
        for key,value in [('expired',True),('size_in_bytes',True),('size_in_bytes',gh.MAX_ARTIFACT+1),
                          ('name','other'),('digest','unknown'),('id',0),
                          ('workflow_run',self.artifact['workflow_run']|{'id':93})]:
            with self.subTest(key=key):
                original=self.artifact[key]
                self.artifact[key]=value
                with self.assertRaises(gh.Reject):self.gate()
                self.artifact[key]=original

    def test_only_preverified_exact_attestation_has_authority(self):
        for value in (None,True,self.claim|{'revision':'e'*40},
                      self.claim|{'artifact_id':'92'},self.claim|{'archive_sha':'f'*64},
                      self.claim|{'build_attempt':True},self.claim|{'payload_sha':'bad'},
                      self.claim|{'notes':'unexpected'}):
            with self.subTest(value=value):
                with self.assertRaises(gh.Reject):self.gate(verifier=lambda *args:value)


if __name__=='__main__':
    unittest.main()
