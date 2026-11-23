import unittest
import sys
import json
from tempfile import TemporaryDirectory
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "assertion-memory-release"))
import native_test_policy as policy

class TestNativeTestPolicy(unittest.TestCase):
    def test_context_fails_closed(self):
        verified = dict(GITHUB_REPOSITORY='Bonzar/craft', GITHUB_WORKFLOW_REF=policy.REF,
            GITHUB_EVENT_NAME='workflow_run', GITHUB_REF='refs/heads/main', GITHUB_REF_PROTECTED='true',
            GITHUB_RUN_ATTEMPT='1', GITHUB_JOB='native-base-test', NATIVE_TEST_ENV_READY='true',
            GITHUB_SHA='a'*40)
        policy.require_native_test(verified)
        for key in verified:
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.require_native_test(dict(verified, **{key: 'wrong'}))

    def test_every_original_identifier_must_match(self):
        tag, rev = 'assertion-memory-v1.0.0', 'a'*40
        verified = dict(GRAPH_TAG=tag, GRAPH_RELEASE_ID='1', GRAPH_REVISION=rev, GRAPH_ARTIFACT_ID='2',
            GRAPH_BUILD_RUN_ID='3', GRAPH_ARCHIVE_SHA='b'*64, GRAPH_VERIFIED_SHA='b'*64)
        identity = dict(artifact_id='2', build_run_id='3', zip_sha256='b'*64)
        policy.match_original(verified, '1', tag, rev, identity)
        for key in verified:
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.match_original(dict(verified, **{key: 'wrong'}), '1', tag, rev, identity)

    def test_approval_requires_configured_environment_and_human_for_exact_run(self):
        env = dict(GITHUB_REPOSITORY='Bonzar/craft', GITHUB_WORKFLOW_REF=policy.REF,
                   GITHUB_EVENT_NAME='workflow_run', GITHUB_REF='refs/heads/main',
                   GITHUB_REF_PROTECTED='true', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_JOB='native-base-test', NATIVE_TEST_ENV_READY='true',
                   GITHUB_SHA='a'*40, GITHUB_RUN_ID='12', NATIVE_TEST_ENV_ID='45',
                   NATIVE_TEST_REVIEWER_ID='67')
        name = 'assertion-memory-native-base-test'
        prefix = '/environments/' + name
        state = {prefix: {'name': name, 'id': 45, 'deployment_branch_policy':
                    {'protected_branches': False, 'custom_branch_policies': True},
                    'protection_rules': [{'type': 'required_reviewers', 'reviewers':
                        [{'type': 'User', 'reviewer': {'id': 67}}]}]},
                 '/branches/main': {'protected': True},
                 prefix+'/deployment-branch-policies?per_page=2':
                    {'total_count': 1, 'branch_policies': [{'type': 'branch', 'name': 'main'}]},
                 '/actions/runs/12/approvals': [{'state': 'approved',
                    'user': {'type': 'User', 'id': 67}, 'environments': [{'name': name, 'id': 45}]}]}
        def get(path):
            return state[path.removeprefix('/repos/Bonzar/craft')]
        policy.check_approval(get, env)
        cases = [(state[prefix], 'id', 44), (state[prefix], 'protection_rules', []),
                 (state['/branches/main'], 'protected', False),
                 (state[prefix+'/deployment-branch-policies?per_page=2'], 'branch_policies',
                  [{'type': 'tag', 'name': 'main'}]),
                 (state['/actions/runs/12/approvals'][0], 'state', 'rejected'),
                 (state['/actions/runs/12/approvals'][0]['user'], 'id', 68)]
        for entry, key, value in cases:
            original = entry[key]
            entry[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.check_approval(get, env)
            entry[key] = original

    def test_manual_replay_requires_explicit_request_and_same_exact_base_tag(self):
        with TemporaryDirectory() as root:
            payload = Path(root) / 'event.json'
            tag = 'assertion-memory-v0.1.1'
            env = dict(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_EVENT_PATH=str(payload), GRAPH_TAG=tag)
            for allowed in (True, 'true'):
                payload.write_text(json.dumps({'inputs': {'tag': tag, 'deploy_base_test': allowed}}))
                policy.require_trigger(env)
            for visual in (True, 'true', 'unexpected', 0):
                payload.write_text(json.dumps({'inputs': {'tag': tag, 'deploy_base_test': True,
                                                          'deploy_visual': visual}}))
                with self.subTest(visual=visual), self.assertRaises(ValueError):
                    policy.require_trigger(env)
            for intent, mark in ((False, tag), ('false', tag), ('true', 'assertion-memory-v0.1.0'),
                                 ('true', 'assertion-memory-visual-v0.1.0'), ('true', tag+'extra')):
                payload.write_text(json.dumps({'inputs': {'tag': mark, 'deploy_base_test': intent}}))
                with self.subTest(intent=intent, tag=mark), self.assertRaises(ValueError):
                    policy.require_trigger(env)

if __name__ == '__main__':
    unittest.main()
