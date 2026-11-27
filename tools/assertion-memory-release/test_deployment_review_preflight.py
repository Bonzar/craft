import hashlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import deployment_review_preflight as preflight


class ReviewPreflightTest(unittest.TestCase):
    root = '/repos/Bonzar/craft'
    path = root + '/actions/runs/11'
    workflow = '.github/workflows/configured-visual-release.yml'
    revision = 'a' * 40
    control = 'b' * 40
    payload = 'c' * 64
    prod = 'configured-release-visual-prod'
    tag = 'assertion-memory-visual-v0.2.0'
    artifact_path = root + '/actions/artifacts/41/zip'

    def setUp(self):
        self.args = SimpleNamespace(run='11', attempt=1, test_job='21', environment=self.prod,
                                    environment_id='31', release_id='51', tag=self.tag,
                                    payload_sha256=self.payload)
        user = {'login': 'Bonzar', 'type': 'User'}
        repo = {'id': preflight.REPO_ID}
        reviewers = [{'type': 'User', 'reviewer': user}]
        env = {'id': 31, 'name': self.prod}
        job = {'run_id': 11, 'run_attempt': 1, 'head_sha': self.control}
        self.receipt = {
            'schema': 1, 'profile': 'visual',
            'workflow_ref': f'Bonzar/craft/{self.workflow}@refs/heads/main',
            'control_sha': self.control, 'run_id': '11', 'run_attempt': '1',
            'trigger': 'workflow_run', 'environment': 'configured-release-visual-test',
            'tag': self.tag, 'revision': self.revision, 'release_id': '51',
            'payload_sha256': self.payload,
        }
        self.artifact = {
            'name': 'configured-release-test-11-attempt-1', 'id': 41, 'expired': False,
            'workflow_run': {'id': 11, 'head_sha': self.control,
                             'repository_id': repo['id'], 'head_repository_id': repo['id']},
        }
        self.responses = {
            '/user': user,
            self.root + '/branches/main': {'protected': True},
            self.path: {
                'id': 11, 'run_attempt': 1, 'path': self.workflow, 'head_branch': 'main',
                'workflow_id': 61, 'status': 'waiting', 'event': 'workflow_run',
                'display_title': f'Visual Release · {self.tag}', 'repository': repo,
                'head_repository': repo, 'actor': user.copy(), 'triggering_actor': user.copy(),
                'head_sha': self.control,
            },
            self.root + '/actions/workflows/configured-visual-release.yml': {'id': 61},
            self.root + '/releases/51': {'id': 51, 'tag_name': self.tag, 'immutable': True, 'draft': False},
            self.root + '/git/ref/tags/' + self.tag: {
                'ref': 'refs/tags/' + self.tag, 'object': {'type': 'commit', 'sha': self.revision},
            },
            self.path + '/attempts/1/jobs?per_page=100': {'total_count': 2, 'jobs': [
                dict(job, id=21, name='configured / TEST', status='completed', conclusion='success',
                     steps=[{'name': name, 'status': 'completed', 'conclusion': 'success'}
                            for name in preflight.TEST_STEPS]),
                dict(job, id=22, name='configured / PROD', status='waiting', conclusion=None, steps=[]),
            ]},
            self.path + '/artifacts?per_page=100': {'total_count': 1, 'artifacts': [self.artifact]},
            self.root + '/environments/' + self.prod: dict(
                env, can_admins_bypass=False, protection_rules=[
                    {'type': 'required_reviewers', 'prevent_self_review': False, 'reviewers': reviewers},
                ]),
            self.path + '/pending_deployments': [
                {'environment': env, 'current_user_can_approve': True, 'reviewers': reviewers},
            ],
        }
        self.pack_receipt()

    def pack_receipt(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('configured-test-proof.json', json.dumps(self.receipt))
        self.blob = buffer.getvalue()
        self.artifact.update(size_in_bytes=len(self.blob),
                             digest='sha256:' + hashlib.sha256(self.blob).hexdigest())

    def get(self, path):
        if path == self.artifact_path:
            return self.blob
        return json.dumps(self.responses[path]).encode()

    def test_accepts_exact_waiting_candidate(self):
        self.assertIn('run 11 attempt 1', preflight.preflight(self.args, self.get))

    def test_rejects_incorrect_run_and_actor(self):
        run = self.responses[self.path]
        for field, value in [('run_attempt', 2), ('actor', {'login': 'other', 'type': 'User'}),
                             ('path', '.github/workflows/other.yml')]:
            with self.subTest(field=field), patch.dict(run, {field: value}):
                with self.assertRaisesRegex(preflight.Reject, 'Wrong configured release run'):
                    preflight.preflight(self.args, self.get)

    def test_rejects_wrong_job_or_missing_review_eligibility(self):
        with patch.object(self.args, 'test_job', '99'):
            with self.assertRaisesRegex(preflight.Reject, 'Wrong TEST or PROD'):
                preflight.preflight(self.args, self.get)
        self.responses[self.path + '/pending_deployments'][0]['current_user_can_approve'] = False
        with self.assertRaisesRegex(preflight.Reject, 'exact required reviewer'):
            preflight.preflight(self.args, self.get)

    def test_rejects_tampered_and_cross_attempt_receipts(self):
        self.blob += b'X'
        with self.assertRaisesRegex(preflight.Reject, 'archive digest or size mismatch'):
            preflight.preflight(self.args, self.get)
        for field, value in [('run_attempt', '2'), ('payload_sha256', 'f' * 64)]:
            with self.subTest(field=field), patch.dict(self.receipt, {field: value}):
                self.pack_receipt()
                with self.assertRaisesRegex(preflight.Reject, 'does not bind'):
                    preflight.preflight(self.args, self.get)

    @patch.object(preflight.subprocess, 'run')
    def test_gh_adapter_uses_get_only(self, run):
        run.return_value.stdout = b'{}'
        self.assertEqual(preflight.gh_get('/user'), b'{}')
        self.assertEqual(run.call_args.args[0],
                         ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', '/user'])


if __name__ == '__main__':
    unittest.main()
