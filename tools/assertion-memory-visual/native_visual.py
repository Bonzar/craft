"""Gate release-graph visual deployment without changing legacy dispatches."""
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

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
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_REF_PROTECTED': 'true', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_JOB': 'deploy', 'NATIVE_VISUAL_READY': 'true'}.items()):
        raise ValueError('Untrusted or disabled native visual workflow')
    lifecycle.ci.safe(SHA, env['GITHUB_SHA'])
    lifecycle.ci.safe(ID, env['GITHUB_RUN_ID'])
    tag = lifecycle.ci.safe(lifecycle.TAG, env['GRAPH_TAG'])
    with Path(env['GITHUB_EVENT_PATH']).open(encoding='utf8') as source:
        event = json.load(source)
    event_name = env.get('GITHUB_EVENT_NAME')
    if event_name == 'workflow_dispatch':
        inputs = event.get('inputs') or {}
        visual, base = inputs.get('deploy_visual'), inputs.get('deploy_base_test')
        if (inputs.get('tag') != tag or not (visual is True or visual == 'true')
                or not (base is None or base is False or base in ('', 'false'))):
            raise ValueError('Native visual needs explicit, exact existing-tag intent')
    elif event_name == 'workflow_run':
        release_event(event, tag)
    else:
        raise ValueError('Unexpected native visual trigger')
    mode = env['VISUAL_ENV']
    if mode not in ('test', 'prod'):
        raise ValueError('Unexpected visual stage')
    return mode


def release_event(event, tag):
    run = event.get('workflow_run') or {}
    if (event.get('action') != 'completed' or run.get('head_branch') != tag
            or run.get('event') != 'release' or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('run_attempt') != 1
            or (run.get('repository') or {}).get('full_name') != REPO
            or (run.get('head_repository') or {}).get('full_name') != REPO
            or run.get('path') != '.github/workflows/' + lifecycle.WORKFLOW):
        raise ValueError('Expected original successful visual Release build event')
    return (lifecycle.ci.safe(ID, str(run.get('id', ''))),
            lifecycle.ci.safe(SHA, run.get('head_sha', '')))


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



TEST_JOB = 'Deploy visual TEST / Activate visual artifact'
TEST_STEPS = (
    'Verify exact immutable visual archive and stage approval',
    'Verify scoped visual credential presence',
    'Activate exact visual archive',
    'Smoke exact visual ingress',
    'Record exact native visual TEST identity',
    'Preserve visual TEST evidence',
    'Publish same-run visual TEST proof for PROD',
)
WORKFLOW_FILE = 'assertion-memory-release-graph.yml'
RECEIPT_FILE = 'native-test-proof.json'


def verify_same_run_test(get, env, expected, folder):
    # PROD is running in this same first attempt. Only the TEST job must have
    # finished; assuming the enclosing workflow has completed would deadlock.
    run_id, control_sha = env['GITHUB_RUN_ID'], env['GITHUB_SHA']
    root = lifecycle.ROOT
    run_path = root + '/actions/runs/' + run_id
    run = get(run_path)
    wf = get(root + '/actions/workflows/' + WORKFLOW_FILE)
    if (str(run.get('id')) != run_id or run.get('workflow_id') != wf.get('id')
            or not wf.get('id') or run.get('path') != '.github/workflows/' + WORKFLOW_FILE
            or run.get('event') != env['GITHUB_EVENT_NAME'] or run.get('head_branch') != 'main'
            or run.get('head_sha') != control_sha or run.get('run_attempt') != 1
            or run.get('status') != 'in_progress'
            or (run.get('repository') or {}).get('full_name') != REPO
            or (run.get('head_repository') or {}).get('full_name') != REPO):
        raise ValueError('Wrong native visual control run or attempt')
    response = get(run_path + '/attempts/1/jobs?per_page=100')
    jobs = response.get('jobs', [])
    if response.get('total_count') != len(jobs) or len(jobs) > 100:
        raise ValueError('Incomplete native visual jobs')
    matches = [job for job in jobs if job.get('name') == TEST_JOB]
    if (len(matches) != 1 or str(matches[0].get('run_id')) != run_id
            or matches[0].get('head_sha') != control_sha
            or matches[0].get('status') != 'completed' or matches[0].get('conclusion') != 'success'):
        raise ValueError('Exact native visual TEST job did not succeed')
    for name in TEST_STEPS:
        matching = [step for step in matches[0].get('steps', []) if step.get('name') == name]
        if (len(matching) != 1 or matching[0].get('status') != 'completed'
                or matching[0].get('conclusion') != 'success'):
            raise ValueError('Native visual TEST did not verify, activate, smoke and publish evidence')
    response = get(run_path + '/artifacts?per_page=100')
    artifacts = response.get('artifacts', [])
    if response.get('total_count') != len(artifacts) or len(artifacts) > 100:
        raise ValueError('Incomplete native visual artifact list')
    name = f'assertion-memory-native-visual-test-{run_id}-attempt-1'
    matches = [entry for entry in artifacts if entry.get('name') == name]
    if len(matches) != 1:
        raise ValueError('Expected exactly one native visual TEST evidence artifact')
    item = matches[0]
    origin = item.get('workflow_run') or {}
    if (item.get('expired') is not False or str(origin.get('id')) != run_id
            or origin.get('head_sha') != control_sha or not 0 < item.get('size_in_bytes', 0) <= 8192):
        raise ValueError('Wrong native visual TEST evidence origin')
    aid = lifecycle.ci.safe(ID, str(item.get('id', '')))
    digest = lifecycle.ci.safe('sha256:' + DIGEST, item.get('digest', ''))[7:]
    archive_path = folder/'native-test-proof.zip'
    get(root + '/actions/artifacts/' + aid + '/zip', archive_path)
    with archive_path.open('rb') as source:
        if archive_path.stat().st_size > 8192 or hashlib.file_digest(source, 'sha256').hexdigest() != digest:
            raise ValueError('Native visual TEST evidence ZIP digest mismatch')
    with zipfile.ZipFile(archive_path) as archive:
        if archive.namelist() != [RECEIPT_FILE] or archive.getinfo(RECEIPT_FILE).file_size > 4096:
            raise ValueError('Unexpected native visual TEST evidence ZIP content')
        if json.loads(archive.read(RECEIPT_FILE)) != expected:
            raise ValueError('Uploaded native visual TEST evidence differs from exact archive')


def resolve(get, env):
    mode = require_context(env)
    check_approval(get, env, mode)
    release_id = lifecycle.ci.safe(ID, env['GRAPH_RELEASE_ID'])
    tag, rev = lifecycle.identify(get, release_id)
    aid, digest, original_run = lifecycle.build_identity(get, release_id, rev)
    if env['GITHUB_EVENT_NAME'] == 'workflow_run':
        event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes())
        if release_event(event, tag) != (original_run, rev):
            raise ValueError('Visual Release event differs from canonical original build')
    graph = {'TAG': tag, 'REVISION': rev, 'ARTIFACT_ID': aid,
             'ARCHIVE_SHA': digest, 'VERIFIED_SHA': digest, 'BUILD_RUN_ID': original_run}
    if any(env.get('GRAPH_' + key) != val for key, val in graph.items()):
        raise ValueError('Original visual archive differs from verified release graph')
    folder = Path(env['VISUAL_DOWNLOAD'])
    values = lifecycle.download(get, folder, rev, aid, digest)
    # Build the expected proof independently in each stage; the caller output
    # and the exact GitHub TEST evidence must both match it before PROD may send.
    proof = {**values, 'tag': tag, 'release_id': release_id, 'zip_sha256': digest,
             'build_run_id': original_run, 'test_run_id': env['GITHUB_RUN_ID'],
             'workflow_sha': env['GITHUB_SHA'], 'attempt': '1'}
    if mode == 'prod':
        if json.loads(env['NATIVE_VISUAL_TEST_PROOF']) != proof:
            raise ValueError('Native visual PROD must use the exact TEST-proven archive')
        verify_same_run_test(get, env, proof, folder)
    return proof


def require_credentials(env):
    # Do not print or pass any secret to a command here. The subsequent
    # activation/smoke steps receive only their own credentials.
    for name in ('VISUAL_HOST_PIN', 'VISUAL_SSH_KEY',
                 'VISUAL_SIGNING_KEY', 'VISUAL_GATEWAY_TOKEN'):
        if not env.get(name, '').strip():
            raise ValueError('Missing scoped visual credential: ' + name)
    lifecycle.ci.safe(DIGEST, env['VISUAL_GATEWAY_TOKEN'])


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
    elif action == 'preflight':
        require_context(env)
        require_credentials(env)
    elif action == 'send':
        require_context(env)
        if env.get('VISUAL_DEPLOY_ENABLED') != 'true':
            raise ValueError('Native visual transport disabled')
        from visual_send import deploy
        deploy(env)
    elif action == 'receipt':
        if require_context(env) != 'test':
            raise ValueError('Only native visual TEST writes a TEST receipt')
        with (Path(env['VISUAL_DOWNLOAD'])/RECEIPT_FILE).open('x',encoding='utf8') as out:
            json.dump(json.loads(env['NATIVE_VISUAL_TEST_PROOF']), out, sort_keys=True)
    else:
        raise ValueError('Unknown native visual action')


if __name__ == '__main__':
    main()
