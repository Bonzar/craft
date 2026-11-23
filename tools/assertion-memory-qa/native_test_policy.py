"""Base TEST graph authorization and exact build binding."""
import re

REF = 'Bonzar/craft/.github/workflows/assertion-memory-release-graph.yml@refs/heads/main'


def require_native_test(env):
    expected = {'GITHUB_REPOSITORY': 'Bonzar/craft', 'GITHUB_WORKFLOW_REF': REF,
                'GITHUB_EVENT_NAME': 'workflow_run', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REF_PROTECTED': 'true', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_JOB': 'native-base-test', 'NATIVE_TEST_ENV_READY': 'true'}
    if any(env.get(key) != value for key, value in expected.items()):
        raise ValueError('Unexpected or disabled native TEST context')
    if not re.fullmatch('[0-9a-f]{40}', env.get('GITHUB_SHA', '')):
        raise ValueError('Invalid native TEST control revision')


def match_original(env, release_id, tag, revision, identity):
    expected = {'GRAPH_TAG': tag, 'GRAPH_RELEASE_ID': release_id,
                'GRAPH_REVISION': revision, 'GRAPH_ARTIFACT_ID': identity['artifact_id'],
                'GRAPH_BUILD_RUN_ID': identity['build_run_id'],
                'GRAPH_ARCHIVE_SHA': identity['zip_sha256'],
                'GRAPH_VERIFIED_SHA': identity['zip_sha256']}
    if any(not value or env.get(key) != value for key, value in expected.items()):
        raise ValueError('Artifact differs from original verified graph artifact')
