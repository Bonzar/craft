"""Offline adversarial checks for immutable, provenance-bound TEST promotion."""
import hashlib
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import lifecycle as lc

SHA = 'a' * 40
PAYLOAD = b'fictional-release'
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
IDENTITY = dict(release_id='42', artifact_id='43', zip_sha256='b'*64,
                build_run_id='44', build_attempt='1', revision='c'*40, sha256=DIGEST)


class PromotionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.receipt = {**IDENTITY, **lc.receipt_context('51', SHA)}
        self.run = dict(id=51, workflow_id=45, path='.github/workflows/'+lc.WORKFLOW,
                        event='workflow_dispatch', head_branch='main', head_sha=SHA,
                        run_attempt=1, status='completed', conclusion='success',
                        repository={'full_name':lc.ci.REPO}, head_repository={'full_name':lc.ci.REPO})
        self.job = dict(name='Deploy TEST', run_id=51, head_sha=SHA,
                        status='completed', conclusion='success',
                        steps=[dict(name=name, status='completed', conclusion='success')
                               for name in (lc.SEND_STEP, lc.UPLOAD_STEP)])
        self.artifact = dict(id=52, name=lc.receipt_name('51'), expired=False,
                             size_in_bytes=1000, workflow_run=dict(id=51, head_sha=SHA))
        self.data = {
            f'{lc.ROOT}/actions/runs/51':self.run,
            f'{lc.ROOT}/actions/workflows/{lc.WORKFLOW}':{'id':45},
            f'{lc.ROOT}/actions/runs/51/attempts/1/jobs?per_page=100':{'total_count':1, 'jobs':[self.job]},
            f'{lc.ROOT}/actions/runs/51/artifacts?per_page=100':{'total_count':1, 'artifacts':[self.artifact]},
        }
        self.make_zip()

    def make_zip(self, members=None):
        source = self.path/'source.zip'
        with zipfile.ZipFile(source, 'w') as z:
            for name, content in (members or {'test-receipt.json':json.dumps(self.receipt)}).items():
                z.writestr(name, content)
        self.zip_bytes = source.read_bytes()
        self.artifact['digest'] = 'sha256:'+hashlib.sha256(self.zip_bytes).hexdigest()

    def get(self, route, dest=None):
        if dest is not None:
            self.assertEqual(route, f'{lc.ROOT}/actions/artifacts/52/zip')
            dest.write_bytes(self.zip_bytes)
        else:
            return self.data[route]

    def read(self):
        return lc.read_test_receipt(self.get, '51', SHA, IDENTITY, self.path)

    def test_green_receipt_binds_original_build_and_two_digests(self):
        self.assertEqual(self.read(), self.receipt)

    def test_rejects_wrong_run_workflow_repo_ref_attempt_and_conclusion(self):
        for key, bad in dict(id=99, workflow_id=99, path='.github/workflows/other.yml',
                             event='push', head_branch='feature', head_sha='d'*40,
                             run_attempt=2, status='in_progress', conclusion='failure',
                             repository={'full_name':'other/craft'},
                             head_repository={'full_name':'other/craft'}).items():
            with self.subTest(key=key):
                old=self.run[key]
                self.run[key]=bad
                with self.assertRaises(ValueError): self.read()
                self.run[key]=old

    def test_rejects_wrong_failed_or_missing_test_job_and_incomplete_pagination(self):
        for key,bad in dict(name='Deploy PROD', run_id=99, head_sha='d'*40,
                            status='in_progress', conclusion='skipped', steps=[]).items():
            with self.subTest(key=key):
                old=self.job[key]
                self.job[key]=bad
                with self.assertRaises(ValueError): self.read()
                self.job[key]=old
        jobs=self.data[f'{lc.ROOT}/actions/runs/51/attempts/1/jobs?per_page=100']
        jobs['total_count']=101
        with self.assertRaises(ValueError): self.read()

    def test_requires_successful_send_and_upload_steps(self):
        for step in self.job['steps']:
            for key,bad in [('conclusion','failure'),('conclusion','skipped'),('status','in_progress')]:
                old=step[key]
                step[key]=bad
                with self.assertRaises(ValueError): self.read()
                step[key]=old

    def test_no_receipt_expired_duplicate_or_foreign_artifact_fails_closed(self):
        for key,bad in dict(name='wrong', expired=True, size_in_bytes=20000,
                            workflow_run={'id':99,'head_sha':SHA}).items():
            old=self.artifact[key]
            self.artifact[key]=bad
            with self.assertRaises(ValueError): self.read()
            self.artifact[key]=old
        artifacts=self.data[f'{lc.ROOT}/actions/runs/51/artifacts?per_page=100']
        for count in [0,2,101]:
            artifacts['total_count']=count
            with self.assertRaises(ValueError): self.read()

    def test_every_receipt_identity_field_and_environment_is_bound(self):
        for key in self.receipt:
            with self.subTest(key=key):
                old=self.receipt[key]
                self.receipt[key]='wrong'
                self.make_zip()
                with self.assertRaisesRegex(ValueError,'TEST receipt'): self.read()
                self.receipt[key]=old

    def test_tampered_zip_extra_paths_and_large_json_rejected(self):
        self.artifact['digest']='sha256:'+'0'*64
        with self.assertRaisesRegex(ValueError,'ZIP digest'): self.read()
        for members in [{'../test-receipt.json':'{}'}, {'test-receipt.json':'{}','extra':'x'},
                        {'test-receipt.json':'x'*8193}]:
            self.make_zip(members)
            with self.assertRaisesRegex(ValueError,'ZIP contents'): self.read()

    def test_receipt_only_after_exact_receiver_success_and_unchanged_payload(self):
        env=dict(GITHUB_REPOSITORY=lc.ci.REPO, GITHUB_RUN_ATTEMPT='1',
                 GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                 GITHUB_WORKFLOW_REF=lc.WORKFLOW_REF, GITHUB_SHA=SHA,
                 GITHUB_JOB='deploy-test', GITHUB_RUN_ID='51', RELEASE_DOWNLOAD_DIR=str(self.path))
        (self.path/'identity.json').write_text(json.dumps(IDENTITY))
        (self.path/'release.tar.gz').write_bytes(PAYLOAD)
        receiver=dict(status='ok', environment='test', revision=IDENTITY['revision'], sha256=DIGEST)
        (self.path/'receiver-receipt.json').write_text(json.dumps(receiver))
        lc.write_test_receipt(env)
        self.assertEqual(json.loads((self.path/'test-receipt.json').read_bytes()), self.receipt)
        for key,bad in dict(GITHUB_REPOSITORY='other/craft', GITHUB_RUN_ATTEMPT='2',
                            GITHUB_EVENT_NAME='push', GITHUB_REF='refs/tags/v1',
                            GITHUB_WORKFLOW_REF='wrong', GITHUB_JOB='deploy-production').items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                lc.write_test_receipt({**env,key:bad})
        for key,bad in dict(status='failed', environment='production', revision='d'*40,sha256='e'*64).items():
            (self.path/'receiver-receipt.json').write_text(json.dumps({**receiver,key:bad}))
            with self.assertRaises(ValueError): lc.write_test_receipt(env)
        (self.path/'receiver-receipt.json').write_text(json.dumps(receiver))
        (self.path/'release.tar.gz').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'changed'): lc.write_test_receipt(env)


    def test_production_resolves_original_bytes_and_exports_only_verified_test_outputs(self):
        """Exercise the production entrypoint; no network, receiver or rebuild."""
        payload_dir=self.path/'payload'
        github_output=self.path/'outputs'
        build_zip=self.path/'build.zip'
        name=f"assertion-memory-qa-{IDENTITY['revision']}.tar.gz"
        with zipfile.ZipFile(build_zip,'w') as z:
            z.writestr(name,PAYLOAD)
            z.writestr(name+'.sha256',f'{DIGEST}  {name}\n')
        build_bytes=build_zip.read_bytes()
        build_identity={key:IDENTITY[key] for key in ('artifact_id','build_run_id','build_attempt')}
        build_identity['zip_sha256']=hashlib.sha256(build_bytes).hexdigest()
        self.receipt.update(build_identity)
        self.make_zip()
        env=dict(GITHUB_REPOSITORY=lc.ci.REPO,GITHUB_RUN_ATTEMPT='1',
                 GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/main',
                 GITHUB_WORKFLOW_REF=lc.WORKFLOW_REF,GITHUB_SHA=SHA,
                 GITHUB_JOB='deploy-production',GITHUB_RUN_ID='61',RELEASE_ID='42',
                 TEST_RUN_ID='51',RELEASE_DOWNLOAD_DIR=str(payload_dir),GITHUB_OUTPUT=str(github_output))
        def api(route,dest=None):
            if route.endswith('/43/zip'):
                dest.write_bytes(build_bytes)
                return
            return self.get(route,dest)
        with patch.dict(os.environ,env,clear=True), patch.object(sys,'argv',['lifecycle.py','production']), \
                patch.object(lc,'identify',return_value=('assertion-memory-v1.2.3',IDENTITY['revision'])), \
                patch.object(lc,'build_identity',return_value=build_identity), patch.object(lc.ci,'api',side_effect=api), \
                patch.object(lc,'authorize_production',return_value='77'):
            lc.main()
        self.assertEqual((payload_dir/'release.tar.gz').read_bytes(),PAYLOAD)
        outputs=github_output.read_text()
        self.assertIn('tested_artifact_id=43\n',outputs)
        self.assertIn('tested_sha256='+DIGEST+'\n',outputs)
        github_output.unlink()


class ProductionAuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        event=Path(self.tmp.name)/'event.json'
        event.write_text(json.dumps({'inputs':{'action':'production','release_id':'42','test_run_id':'51'}}))
        self.env=dict(GITHUB_REPOSITORY=lc.ci.REPO,GITHUB_RUN_ATTEMPT='1',
                      GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/main',
                      GITHUB_REF_PROTECTED='true',GITHUB_WORKFLOW_REF=lc.WORKFLOW_REF,GITHUB_SHA=SHA,
                      GITHUB_JOB='deploy-production',GITHUB_RUN_ID='61',GITHUB_ACTOR_ID='77',
                      RELEASE_ID='42',TEST_RUN_ID='51',PRODUCTION_READY='true',GITHUB_EVENT_PATH=str(event))
        self.run=dict(id=61,actor={'id':77},triggering_actor={'id':77},event='workflow_dispatch',
                      run_attempt=1,head_branch='main',head_sha=SHA,path='.github/workflows/'+lc.WORKFLOW,
                      repository={'full_name':lc.ci.REPO},head_repository={'full_name':lc.ci.REPO})
        self.config=dict(name='ASSERTION_MEMORY_PROD_ACTOR_IDS',value='77,88')
        self.environment=dict(name='assertion-memory-production',deployment_branch_policy={
            'protected_branches':False,'custom_branch_policies':True})
        self.policies=dict(total_count=1,branch_policies=[{'name':'main','type':'branch'}])
        base=lc.ROOT+'/environments/assertion-memory-production'
        self.data={lc.ROOT+'/actions/runs/61':self.run,base:self.environment,
                   base+'/deployment-branch-policies?per_page=100':self.policies,
                   base+'/variables/ASSERTION_MEMORY_PROD_ACTOR_IDS':self.config}

    def auth(self):
        return lc.authorize_production(self.data.__getitem__,self.env)

    def test_permitted_authenticated_actor_without_hardcoded_owner(self):
        self.assertEqual(self.auth(),'77')

    def test_missing_malformed_duplicate_or_unknown_actor_allowlist(self):
        for value in ['', ' ', '0', '77,', '77, 88', '77,77', '077', '*', '99', '77\n']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.config['value']=value
                self.auth()
        self.config.clear()
        with self.assertRaises(ValueError): self.auth()

    def test_disabled_prod_independent_of_test_readiness(self):
        for value in ['', 'false', 'TRUE']:
            self.env.update(PRODUCTION_READY=value,ASSERTION_MEMORY_BASE_DEPLOY_READY='true')
            with patch.object(lc.ci,'api') as get,self.assertRaisesRegex(ValueError,'readiness'):
                lc.authorize_production(get,self.env)
            get.assert_not_called()

    def test_invalid_actor_dispatch_attempt_ref_or_branch_protection(self):
        for key,value in dict(GITHUB_ACTOR_ID='',GITHUB_REF='refs/heads/other',
                              GITHUB_REF_PROTECTED='false',GITHUB_RUN_ATTEMPT='2',
                              GITHUB_EVENT_NAME='push',GITHUB_JOB='deploy-test').items():
            with self.subTest(key=key),self.assertRaises(ValueError):
                lc.authorize_production(self.data.__getitem__,{**self.env,key:value})
        for key,value in dict(actor={'id':99},triggering_actor={'id':99},head_sha='b'*40,
                              event='push',run_attempt=2,head_branch='other').items():
            old=self.run[key]; self.run[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): self.auth()
            self.run[key]=old
        Path(self.env['GITHUB_EVENT_PATH']).write_text(json.dumps({'inputs':{'action':'test'}}))
        with self.assertRaisesRegex(ValueError,'explicit'): self.auth()

    def test_missing_environment_read_access_and_wrong_branch_policy_fail_closed(self):
        with self.assertRaises(PermissionError):
            lc.authorize_production(lambda path: (_ for _ in ()).throw(PermissionError('Forbidden')),self.env)
        for policy in [None,{'protected_branches':True,'custom_branch_policies':False}]:
            self.environment['deployment_branch_policy']=policy
            with self.assertRaises(ValueError): self.auth()
        self.environment['deployment_branch_policy']={'protected_branches':False,'custom_branch_policies':True}
        self.policies['branch_policies'][0]['type']='tag'
        with self.assertRaises(ValueError): self.auth()

    def test_readiness_and_caller_flags_cannot_satisfy_missing_host_proof(self):
        with patch.dict(os.environ,PRODUCTION_READY='true',HOST_PROOF='true'), \
                self.assertRaisesRegex(ValueError,'host proof bridge is not integrated'):
            lc.require_host_proof()


    def test_prod_workflow_calls_only_the_audited_pin(self):
        workflow=(Path(__file__).resolve().parents[2]/'.github/workflows/assertion-memory-base.yml').read_text()
        prod=workflow.split('  deploy-production:',1)[1]
        self.assertIn("github.event_name == 'workflow_dispatch'",prod)
        self.assertIn("inputs.action == 'production'",prod)
        self.assertIn("github.run_attempt == '1'",prod)
        self.assertIn('github.ref_protected',prod)
        self.assertIn("vars.ASSERTION_MEMORY_PROD_BRIDGE_CALLER_READY == 'true'",prod)
        self.assertIn('id-token: write',prod)
        self.assertIn('uses: Bonzar/craft/.github/workflows/assertion-memory-authorize-promotion.yml@6a0286c846bf6e7448d88be57db881d9460d7f1d',prod)
        for forbidden in ('secrets.', 'send-release.sh', 'SSH_PRIVATE_KEY', 'secrets:', 'steps:'):
            self.assertNotIn(forbidden,prod)


if __name__ == '__main__':
    unittest.main()
