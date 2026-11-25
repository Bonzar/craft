"""Read-only native GitHub release graph. Never execute a selected release's code."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = 'Bonzar/craft'
REPO_ID = 1260097465
ROOT = '/repos/' + REPO
MAIN = 'refs/heads/main'
CONTROL = REPO + '/.github/workflows/assertion-memory-release-graph.yml@' + MAIN
MAX_ARTIFACT = 110_000_000
ID = r'[1-9][0-9]{0,19}'
SHA = r'[0-9a-f]{40}'
DIGEST = r'[0-9a-f]{64}'
VERSION = r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?'


@dataclass(frozen=True)
class Product:
    tag_prefix: str
    artifact_prefix: str
    workflow: str


class Reject(ValueError):
    """Trusted fixed diagnostic; never carry HTTP values in this exception."""


PRODUCTS = (
    Product('assertion-memory-visual-', 'assertion-memory-visual-', 'assertion-memory-visual.yml'),
    Product('assertion-memory-', 'assertion-memory-base-release-', 'assertion-memory-base.yml'),
)


def exact(pattern, value):
    if not isinstance(value,str) or not re.fullmatch(pattern,value):
        raise Reject('Invalid release graph identity')
    return value


def product_of_tag(tag):
    products=[p for p in PRODUCTS if isinstance(tag,str) and
              re.fullmatch(re.escape(p.tag_prefix)+VERSION,tag)]
    if len(products)!=1:
        raise Reject('Use an immutable base or visual release version tag')
    return products[0]


def api(get_path, token):
    if not get_path.startswith(ROOT+'/'):
        raise Reject('Unexpected metadata endpoint')
    request=Request('https://api.github.com'+get_path,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28'})
    # Metadata requests require a direct same-origin reply.
    with build_opener(NoRedirect()).open(request,timeout=20) as response:
        body=response.read(4_000_001)
    if len(body)>4_000_000:
        raise Reject('Metadata payload exceeds bound')
    return json.loads(body)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Reject('Unexpected GitHub redirect')


class ArtifactRedirect(HTTPRedirectHandler):
    """Permit only one GitHub artifact-storage redirect without carrying auth."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        src=urlparse(req.full_url)
        dst=urlparse(newurl)
        host=dst.hostname or ''
        if (src.netloc!='api.github.com' or dst.scheme!='https' or dst.username
                or dst.password or dst.port is not None
                or not (host.endswith('.blob.core.windows.net')
                        or host in ('objects.githubusercontent.com','release-assets.githubusercontent.com',
                                    'github-production-release-asset-2e65be.s3.amazonaws.com'))):
            raise Reject('Unexpected artifact storage')
        return Request(newurl,method='GET')


def artifact_digest(artifact_id, token):
    artifact_id=exact(ID,artifact_id)
    initial='https://api.github.com'+ROOT+'/actions/artifacts/'+artifact_id+'/zip'
    request=Request(initial,headers={'Authorization':'Bearer '+token,
                   'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    digest=hashlib.sha256()
    size=0
    with build_opener(ArtifactRedirect()).open(request,timeout=25) as response:
        while chunk:=response.read(1024*1024):
            size+=len(chunk)
            if size>MAX_ARTIFACT:
                raise Reject('Archived build exceeds bound')
            digest.update(chunk)
    if not size:
        raise Reject('Empty archived build')
    return digest.hexdigest()


def release_commit(get, tag):
    route=ROOT+'/git/ref/tags/'+quote(tag,safe='')
    ref=get(route)
    if ref.get('ref')!='refs/tags/'+tag:
        raise Reject('Release tag ref mismatch')
    target=ref['object']
    for _ in range(6):
        if target.get('type')!='tag':
            break
        target=get(ROOT+'/git/tags/'+exact(SHA,target.get('sha')))['object']
    if target.get('type')!='commit':
        raise Reject('Release tag did not select a commit')
    revision=exact(SHA,target.get('sha'))
    ancestry=get(ROOT+'/compare/'+revision+'...main')
    if ancestry.get('status') not in ('ahead','identical'):
        raise Reject('Selected version is not from main history')
    return revision


def release_by_id(get, release_id):
    release_id=exact(ID,release_id)
    release=get(ROOT+'/releases/'+release_id)
    if (str(release.get('id'))!=release_id or release.get('draft') is not False
            or release.get('immutable') is not True or not release.get('published_at')):
        raise Reject('Expected published immutable release')
    tag=release.get('tag_name')
    product=product_of_tag(tag)
    return release_id,tag,product,release_commit(get,tag)


def run_ok(get, run_id, product, revision, tag):
    run_id=exact(ID,run_id)
    canonical=get(ROOT+'/actions/runs/'+run_id)
    workflow=get(ROOT+'/actions/workflows/'+product.workflow)
    if (str(canonical.get('id'))!=run_id or canonical.get('workflow_id')!=workflow['id']
            or canonical.get('path')!='.github/workflows/'+product.workflow
            or canonical.get('event')!='release' or canonical.get('run_attempt')!=1
            or canonical.get('status')!='completed' or canonical.get('conclusion')!='success'
            or canonical.get('head_sha')!=revision or canonical.get('head_branch')!=tag
            or (canonical.get('repository') or {}).get('id')!=REPO_ID
            or (canonical.get('head_repository') or {}).get('id')!=REPO_ID):
        raise Reject('Not the successful original release build')
    return canonical


def one_artifact(result):
    artifacts=result.get('artifacts')
    if result.get('total_count')!=1 or not isinstance(artifacts,list) or len(artifacts)!=1:
        raise Reject('Expected exactly one original archived build')
    return artifacts[0]


def result_of(get, release_id, tag, product, revision, artifact, expected_run=None):
    name=product.artifact_prefix+release_id+'-'+revision
    origin=artifact.get('workflow_run') or {}
    run_id=exact(ID,str(origin.get('id','')))
    if (artifact.get('name')!=name or artifact.get('expired') is not False
            or not isinstance(artifact.get('size_in_bytes'),int)
            or not 0<artifact['size_in_bytes']<=MAX_ARTIFACT
            or origin.get('head_sha')!=revision or origin.get('repository_id')!=REPO_ID
            or origin.get('head_repository_id')!=REPO_ID
            or (expected_run is not None and run_id!=expected_run)):
        raise Reject('Artifact origin does not match the immutable release')
    run_ok(get,run_id,product,revision,tag)
    return dict(tag=tag,revision=revision,release_id=release_id,build_run_id=run_id,
                artifact_id=exact(ID,str(artifact.get('id',''))),
                archive_sha=exact('sha256:'+DIGEST,artifact.get('digest',''))[7:])


def selection_for_tag(get, tag):
    product_of_tag(tag)
    indexed=get(ROOT+'/releases/tags/'+quote(tag,safe=''))
    release_id,actual,product,revision=release_by_id(get,str(indexed.get('id','')))
    if actual!=tag:
        raise Reject('Selected tag changed')
    return release_id,product,revision


def archive_filter(product, release_id, revision):
    name=product.artifact_prefix+release_id+'-'+revision
    return '?per_page=2&name='+quote(name,safe='')


def from_tag(get, tag):
    release_id,product,revision=selection_for_tag(get,tag)
    artifact=one_artifact(get(ROOT+'/actions/artifacts'+archive_filter(product,release_id,revision)))
    return result_of(get,release_id,tag,product,revision,artifact)


def from_completed_build(get, run):
    # Event provides handles, never command text or asset downloads.
    run_id=exact(ID,str(run.get('id','')))
    if run.get('event')!='release' or run.get('conclusion')!='success':
        raise Reject('Not a successful release build event')
    tag=run.get('head_branch')
    release_id,product,revision=selection_for_tag(get,tag)
    if exact(SHA,run.get('head_sha'))!=revision:
        raise Reject('Release event source revision mismatch')
    # Only the canonical archive must be unique. The producer can publish other
    # named packages (e.g. Sites) in the same run.
    artifact=one_artifact(get(ROOT+'/actions/runs/'+run_id+'/artifacts'+
                              archive_filter(product,release_id,revision)))
    return result_of(get,release_id,tag,product,revision,artifact,expected_run=run_id)


def require_control(env):
    if (env.get('GITHUB_REPOSITORY')!=REPO or env.get('GITHUB_REF')!=MAIN
            or env.get('GITHUB_WORKFLOW_REF')!=CONTROL or env.get('GITHUB_RUN_ATTEMPT')!='1'):
        raise Reject('Release graph must use original trusted main attempt')


def main(env=os.environ):
    mode,=sys.argv[1:]
    require_control(env)
    event=env['GITHUB_EVENT_NAME']
    get=lambda path:api(path,env['GH_TOKEN'])
    if mode=='resolve':
        if event=='workflow_dispatch':
            evidence=from_tag(get,env['SELECTED_TAG'])
        elif event=='workflow_run':
            evidence=from_completed_build(get,json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes())['workflow_run'])
        else:
            raise Reject('Unsupported release graph trigger')
        with Path(env['GITHUB_OUTPUT']).open('a',encoding='ascii') as out:
            for key,value in evidence.items():
                out.write(f'{key}={value}\n')
        with Path(env['GITHUB_STEP_SUMMARY']).open('a',encoding='utf-8') as info:
            tag=evidence['tag']
            run=evidence['build_run_id']
            info.write(f'### {tag}\n\n[Immutable release](https://github.com/{REPO}/releases/tag/{tag}) · '
                       f'[Original build](https://github.com/{REPO}/actions/runs/{run})\n\n'
                       f'Commit: `{evidence["revision"]}`\n\nNo deployment in this preview.\n')
    elif mode=='hash':
        approved=exact(DIGEST,env['ARCHIVE_SHA'])
        if artifact_digest(env['ARTIFACT_ID'],env['GH_TOKEN'])!=approved:
            raise Reject('Original artifact hash mismatch')
        with Path(env['GITHUB_OUTPUT']).open('a',encoding='ascii') as out:
            out.write('verified_sha='+approved+'\n')
    elif mode=='compare':
        if exact(DIGEST,env['ARCHIVE_SHA'])!=exact(DIGEST,env['VERIFIED_SHA']):
            raise Reject('Promotion preview changed immutable bytes')
    else:
        raise Reject('Invalid release preview stage')


if __name__=='__main__':
    try:
        main()
    except (HTTPError,OSError,KeyError,ValueError) as error:
        # Only explicit fixed messages written above may be logged.
        # Never log HTTP bodies, signed download URLs or request headers.
        raise SystemExit('Native release preview stopped: '+(str(error) if isinstance(error,Reject)
                         else type(error).__name__)) from None
