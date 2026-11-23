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

    def runner(self, env, identity, artifact, archive):
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
        def get(path, dst=None):
            if dst:
                self.assertEqual(path, visual.lifecycle.ROOT+'/actions/artifacts/3/zip')
                dst.write_bytes(archive)
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
                _, get = self.runner(env, idn, artifact, archive)
                self.assertEqual(visual.resolve(get,env), proof)
                for n, key in enumerate(proof):
                    with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'TEST-proven'):
                        visual.resolve(get, dict(env, VISUAL_DOWNLOAD=str(root/f'different-{n}'),
                            NATIVE_VISUAL_TEST_PROOF=json.dumps(dict(proof, **{key:'other'}))))

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
