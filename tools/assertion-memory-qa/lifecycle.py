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


def main():
    import sys
    mode, = sys.argv[1:]
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
            run = ci.safe(ID, os.environ['GITHUB_RUN_ID'])
            history = ci.api(f'{ROOT}/actions/runs/{run}/approvals')
            if not any((entry.get('user') or {}).get('id') == 61428612
                       and ci.tested_approval([entry]) for entry in history):
                raise ValueError('Missing approval from required production reviewer')
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
