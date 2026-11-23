"""Resolve only immutable, published main releases and their original build."""
import hashlib
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import quote

from importlib.machinery import SourceFileLoader

ci = SourceFileLoader('release_ci', str(Path(__file__).with_name('release-ci.py'))).load_module()
ROOT = '/repos/' + ci.REPO
WORKFLOW = 'assertion-memory-base.yml'
ID = r'[1-9][0-9]{0,19}'
SHA = r'[0-9a-f]{40}'
DIGEST = r'[0-9a-f]{64}'
TAG = r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?'


def identify(get, release_id):
    ci.safe(ID, release_id)
    release = get(f'{ROOT}/releases/{release_id}')
    if (str(release.get('id')) != release_id or release.get('draft') is not False
            or release.get('immutable') is not True or not release.get('published_at')):
        raise ValueError('Release must be published and GitHub-immutable')
    tag = ci.safe(TAG, release.get('tag_name', ''))
    ref = get(f'{ROOT}/git/ref/tags/{quote(tag, safe="")}')
    if ref.get('ref') != 'refs/tags/' + tag:
        raise ValueError('Tag ref mismatch')
    target = ref['object']
    for _ in range(6):
        if target['type'] != 'tag':
            break
        target = get(f'{ROOT}/git/tags/{ci.safe(SHA, target["sha"])}')['object']
    revision = ci.safe(SHA, target['sha'])
    if target['type'] != 'commit':
        raise ValueError('Release does not identify a commit')
    if get(f'{ROOT}/compare/{revision}...main').get('status') not in ('ahead', 'identical'):
        raise ValueError('Release commit is not an ancestor of main')
    return tag, revision


def build_identity(get, release_id, revision):
    name = f'assertion-memory-base-release-{release_id}-{revision}'
    result = get(f'{ROOT}/actions/artifacts?per_page=2&name={name}')
    if result.get('total_count') != 1 or len(result.get('artifacts', [])) != 1:
        raise ValueError('Expected exactly one original release build; absent, expired or duplicate')
    artifact = result['artifacts'][0]
    artifact_id = ci.safe(ID, str(artifact['id']))
    digest = ci.safe(r'sha256:' + DIGEST, artifact['digest'])[7:]
    origin = artifact.get('workflow_run') or {}
    run_id = ci.safe(ID, str(origin['id']))
    run = get(f'{ROOT}/actions/runs/{run_id}')
    workflow = get(f'{ROOT}/actions/workflows/{WORKFLOW}')
    if (artifact.get('name') != name or artifact.get('expired') is not False
            or origin.get('head_sha') != revision
            or not 0 < artifact.get('size_in_bytes', 0) <= 110_000_000
            or str(run.get('id')) != run_id or run.get('workflow_id') != workflow['id']
            or run.get('event') != 'release' or run.get('conclusion') != 'success'
            or run.get('run_attempt') != 1 or run.get('head_sha') != revision
            or (run.get('head_repository') or {}).get('full_name') != ci.REPO):
        raise ValueError('Artifact is not the original verified release build')
    return {'artifact_id': artifact_id, 'zip_sha256': digest,
            'build_run_id': run_id, 'build_attempt': '1'}


def find_artifact(get, release_id, revision):
    identity = build_identity(get, release_id, revision)
    return identity['artifact_id'], identity['zip_sha256']


def verify_receipt(expected, observed):
    if not all(value and value == observed.get(key) for key, value in expected.items()):
        raise ValueError('Production must match the activated and smoke-tested TEST receipt')



SEND_STEP = 'Activate and smoke-check via pinned restricted connection'
UPLOAD_STEP = 'Persist verified TEST receipt'
WORKFLOW_REF = f'{ci.REPO}/.github/workflows/{WORKFLOW}@refs/heads/main'


def dispatch_context(env):
    if (env.get('GITHUB_REPOSITORY') != ci.REPO
            or env.get('GITHUB_RUN_ATTEMPT') != '1'
            or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REF') != 'refs/heads/main'
            or env.get('GITHUB_WORKFLOW_REF') != WORKFLOW_REF):
        raise ValueError('Dispatch the allowlisted workflow on main, first attempt only')
    return ci.safe(SHA, env['GITHUB_SHA'])


def receipt_name(run):
    return f'assertion-memory-test-receipt-{ci.safe(ID, run)}-attempt-1'


def receipt_context(run, workflow_sha):
    return {'schema': 1, 'repository': ci.REPO, 'environment': 'test',
            'workflow_ref': WORKFLOW_REF, 'workflow_sha': workflow_sha,
            'test_run_id': run, 'test_attempt': '1', 'test_job': 'deploy-test'}


def write_test_receipt(env):
    workflow_sha = dispatch_context(env)
    if env.get('GITHUB_JOB') != 'deploy-test':
        raise ValueError('Receipt requires the TEST job')
    run = ci.safe(ID, env['GITHUB_RUN_ID'])
    path = Path(env['RELEASE_DOWNLOAD_DIR'])
    identity = json.loads((path / 'identity.json').read_bytes())
    receiver = json.loads((path / 'receiver-receipt.json').read_bytes())
    if receiver != {'status': 'ok', 'environment': 'test',
                    'revision': identity['revision'], 'sha256': identity['sha256']}:
        raise ValueError('Receipt requires exact successful TEST receiver response')
    with (path / 'release.tar.gz').open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != identity['sha256']:
            raise ValueError('TEST payload changed after receiver success')
    value = {**identity, **receipt_context(run, workflow_sha)}
    (path / 'test-receipt.json').write_text(json.dumps(value, sort_keys=True) + '\n')


def read_test_receipt(get, run_id, workflow_sha, expected, output):
    """Trust only a completed TEST from this exact reviewed main workflow commit.

    Requiring equal control commits binds environment/job configuration to the
    code executing promotion. A later main commit needs a fresh TEST, never a build.
    """
    ci.safe(ID, run_id)
    ci.safe(SHA, workflow_sha)
    run = get(f'{ROOT}/actions/runs/{run_id}')
    workflow = get(f'{ROOT}/actions/workflows/{WORKFLOW}')
    if (str(run.get('id')) != run_id or run.get('workflow_id') != workflow['id']
            or run.get('path') != '.github/workflows/' + WORKFLOW
            or run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main'
            or run.get('head_sha') != workflow_sha or run.get('run_attempt') != 1
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or (run.get('repository') or {}).get('full_name') != ci.REPO
            or (run.get('head_repository') or {}).get('full_name') != ci.REPO):
        raise ValueError('Source TEST run is not the exact successful main workflow/attempt')
    result = get(f'{ROOT}/actions/runs/{run_id}/attempts/1/jobs?per_page=100')
    jobs = result.get('jobs', [])
    if result.get('total_count') != len(jobs) or len(jobs) > 100:
        raise ValueError('Incomplete TEST jobs response')
    matches = [job for job in jobs if job.get('name') == 'Deploy TEST']
    if len(matches) != 1:
        raise ValueError('Expected exactly one TEST job')
    job = matches[0]
    if (str(job.get('run_id')) != run_id or job.get('head_sha') != workflow_sha
            or job.get('status') != 'completed' or job.get('conclusion') != 'success'):
        raise ValueError('TEST job did not complete successfully')
    for name in (SEND_STEP, UPLOAD_STEP):
        steps = [step for step in job.get('steps', []) if step.get('name') == name]
        if len(steps) != 1 or steps[0].get('conclusion') != 'success' or steps[0].get('status') != 'completed':
            raise ValueError('TEST activation or receipt upload was not successful')
    result = get(f'{ROOT}/actions/runs/{run_id}/artifacts?per_page=100')
    # This dispatch only emits one artifact; build and PROD jobs are skipped.
    if result.get('total_count') != 1 or len(result.get('artifacts', [])) != 1:
        raise ValueError('Expected exactly one durable TEST receipt artifact')
    artifact = result['artifacts'][0]
    origin = artifact.get('workflow_run') or {}
    if (artifact.get('name') != receipt_name(run_id) or artifact.get('expired') is not False
            or str(origin.get('id')) != run_id or origin.get('head_sha') != workflow_sha
            or not 0 < artifact.get('size_in_bytes', 0) <= 16384):
        raise ValueError('TEST receipt artifact provenance mismatch')
    artifact_id = ci.safe(ID, str(artifact['id']))
    digest = ci.safe(r'sha256:' + DIGEST, artifact['digest'])[7:]
    archive = output / 'test-receipt.zip'
    get(f'{ROOT}/actions/artifacts/{artifact_id}/zip', archive)
    with archive.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != digest:
            raise ValueError('TEST receipt ZIP digest mismatch')
    with zipfile.ZipFile(archive) as z:
        if z.namelist() != ['test-receipt.json'] or z.getinfo('test-receipt.json').file_size > 8192:
            raise ValueError('Unexpected TEST receipt ZIP contents')
        observed = json.loads(z.read('test-receipt.json'))
    required = {**expected, **receipt_context(run_id, workflow_sha)}
    if observed != required:
        raise ValueError('Production must match the activated and smoke-tested TEST receipt')
    return observed



def authorize_production(get, env):
    """Read the allowlist ONLY from the protected PROD environment, never repo vars."""
    workflow_sha = dispatch_context(env)
    if env.get('PRODUCTION_READY') != 'true':
        raise ValueError('Independent PROD readiness is disabled')
    if env.get('GITHUB_REF_PROTECTED') != 'true' or env.get('GITHUB_JOB') != 'deploy-production':
        raise ValueError('PROD requires the protected main branch and production job')
    actor = ci.safe(ID, env.get('GITHUB_ACTOR_ID', ''))
    run_id = ci.safe(ID, env['GITHUB_RUN_ID'])
    run = get(f'{ROOT}/actions/runs/{run_id}')
    if (str(run.get('id')) != run_id or str((run.get('actor') or {}).get('id')) != actor
            or str((run.get('triggering_actor') or {}).get('id')) != actor
            or run.get('event') != 'workflow_dispatch' or run.get('run_attempt') != 1
            or run.get('head_branch') != 'main' or run.get('head_sha') != workflow_sha
            or run.get('path') != '.github/workflows/' + WORKFLOW
            or (run.get('repository') or {}).get('full_name') != ci.REPO
            or (run.get('head_repository') or {}).get('full_name') != ci.REPO):
        raise ValueError('PROD actor or authenticated dispatch provenance mismatch')
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes())
    if (event.get('inputs') or {}) != {'action': 'production', 'release_id': env['RELEASE_ID'],
                                      'test_run_id': env['TEST_RUN_ID']}:
        raise ValueError('PROD requires explicit matching production dispatch inputs')
    environment_path = f'{ROOT}/environments/assertion-memory-production'
    environment = get(environment_path)
    if (environment.get('name') != 'assertion-memory-production'
            or environment.get('deployment_branch_policy') !=
            {'protected_branches': False, 'custom_branch_policies': True}):
        raise ValueError('PROD environment must be explicitly branch scoped')
    policies = get(environment_path + '/deployment-branch-policies?per_page=100')
    rules = policies.get('branch_policies', [])
    if (policies.get('total_count') != 1 or len(rules) != 1
            or rules[0].get('name') != 'main' or rules[0].get('type') != 'branch'):
        raise ValueError('PROD environment must allow only the main branch, no tags')
    config = get(environment_path + '/variables/ASSERTION_MEMORY_PROD_ACTOR_IDS')
    if config.get('name') != 'ASSERTION_MEMORY_PROD_ACTOR_IDS':
        raise ValueError('Missing environment-only PROD actor allowlist')
    value = ci.safe(r'[1-9][0-9]{0,19}(?:,[1-9][0-9]{0,19}){0,49}', config.get('value', ''))
    actors = value.split(',')
    if len(set(actors)) != len(actors) or actor not in actors:
        raise ValueError('PROD actor is not in the strict environment allowlist')
    return actor


def require_host_proof():
    # No flag, receipt file, caller-supplied boolean or SSH credential can satisfy
    # this placeholder. Integration requires separately reviewed authorization code.
    raise ValueError('PROD transport disabled: exact host proof bridge is not integrated')


def main():
    import sys
    mode, = sys.argv[1:]
    if mode == 'host-proof':
        require_host_proof()
    if mode == 'receipt':
        write_test_receipt(os.environ)
        return
    if os.environ['GITHUB_REPOSITORY'] != ci.REPO or os.environ['GITHUB_RUN_ATTEMPT'] != '1':
        raise ValueError('Unexpected repository or repeated run')
    release_id = os.environ['RELEASE_ID']
    tag, revision = identify(ci.api, release_id)
    outputs = {'tag': tag, 'revision': revision}
    if mode == 'build':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
        if (os.environ['GITHUB_EVENT_NAME'] != 'release' or event.get('action') != 'published'
                or str((event.get('release') or {}).get('id')) != release_id
                or os.environ['GITHUB_SHA'] != revision or os.environ['GITHUB_REF'] != 'refs/tags/' + tag):
            raise ValueError('Release event and current immutable release differ')
    elif mode in ('test', 'production'):
        workflow_sha = dispatch_context(os.environ)
        if os.environ.get('GITHUB_JOB') != ('deploy-test' if mode == 'test' else 'deploy-production'):
            raise ValueError('Unexpected deployment job')
        identity = build_identity(ci.api, release_id, revision)
        artifact_id, digest = identity['artifact_id'], identity['zip_sha256']
        if mode == 'production':
            authorize_production(ci.api, os.environ)
        path = Path(os.environ['RELEASE_DOWNLOAD_DIR'])
        path.mkdir(mode=0o700)
        archive = path / 'github.zip'
        ci.api(f'{ROOT}/actions/artifacts/{artifact_id}/zip', archive)
        with archive.open('rb') as f:
            if hashlib.file_digest(f, 'sha256').hexdigest() != digest:
                raise ValueError('GitHub archive digest mismatch')
        inner = ci.unpack_zip(archive, path, revision)
        identity.update(release_id=release_id, revision=revision, sha256=inner)
        if mode == 'production':
            receipt = read_test_receipt(ci.api, os.environ['TEST_RUN_ID'], workflow_sha, identity, path)
            outputs.update(tested_artifact_id=receipt['artifact_id'],
                           tested_revision=receipt['revision'], tested_sha256=receipt['sha256'])
        else:
            (path / 'identity.json').write_text(json.dumps(identity, sort_keys=True) + '\n')
        outputs.update(artifact_id=artifact_id, release_sha256=inner, release_id=release_id)
    else:
        raise ValueError('Unknown release action')
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as f:
        for key, value in outputs.items():
            f.write(f'{key}={value}\n')


if __name__ == '__main__':
    main()
