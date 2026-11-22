"""Verify trusted immutable visual release metadata and its single build ZIP."""
import hashlib
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
import zipfile

ci = SourceFileLoader('release_ci', str(Path(__file__).parents[1] / 'assertion-memory-qa' / 'release-ci.py')).load_module()
ROOT = '/repos/' + ci.REPO
WORKFLOW = 'assertion-memory-visual.yml'
TOKEN = r'[1-9][0-9]{0,19}'
SHA = r'[a-f0-9]{40}'
DIGEST = r'[a-f0-9]{64}'
TAG = r'assertion-memory-visual-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?'


def identify(get, release_id):
    ci.safe(TOKEN, release_id)
    rel = get(f'{ROOT}/releases/{release_id}')
    if (str(rel.get('id')) != release_id or rel.get('draft') is not False
            or rel.get('immutable') is not True or not rel.get('published_at')):
        raise ValueError('Visual release must be published and GitHub-immutable')
    tag = ci.safe(TAG, rel['tag_name'])
    ref = get(f'{ROOT}/git/ref/tags/{quote(tag,safe="")}')
    if ref.get('ref') != 'refs/tags/'+tag:
        raise ValueError('Visual tag mismatch')
    target = ref['object']
    for _ in range(6):
        if target['type'] != 'tag':
            break
        target = get(f'{ROOT}/git/tags/{ci.safe(SHA,target["sha"])}')['object']
    revision = ci.safe(SHA,target['sha'])
    if target['type'] != 'commit' or get(f'{ROOT}/compare/{revision}...main').get('status') not in ('ahead','identical'):
        raise ValueError('Visual release must identify a commit in main')
    return tag, revision


def locate(get, release_id, revision):
    name = f'assertion-memory-visual-{release_id}-{revision}'
    result = get(f'{ROOT}/actions/artifacts?name={name}&per_page=2')
    if result.get('total_count') != 1 or len(result.get('artifacts', [])) != 1:
        raise ValueError('Expected exactly one original visual build')
    item = result['artifacts'][0]
    aid = ci.safe(TOKEN,str(item['id']))
    digest = ci.safe('sha256:'+DIGEST,item['digest'])[7:]
    origin = item['workflow_run']
    run = get(f'{ROOT}/actions/runs/{ci.safe(TOKEN,str(origin["id"]))}')
    workflow = get(f'{ROOT}/actions/workflows/{WORKFLOW}')
    if (item.get('name') != name or item.get('expired') is not False or not 0 < item.get('size_in_bytes',0) <= 6_000_000
            or origin.get('head_sha') != revision or str(run['id']) != str(origin['id'])
            or run.get('workflow_id') != workflow['id'] or run.get('event') != 'release'
            or run.get('conclusion') != 'success' or run.get('run_attempt') != 1 or run.get('head_sha') != revision
            or (run.get('head_repository') or {}).get('full_name') != ci.REPO):
        raise ValueError('Not the trusted original visual release build')
    return aid, digest


def unpack(path, destination, revision):
    with zipfile.ZipFile(path) as ar:
        files = {item.filename:item for item in ar.infolist()}
        if len(ar.infolist()) != 2 or set(files) != {'visual.tar','receipt.json'}:
            raise ValueError('Unexpected artifact members')
        if files['visual.tar'].file_size > 5_000_000 or files['receipt.json'].file_size > 1024:
            raise ValueError('Visual artifact exceeds limit')
        receipt = json.loads(ar.read('receipt.json')); raw = ar.read('visual.tar')
    sha = hashlib.sha256(raw).hexdigest()
    if (set(receipt) != {'source_commit','archive_sha256','archive_bytes','release_digest'} or receipt['source_commit'] != revision
            or receipt['archive_sha256'] != sha or receipt['archive_bytes'] != len(raw)):
        raise ValueError('Visual payload receipt mismatch')
    with (destination/'visual.tar').open('xb') as out: out.write(raw)
    return sha, ci.safe(DIGEST,receipt['release_digest'])


def promote(expected, received):
    if not all(v and received.get(k) == v for k, v in expected.items()):
        raise ValueError('PROD must receive exact verified TEST artifact and digest')


def main():
    mode, = sys.argv[1:]
    if os.environ['GITHUB_REPOSITORY'] != ci.REPO or os.environ['GITHUB_RUN_ATTEMPT'] != '1':
        raise ValueError('untrusted visual workflow context')
    rid = os.environ['VISUAL_RELEASE_ID']
    tag, rev = identify(ci.api,rid)
    if mode == 'build':
        if os.environ['GITHUB_EVENT_NAME'] != 'release' or os.environ['GITHUB_SHA'] != rev or os.environ.get('GITHUB_REF') != 'refs/tags/'+tag:
            raise ValueError('Visual release event mismatch')
        return
    if mode not in ('test','prod') or os.environ['GITHUB_EVENT_NAME'] != 'workflow_dispatch' or os.environ['GITHUB_REF'] != 'refs/heads/main':
        raise ValueError('Deploy visual on main only')
    aid,digest=locate(ci.api,rid,rev)
    folder=Path(os.environ['VISUAL_DOWNLOAD']); folder.mkdir(mode=0o700)
    archive=folder/'download.zip'
    ci.api(f'{ROOT}/actions/artifacts/{aid}/zip',archive)
    if archive.stat().st_size > 6_000_000 or hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError('GitHub visual ZIP digest mismatch')
    sha, release = unpack(archive,folder,rev)
    values={'revision':rev,'artifact_id':aid,'sha256':sha,'release':release}
    if mode=='prod':
        run_id=ci.safe(TOKEN,os.environ['GITHUB_RUN_ID'])
        reviews=ci.api(f'{ROOT}/actions/runs/{run_id}/approvals')
        reviewer=ci.safe(TOKEN,os.environ['VISUAL_PROD_APPROVER_ID'])
        if not any(str((rec.get('user') or {}).get('id')) == reviewer
                   and rec.get('state') == 'approved'
                   and any(e.get('name')=='assertion-memory-visual-prod' for e in rec.get('environments',[]))
                   for rec in reviews):
            raise ValueError('Missing required visual PROD review')
        promote(values,{key:os.environ.get('VISUAL_TEST_'+key.upper()) for key in values})
    with Path(os.environ['GITHUB_OUTPUT']).open('a',encoding='utf8') as out:
        for key,value in values.items(): out.write(f'{key}={value}\n')


if __name__=='__main__': main()
