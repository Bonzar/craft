"""Read immutable GitHub artifact metadata, verify it, and check a protected approval."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

REPO = 'Bonzar/craft'
API = 'https://api.github.com'
APPROVAL_ENV = 'assertion-memory-production'


def safe(pattern, value):
    if not re.fullmatch(pattern, value):
        raise ValueError('Invalid release identifier')
    return value


def api(path, dest=None):
    token = os.environ.get('GH_TOKEN', '')
    if not token or '\r' in token or '\n' in token:
        raise ValueError('GitHub Actions read token missing')
    # Do not place token in argv; default curl drops Authorization on cross-host redirects.
    # Actions installation tokens are opaque. Accept the RFC 6750 b64token
    # grammar, but not quotes, backslashes or whitespace in curl's config.
    if not re.fullmatch(r'[A-Za-z0-9._~+/\-]+=*', token):
        raise ValueError('Unsupported GitHub token format')
    config = f'header = "Authorization: Bearer {token}"\n'
    args = ['curl','-q','--config','-','--silent','--show-error','--fail',
            '--proto','=https','--proto-redir','=https','--max-time','150',
            '--header','Accept: application/vnd.github+json',
            '--header','X-GitHub-Api-Version: 2022-11-28']
    if dest is not None:
        args += ['--location','--output',str(dest)]
    result = subprocess.run(args + [API+path], input=config.encode(), check=True, capture_output=True)
    return json.loads(result.stdout) if dest is None else None


def tested_approval(history):
    return any(isinstance(entry, dict) and entry.get('state') == 'approved'
               and (entry.get('user') or {}).get('type') == 'User'
               and any(env.get('name') == APPROVAL_ENV for env in (entry.get('environments') or []))
               for entry in history)


def unpack_zip(archive, output, revision):
    prefix=f'assertion-memory-qa-{revision}.tar.gz'
    temp=output/'release.tar.gz'
    with zipfile.ZipFile(archive) as z:
        if sorted(z.namelist()) != sorted([prefix,prefix+'.sha256']):
            raise ValueError('Unexpected GitHub release archive members')
        if z.getinfo(prefix).file_size > 100_000_000 or z.getinfo(prefix+'.sha256').file_size > 256:
            raise ValueError('Release archive exceeds limits')
        receipt=z.read(prefix+'.sha256').decode('ascii').strip()
        expected, separator, name=receipt.partition('  ')
        if not separator or name != prefix:
            raise ValueError('Inner archive receipt references the wrong file')
        safe(r'[0-9a-f]{64}',expected)
        with z.open(prefix) as src, temp.open('xb') as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    with temp.open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=expected:
            raise ValueError('Inner release digest mismatch')
    return expected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('download','approval'))
    args=p.parse_args()
    if os.environ['GITHUB_REPOSITORY'] != REPO:
        raise ValueError('Repository is not allowlisted')
    run=safe(r'[1-9][0-9]{0,19}',os.environ['GITHUB_RUN_ID'])
    if args.mode=='approval':
        approvals=api(f'/repos/{REPO}/actions/runs/{run}/approvals')
        if not tested_approval(approvals):
            raise ValueError('No human approval for the production environment in this run')
        print('Production approval verified.')
        return
    revision=safe(r'[0-9a-f]{40}',os.environ['GITHUB_SHA'])
    attempt=safe(r'[1-9][0-9]{0,7}',os.environ['GITHUB_RUN_ATTEMPT'])
    archive_id=safe(r'[1-9][0-9]{0,19}',os.environ['ARTIFACT_ID'])
    outer_digest=safe(r'[0-9a-f]{64}',os.environ['ARTIFACT_ZIP_SHA256'])
    output=Path(os.environ['RELEASE_DOWNLOAD_DIR'])
    output.mkdir(mode=0o700)
    artifact=api(f'/repos/{REPO}/actions/artifacts/{archive_id}')
    if (str(artifact.get('id'))!=archive_id
            or artifact.get('name')!=f'assertion-memory-release-{revision}-attempt-{attempt}'
            or artifact.get('expired') is not False
            or artifact.get('digest')!='sha256:'+outer_digest
            or str(artifact.get('workflow_run',{}).get('id'))!=run
            or artifact.get('workflow_run',{}).get('head_sha')!=revision):
        raise ValueError('Artifact is not the exact build output of this release run')
    if not 0 < artifact.get('size_in_bytes', 0) <= 110_000_000:
        raise ValueError('GitHub artifact exceeds bounds')
    archive=output/'github.zip'
    api(f'/repos/{REPO}/actions/artifacts/{archive_id}/zip',archive)
    with archive.open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=outer_digest:
            raise ValueError('Downloaded immutable artifact digest mismatch')
    inner=unpack_zip(archive,output,revision)
    with Path(os.environ['GITHUB_OUTPUT']).open('a',encoding='utf-8') as f:
        f.write('release_sha256='+inner+'\n')

if __name__=='__main__':
    main()
