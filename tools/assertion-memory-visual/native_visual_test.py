"""Native visual workflow rejects substitution before its transport step."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import native_visual as visual


class VisualGate(unittest.TestCase):
    def fixture(self, folder):
        tag, revision, tar = 'assertion-memory-visual-v1.0.0', 'a'*40, b'exact visual payload'
        payload_sha, manifest = hashlib.sha256(tar).hexdigest(), 'b'*64
        graph = dict(TAG=tag, REVISION=revision, ARTIFACT_ID='3', BUILD_RUN_ID='4', RELEASE_ID='5')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('visual.tar', tar)
            z.writestr('receipt.json', json.dumps(dict(source_commit=revision,
                archive_sha256=payload_sha, archive_bytes=len(tar), release_digest=manifest)))
        archive = buf.getvalue()
        digest = hashlib.sha256(archive).hexdigest()
        graph.update(ARCHIVE_SHA=digest, VERIFIED_SHA=digest)
        event = folder/'event.json'
        event.write_text(json.dumps(dict(inputs=dict(tag=tag, deploy_visual='true'))))
        env = dict(GITHUB_REPOSITORY=visual.REPO, GITHUB_WORKFLOW_REF=visual.WORKFLOW_REF,
                   GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                   GITHUB_REF_PROTECTED='true', GITHUB_RUN_ATTEMPT='1', GITHUB_JOB='deploy',
                   GITHUB_SHA='d'*40, GITHUB_RUN_ID='10', GITHUB_EVENT_PATH=str(event),
                   NATIVE_VISUAL_READY='true', NATIVE_VISUAL_ENV_ID='20',
                   NATIVE_VISUAL_REVIEWER_ID='30', VISUAL_ENV='test',
                   VISUAL_DOWNLOAD=str(folder/'test-download'))
        env.update({'GRAPH_'+k:v for k,v in graph.items()})
        proof = dict(revision=revision, sha256=payload_sha, release=manifest,
                     artifact_id='3', zip_sha256=digest, release_id='5', build_run_id='4',
                     tag=tag, test_run_id='10', workflow_sha='d'*40, attempt='1')
        return env, (tag, revision), ('3', digest, '4'), archive, proof

    def runner(self, env, identity, artifact, archive, proof=None, *, uploaded_proof=None):
        name = 'assertion-memory-native-visual-' + env['VISUAL_ENV']
        pre = visual.lifecycle.ROOT + '/environments/' + name
        records = {
            pre: dict(name=name, id=20, deployment_branch_policy={
                'protected_branches':False, 'custom_branch_policies':True},
                protection_rules=[dict(type='required_reviewers', reviewers=[
                    dict(type='User', reviewer={'id':30})])]),
            visual.lifecycle.ROOT+'/branches/main': {'protected':True},
            pre+'/deployment-branch-policies?per_page=2': {
                'total_count':1, 'branch_policies':[dict(type='branch', name='main')]},
            visual.lifecycle.ROOT+'/actions/runs/10/approvals': [dict(state='approved', user={
                'type':'User','id':30}, environments=[dict(name=name,id=20)])]}
        base = visual.lifecycle.ROOT
        run_path = base+'/actions/runs/10'
        if proof is not None:
            evidence = io.BytesIO()
            with zipfile.ZipFile(evidence, 'w') as out:
                out.writestr(visual.RECEIPT_FILE, json.dumps(
                    proof if uploaded_proof is None else uploaded_proof))
            zip_bytes = evidence.getvalue()
            records[base+'/actions/workflows/'+visual.WORKFLOW_FILE] = {'id': 41}
            records[run_path] = dict(id=10, workflow_id=41, event=env['GITHUB_EVENT_NAME'],
                path='.github/workflows/'+visual.WORKFLOW_FILE, head_branch='main',
                head_sha='d'*40, run_attempt=1, status='in_progress',
                repository={'full_name':visual.REPO}, head_repository={'full_name':visual.REPO})
            records[run_path+'/attempts/1/jobs?per_page=100'] = {'total_count':1, 'jobs':[
                dict(name=visual.TEST_JOB, run_id=10, head_sha='d'*40, status='completed',
                     conclusion='success', steps=[dict(name=name, status='completed',
                         conclusion='success') for name in visual.TEST_STEPS])]}
            records[run_path+'/artifacts?per_page=100'] = {'total_count':1, 'artifacts':[
                dict(name='assertion-memory-native-visual-test-10-attempt-1', expired=False,
                     id=50, workflow_run={'id':10,'head_sha':'d'*40}, size_in_bytes=len(zip_bytes),
                     digest='sha256:'+hashlib.sha256(zip_bytes).hexdigest())]}
        def get(path, dst=None):
            if dst:
                if path == base+'/actions/artifacts/3/zip':
                    dst.write_bytes(archive)
                else:
                    self.assertEqual(path, base+'/actions/artifacts/50/zip')
                    dst.write_bytes(zip_bytes)
            else:
                return records[path]
        return records, get

    def test_exact_original_and_same_run_proof(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env, idn, artifact, archive, proof = self.fixture(root)
            _, get = self.runner(env, idn, artifact, archive)
            with patch.object(visual.lifecycle,'identify',return_value=idn), \
                 patch.object(visual.lifecycle,'build_identity',return_value=artifact):
                self.assertEqual(visual.resolve(get,env), proof)
                env.update(VISUAL_ENV='prod', VISUAL_DOWNLOAD=str(root/'prod-download'),
                           NATIVE_VISUAL_TEST_PROOF=json.dumps(proof))
                _, get = self.runner(env, idn, artifact, archive, proof)
                self.assertEqual(visual.resolve(get,env), proof)
                for n, key in enumerate(proof):
                    with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'TEST-proven'):
                        visual.resolve(get, dict(env, VISUAL_DOWNLOAD=str(root/f'different-{n}'),
                            NATIVE_VISUAL_TEST_PROOF=json.dumps(dict(proof, **{key:'other'}))))

    def test_github_release_completion_uses_only_first_attempt_original(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env, idn, artifact, archive, proof = self.fixture(root)
            env['GITHUB_EVENT_NAME'] = 'workflow_run'
            event = Path(env['GITHUB_EVENT_PATH'])
            run = dict(id=4, head_branch=idn[0], head_sha=idn[1],
                       path='.github/workflows/'+visual.lifecycle.WORKFLOW,
                       event='release', status='completed', conclusion='success',
                       run_attempt=1, repository={'full_name':visual.REPO},
                       head_repository={'full_name':visual.REPO})
            event.write_text(json.dumps(dict(action='completed', workflow_run=run)))
            _, get = self.runner(env, idn, artifact, archive)
            with patch.object(visual.lifecycle,'identify',return_value=idn), \
                 patch.object(visual.lifecycle,'build_identity',return_value=artifact):
                self.assertEqual(visual.resolve(get,env), proof)
                env.update(VISUAL_ENV='prod', VISUAL_DOWNLOAD=str(root/'auto-prod'),
                           NATIVE_VISUAL_TEST_PROOF=json.dumps(proof))
                _, get = self.runner(env, idn, artifact, archive, proof)
                self.assertEqual(visual.resolve(get,env), proof)
                failures = [
                    ('id', 5, 'canonical original build'),
                    ('head_sha', '6'*40, 'canonical original build'),
                    ('run_attempt', 2, 'original successful visual Release build event'),
                    ('head_branch', 'assertion-memory-v1.0.0', 'original successful visual Release build event'),
                    ('event', 'pull_request', 'original successful visual Release build event'),
                    ('path', '.github/workflows/assertion-memory-base.yml', 'original successful visual Release build event'),
                    ('head_repository', {'full_name':'other/craft'}, 'original successful visual Release build event'),
                ]
                for key, val, reason in failures:
                    event.write_text(json.dumps(dict(action='completed', workflow_run=dict(run, **{key:val}))))
                    with self.subTest(key=key), self.assertRaisesRegex(ValueError, reason):
                        visual.resolve(get,env)

    def test_prod_rejects_caller_proof_without_independent_verified_test(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env, idn, artifact, archive, proof = self.fixture(root)
            env.update(VISUAL_ENV='prod', NATIVE_VISUAL_TEST_PROOF=json.dumps(proof))
            records, get = self.runner(env, idn, artifact, archive, proof)
            src = visual.lifecycle.ROOT+'/actions/runs/10'
            jobs = records[src+'/attempts/1/jobs?per_page=100']
            saved = records[src+'/artifacts?per_page=100']
            rec = saved['artifacts'][0]
            steps = {step['name']: step for step in jobs['jobs'][0]['steps']}
            with patch.object(visual.lifecycle,'identify',return_value=idn), \
                 patch.object(visual.lifecycle,'build_identity',return_value=artifact):
                failures = [
                    (records[src], 'run_attempt', 2, 'control run or attempt'),
                    (records[src], 'head_sha', '1'*40, 'control run or attempt'),
                    (jobs['jobs'][0], 'name', 'Promote exact visual TEST to PROD', 'TEST job'),
                    (jobs['jobs'][0], 'conclusion', 'failure', 'TEST job'),
                    (steps['Smoke exact visual ingress'], 'conclusion', 'skipped', 'did not verify'),
                    (steps['Preserve visual TEST evidence'], 'conclusion', 'failure', 'did not verify'),
                    (rec['workflow_run'], 'id', 99, 'evidence origin'),
                    (rec['workflow_run'], 'head_sha', '1'*40, 'evidence origin'),
                    (saved, 'total_count', 2, 'Incomplete'),
                    (rec, 'digest', 'sha256:'+'1'*64, 'ZIP digest mismatch'),
                ]
                for i, (obj, key, val, message) in enumerate(failures):
                    old = obj[key]
                    obj[key] = val
                    with self.subTest(key=key, value=val), self.assertRaisesRegex(ValueError, message):
                        visual.resolve(get, dict(env, VISUAL_DOWNLOAD=str(root/f'failure-{i}')))
                    obj[key] = old
                _, get = self.runner(env, idn, artifact, archive, proof,
                                     uploaded_proof=dict(proof, revision='1'*40))
                with self.assertRaisesRegex(ValueError, 'Uploaded native visual TEST evidence'):
                    visual.resolve(get, dict(env,VISUAL_DOWNLOAD=str(root/'wrong-evidence')))

    def test_no_dispatch_authority_without_exact_intent_environment_or_approval(self):
        with tempfile.TemporaryDirectory() as td:
            env,idn,artifact,archive,_ = self.fixture(Path(td))
            records, get = self.runner(env, idn, artifact, archive)
            with patch.object(visual.lifecycle,'identify') as identify:
                for key in ('GITHUB_WORKFLOW_REF','GITHUB_REF_PROTECTED','GITHUB_RUN_ATTEMPT',
                            'GITHUB_JOB','NATIVE_VISUAL_READY','GRAPH_TAG'):
                    with self.subTest(env=key), self.assertRaises(ValueError):
                        visual.resolve(get,dict(env, **{key:'wrong'}))
                history = records[visual.lifecycle.ROOT+'/actions/runs/10/approvals']
                history[0]['environments'][0]['name'] = 'assertion-memory-native-visual-prod'
                with self.assertRaisesRegex(ValueError,'No exact authorized'):
                    visual.resolve(get,env)
                # Even a correct environment cannot turn a preview dispatch into a deploy.
                Path(env['GITHUB_EVENT_PATH']).write_text(json.dumps(
                    {'inputs':{'tag':env['GRAPH_TAG'],'deploy_visual':'false'}}))
                with self.assertRaisesRegex(ValueError,'exact existing-tag intent'):
                    visual.resolve(get,env)
                identify.assert_not_called()

    def test_credentials_missing_or_invalid_are_rejected_without_values(self):
        keys = ('VISUAL_HOST_PIN', 'VISUAL_SSH_KEY', 'VISUAL_SIGNING_KEY', 'VISUAL_GATEWAY_TOKEN')
        sample = {key: 'a'*64 for key in keys}
        visual.require_credentials(sample)
        for key in keys:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Missing scoped visual credential: '+key):
                visual.require_credentials(dict(sample, **{key:' '}))
        with self.assertRaisesRegex(ValueError, 'Invalid release identifier'):
            visual.require_credentials(dict(sample, VISUAL_GATEWAY_TOKEN='not-hex'))

    def test_reusable_stage_declares_and_both_callers_map_exact_secret_names(self):
        root = Path(__file__).resolve().parents[2]/'.github/workflows'
        called = (root/'assertion-memory-native-visual.yml').read_text()
        graph = (root/'assertion-memory-release-graph.yml').read_text()
        expected = tuple('ASSERTION_MEMORY_NATIVE_VISUAL_'+x for x in
                         ('HOST_PIN','SSH_KEY','SIGNING_KEY','GATEWAY_TOKEN'))
        declared = called.split('    secrets:\n', 1)[1].split('    inputs:\n', 1)[0]
        self.assertEqual(declared.splitlines(),
                         ['      '+name+': {required: true}' for name in expected])
        for job in ('native-visual-test','native-visual-prod'):
            chunk = graph.split('  '+job+':\n', 1)[1]
            mapped = chunk.split('    secrets:\n', 1)[1].split('    with:\n', 1)[0]
            self.assertEqual(mapped.splitlines(),
                             ['      '+name+': ${{ secrets.'+name+' }}' for name in expected])
        self.assertLess(called.index('Verify exact immutable visual archive and stage approval'),
                        called.index('Verify scoped visual credential presence'))
        self.assertLess(called.index('Verify scoped visual credential presence'),
                        called.index('Activate exact visual archive', called.index('steps:')))
        self.assertIn('environment: assertion-memory-native-visual-${{ inputs.stage }}', called)

    def test_graph_or_zip_substitution_fails(self):
        with tempfile.TemporaryDirectory() as td:
            env,idn,artifact,archive,_ = self.fixture(Path(td))
            _, get = self.runner(env, idn, artifact, archive + b'X')
            with patch.object(visual.lifecycle,'identify',return_value=idn), \
                 patch.object(visual.lifecycle,'build_identity',return_value=artifact):
                for key in ('GRAPH_RELEASE_ID','GRAPH_BUILD_RUN_ID','GRAPH_REVISION',
                            'GRAPH_ARTIFACT_ID','GRAPH_ARCHIVE_SHA','GRAPH_VERIFIED_SHA'):
                    if key == 'GRAPH_RELEASE_ID':
                        # The immutable release resolver binds this numeric identity to its API resource.
                        continue
                    with self.subTest(key=key), self.assertRaisesRegex(ValueError,'verified release graph'):
                        visual.resolve(get,dict(env, **{key:'9'}))
                with self.assertRaisesRegex(ValueError,'ZIP digest mismatch'):
                    visual.resolve(get,env)

if __name__ == '__main__':
    unittest.main()
