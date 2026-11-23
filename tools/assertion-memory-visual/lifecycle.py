"""Verify trusted immutable visual release metadata and its single build ZIP."""
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
import zipfile

import release_ci as ci
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


def test_receipt(get, test_run_id, release_id, expected, destination, *, unpack_receipt):
    run_id = ci.safe(TOKEN, test_run_id)
    workflow = get(f'{ROOT}/actions/workflows/{WORKFLOW}')
    run = get(f'{ROOT}/actions/runs/{run_id}')
    if (str(run.get('id')) != run_id or run.get('workflow_id') != workflow['id']
            or run.get('path') != '.github/workflows/' + WORKFLOW
            or run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main'
            or run.get('run_attempt') != 1 or run.get('conclusion') != 'success'
            or (run.get('head_repository') or {}).get('full_name') != ci.REPO):
        raise ValueError('Not a successful original visual TEST run on main')
    jobs = get(f'{ROOT}/actions/runs/{run_id}/attempts/1/jobs?per_page=100')
    matches = [job for job in jobs['jobs'] if job['name'] == 'Deploy visual TEST']
    if len(matches) != 1 or matches[0]['conclusion'] != 'success':
        raise ValueError('Protected visual TEST job did not succeed')
    artifacts = get(f'{ROOT}/actions/runs/{run_id}/artifacts?name=visual-test-proof-{run_id}&per_page=2')
    if artifacts.get('total_count') != 1 or len(artifacts['artifacts']) != 1:
        raise ValueError('Must identify exactly one TEST receipt from that run')
    artifact, = artifacts['artifacts']
    if (artifact.get('expired') is not False or artifact.get('name') != f'visual-test-proof-{run_id}'
            or not 0 < artifact.get('size_in_bytes', 0) <= 4096):
        raise ValueError('Visual TEST receipt missing or invalid')
    aid = ci.safe(TOKEN, str(artifact['id']))
    digest = ci.safe('sha256:'+DIGEST, artifact['digest'])[7:]
    zpath = destination / 'test-proof.zip'
    get(f'{ROOT}/actions/artifacts/{aid}/zip', zpath)
    if zpath.stat().st_size > 4096 or hashlib.sha256(zpath.read_bytes()).hexdigest() != digest:
        raise ValueError('Visual TEST receipt ZIP digest mismatch')
    with zipfile.ZipFile(zpath) as z:
        if z.namelist() != ['test-proof.json'] or z.getinfo('test-proof.json').file_size > 1024:
            raise ValueError('Unexpected visual test proof content')
        receipt = unpack_receipt(z.read('test-proof.json'))
    if receipt != {**expected, 'test_run_id': run_id, 'release_id': release_id}:
        raise ValueError('Visual PROD payload differs from the TEST-approved artifact')


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
        test_receipt(ci.api, os.environ['VISUAL_TEST_RUN_ID'], rid, values, folder, unpack_receipt=json.loads)
    with Path(os.environ['GITHUB_OUTPUT']).open('a',encoding='utf8') as out:
        for key,value in values.items(): out.write(f'{key}={value}\n')


if __name__=='__main__': main()
