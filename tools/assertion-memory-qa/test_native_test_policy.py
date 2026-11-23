import unittest
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

if __name__ == '__main__':
    unittest.main()
