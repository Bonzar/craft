"""Source binding from the clean protected builder, never executable payload."""
from hashlib import file_digest
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError

from base_rebuild_proof import ROOT, WORKFLOW, need, verify_source_selection

BUNDLE = 'assertion-memory-base-reconstructed.tar.gz'
BINDING = 'base-source-binding.json'


def github_get(path):
    result = subprocess.run(['gh', 'api', path], text=True, capture_output=True)
    if result.returncode:
        if '(HTTP 404)' in result.stderr:
            raise HTTPError(path, 404, 'Not found', None, None)
        raise ValueError('GitHub metadata request failed')
    return json.loads(result.stdout)


def absent(get, path):
    try:
        get(path)
    except HTTPError as exc:
        if exc.code == 404:
            return
        raise
    raise ValueError('Version or ref already exists')


def binding(d, source, tag, control, run):
    archive = d / BUNDLE
    need(archive.is_file() and not archive.is_symlink()
         and 0 < archive.stat().st_size <= 64*1024*1024, 'Malformed candidate')
    with archive.open('rb') as stream:
        sha = file_digest(stream, 'sha256').hexdigest()
    return dict(schema=1, payload_sha=source, control_sha=control,
                tag=tag, archive_digest='sha256:'+sha, run_id=run)


def verified_certificate(path, sha):
    args = ['gh', 'attestation', 'verify', str(path), '--repo', 'Bonzar/craft',
            '--signer-workflow', 'Bonzar/craft/'+WORKFLOW,
            '--source-ref', 'refs/heads/main', '--source-digest', sha,
            '--deny-self-hosted-runners']
    subprocess.run(args, check=True)


def main():
    cmd = sys.argv[1]
    env = os.environ
    sha, run = env['GITHUB_SHA'], env['GITHUB_RUN_ID']
    if cmd in ('select', 'bind'):
        source, tag = env['SOURCE_COMMIT'], env['NEW_TAG']
        verify_source_selection(github_get, control_sha=sha, payload_sha=source, tag=tag)
    if cmd == 'select':
        absent(github_get, ROOT+'/releases/tags/'+tag)
        absent(github_get, ROOT+'/git/ref/tags/'+tag)
        with open(env['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write(f'source={source}\ntag={tag}\n')
    elif cmd == 'bind':
        d = Path(sys.argv[2])
        need({p.name for p in d.iterdir()} == {BUNDLE}, 'Unexpected candidate members')
        with (d/BINDING).open('x', encoding='utf-8') as stream:
            json.dump(binding(d, source, tag, sha, run), stream, sort_keys=True)
    elif cmd == 'verify':
        d = Path(sys.argv[2])
        need({p.name for p in d.iterdir()} == {BUNDLE, BINDING}, 'Unexpected proof members')
        for p in (d/BUNDLE, d/BINDING):
            need(p.is_file() and not p.is_symlink(), 'Malformed proof file')
            verified_certificate(p, sha)
        need((d/BINDING).stat().st_size <= 4096, 'Binding too large')
        got = json.loads((d/BINDING).read_text())
        verified = binding(d, got['payload_sha'], got['tag'], sha, run)
        need(got == verified, 'Binding does not match archive/control/run')
        verify_source_selection(github_get, control_sha=sha,
                                payload_sha=got['payload_sha'], tag=got['tag'])
    else:
        raise ValueError('Unknown verification phase')


if __name__ == '__main__':
    main()
