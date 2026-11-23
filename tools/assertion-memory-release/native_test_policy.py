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


def check_approval(get, env):
    require_native_test(env)
    def num(key):
        val = env.get(key, '')
        if not re.fullmatch('[1-9][0-9]{0,19}', val):
            raise ValueError('Native TEST approval identity is not configured')
        return val
    run, environment, reviewer = (num(key) for key in
        ('GITHUB_RUN_ID', 'NATIVE_TEST_ENV_ID', 'NATIVE_TEST_REVIEWER_ID'))
    repo = '/repos/Bonzar/craft'
    name = 'assertion-memory-native-base-test'
    prefix = repo + '/environments/' + name
    settings = get(prefix)
    if (settings.get('name') != name or str(settings.get('id')) != environment or
            settings.get('deployment_branch_policy') !=
            {'protected_branches': False, 'custom_branch_policies': True} or
            get(repo + '/branches/main').get('protected') is not True):
        raise ValueError('Wrong GitHub environment or branch protection')
    rules = [entry for entry in settings.get('protection_rules', [])
             if entry.get('type') == 'required_reviewers']
    if len(rules) != 1:
        raise ValueError('Required reviewer rule missing or ambiguous')
    identities = [(entry.get('type'), str((entry.get('reviewer') or {}).get('id')))
                  for entry in rules[0].get('reviewers', [])]
    if identities != [('User', reviewer)]:
        raise ValueError('Wrong native TEST reviewer rule')
    branches = get(prefix + '/deployment-branch-policies?per_page=2')
    if (branches.get('total_count') != 1 or
            [(item.get('type'), item.get('name')) for item in
             branches.get('branch_policies', [])] != [('branch', 'main')]):
        raise ValueError('Native TEST must be restricted to main only')
    approvals = get(repo + '/actions/runs/' + run + '/approvals')
    matched = [entry for entry in approvals if any(
        item.get('name') == name and str(item.get('id')) == environment
        for item in entry.get('environments', []))]
    if (len(matched) != 1 or matched[0].get('state') != 'approved' or
            (matched[0].get('user') or {}).get('type') != 'User' or
            str((matched[0].get('user') or {}).get('id')) != reviewer):
        raise ValueError('No exact authorized native TEST approval')
