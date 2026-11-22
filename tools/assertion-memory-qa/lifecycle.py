"""Resolve only immutable, published main releases and their original build."""
import hashlib
import json
import os
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


def find_artifact(get, release_id, revision):
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
    return artifact_id, digest


def verify_receipt(expected, observed):
    if not all(value and value == observed.get(key) for key, value in expected.items()):
        raise ValueError('Production must match the activated and smoke-tested TEST receipt')


def main():
    import sys
    mode, = sys.argv[1:]
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
        if (os.environ['GITHUB_EVENT_NAME'] != 'workflow_dispatch'
                or os.environ['GITHUB_REF'] != 'refs/heads/main'):
            raise ValueError('Dispatch this workflow on main')
        artifact_id, digest = find_artifact(ci.api, release_id, revision)
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
        if mode == 'production':
            verify_receipt({'release_id': release_id, 'artifact_id': artifact_id, 'revision': revision, 'sha256': inner},
                           {key: os.environ.get('TESTED_' + key.upper())
                            for key in ('release_id','artifact_id','revision','sha256')})
        outputs.update(artifact_id=artifact_id, release_sha256=inner, release_id=release_id)
    else:
        raise ValueError('Unknown release action')
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as f:
        for key, value in outputs.items():
            f.write(f'{key}={value}\n')


if __name__ == '__main__':
    main()
