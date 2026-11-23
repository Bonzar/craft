"""Gate release-graph visual deployment without changing legacy dispatches."""
import json
import os
from pathlib import Path
import sys

import lifecycle


REPO = 'Bonzar/craft'
WORKFLOW_REF = REPO + '/.github/workflows/assertion-memory-release-graph.yml@refs/heads/main'
IDENTITY_KEYS = ('revision', 'artifact_id', 'sha256', 'release')
ID = lifecycle.TOKEN
SHA = lifecycle.SHA
DIGEST = lifecycle.DIGEST


def require_context(env):
    if any(env.get(name) != expected for name, expected in {
            'GITHUB_REPOSITORY': REPO, 'GITHUB_WORKFLOW_REF': WORKFLOW_REF,
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_REF_PROTECTED': 'true', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_JOB': 'deploy', 'NATIVE_VISUAL_READY': 'true'}.items()):
        raise ValueError('Untrusted or disabled native visual workflow')
    lifecycle.ci.safe(SHA, env['GITHUB_SHA'])
    lifecycle.ci.safe(ID, env['GITHUB_RUN_ID'])
    tag = lifecycle.ci.safe(lifecycle.TAG, env['GRAPH_TAG'])
    with Path(env['GITHUB_EVENT_PATH']).open(encoding='utf8') as source:
        event = json.load(source)
    inputs = event.get('inputs') or {}
    if (inputs.get('tag') != tag or inputs.get('deploy_visual') not in ('true', True)
            or inputs.get('deploy_base_test') not in (None, '', 'false', False)):
        raise ValueError('Native visual needs explicit, exact existing-tag intent')
    mode = env['VISUAL_ENV']
    if mode not in ('test', 'prod'):
        raise ValueError('Unexpected visual stage')
    return mode


def check_approval(get, env, mode):
    environment_id = lifecycle.ci.safe(ID, env['NATIVE_VISUAL_ENV_ID'])
    reviewer_id = lifecycle.ci.safe(ID, env['NATIVE_VISUAL_REVIEWER_ID'])
    name = 'assertion-memory-native-visual-' + mode
    path = lifecycle.ROOT + '/environments/' + name
    settings = get(path)
    if (settings.get('name') != name or str(settings.get('id')) != environment_id
            or settings.get('deployment_branch_policy') != {
                'protected_branches': False, 'custom_branch_policies': True}
            or get(lifecycle.ROOT + '/branches/main').get('protected') is not True):
        raise ValueError('Wrong visual environment or branch protection')
    rules = [rule for rule in settings.get('protection_rules', [])
             if rule.get('type') == 'required_reviewers']
    if len(rules) != 1 or [(reviewer.get('type'),
                           str((reviewer.get('reviewer') or {}).get('id')))
                          for reviewer in rules[0].get('reviewers', [])] != [('User', reviewer_id)]:
        raise ValueError('Wrong native visual reviewer')
    policy = get(path + '/deployment-branch-policies?per_page=2')
    if (policy.get('total_count') != 1
            or [(rule.get('type'), rule.get('name')) for rule in policy.get('branch_policies', [])]
            != [('branch', 'main')]):
        raise ValueError('Visual deployment environment must be main only')
    approvals = get(lifecycle.ROOT + '/actions/runs/' + env['GITHUB_RUN_ID'] + '/approvals')
    matches = [item for item in approvals if any(
        target.get('name') == name and str(target.get('id')) == environment_id
        for target in item.get('environments', []))]
    if (len(matches) != 1 or matches[0].get('state') != 'approved'
            or (matches[0].get('user') or {}).get('type') != 'User'
            or str((matches[0].get('user') or {}).get('id')) != reviewer_id):
        raise ValueError('No exact authorized visual approval for this run/stage')


def resolve(get, env):
    mode = require_context(env)
    check_approval(get, env, mode)
    release_id = lifecycle.ci.safe(ID, env['GRAPH_RELEASE_ID'])
    tag, rev = lifecycle.identify(get, release_id)
    aid, digest, original_run = lifecycle.build_identity(get, release_id, rev)
    graph = {'TAG': tag, 'REVISION': rev, 'ARTIFACT_ID': aid,
             'ARCHIVE_SHA': digest, 'VERIFIED_SHA': digest, 'BUILD_RUN_ID': original_run}
    if any(env.get('GRAPH_' + key) != val for key, val in graph.items()):
        raise ValueError('Original visual archive differs from verified release graph')
    folder = Path(env['VISUAL_DOWNLOAD'])
    values = lifecycle.download(get, folder, rev, aid, digest)
    # The workflow supplies this exclusively from needs.native-visual-test after
    # activation, live ingress smoke and evidence upload in this same run.
    proof = {**values, 'tag': tag, 'release_id': release_id, 'zip_sha256': digest,
             'build_run_id': original_run, 'test_run_id': env['GITHUB_RUN_ID'],
             'workflow_sha': env['GITHUB_SHA'], 'attempt': '1'}
    if mode == 'prod' and json.loads(env['NATIVE_VISUAL_TEST_PROOF']) != proof:
        raise ValueError('Native visual PROD must use the exact TEST-proven archive')
    return proof


def main():
    action, = sys.argv[1:]
    env = os.environ
    if action == 'resolve':
        proof = resolve(lifecycle.ci.api, env)
        values = {key: proof[key] for key in IDENTITY_KEYS}
        values['proof'] = json.dumps(proof, sort_keys=True, separators=(',', ':'))
        with Path(env['GITHUB_OUTPUT']).open('a', encoding='utf8') as out:
            for key, value in values.items():
                out.write(f'{key}={value}\n')
    elif action == 'send':
        require_context(env)
        if env.get('VISUAL_DEPLOY_ENABLED') != 'true':
            raise ValueError('Native visual transport disabled')
        from visual_send import deploy
        deploy(env)
    elif action == 'receipt':
        if require_context(env) != 'test':
            raise ValueError('Only native visual TEST writes a TEST receipt')
        with (Path(env['VISUAL_DOWNLOAD'])/'native-test-proof.json').open('x',encoding='utf8') as out:
            json.dump(json.loads(env['NATIVE_VISUAL_TEST_PROOF']), out, sort_keys=True)
    else:
        raise ValueError('Unknown native visual action')


if __name__ == '__main__':
    main()
