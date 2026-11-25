"""Stage an approved TEST bundle against the inspected native source. No network calls."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from package_release import HEX, PAUSE_FORMAT, PAUSE_MODULES, SHA, TAG, digest, envelope, valid, wire
from verify_native_receipt import read_bundle

ENTRY = 'preview_visual_entry.mjs'
ANCHOR = b"export default withVisualAtMcpOrigin('test',visualGraph,previous);"
ORIGIN = re.compile(r'https://[a-z0-9-]+\.[a-z0-9-]+\.chatgpt\.site')
GATEWAY = re.compile(r'https://[a-z0-9-]+\.[a-z0-9-]+\.apigw\.yandexcloud\.net/memory-visual-test-pause')


def prepare(bundle, *, archive_sha256, tag, release_sha, observed_acl_sha256,
            observed_entry_sha256, hosting_bytes, entry_bytes, origin, gateway):
    """Compose only the entry and dependency modules for an already owner-proved Site."""
    manifest, files = read_bundle(bundle, archive_sha256)
    if (manifest['version'] != PAUSE_FORMAT or manifest['tag'] != tag or
            manifest['source_sha'] != release_sha or not valid(TAG, tag) or
            not valid(SHA, release_sha)):
        raise ValueError('pause source is not the approved release')
    profile=json.loads(files['profiles/test.json'])
    envelope('test', profile)
    project_id=json.loads(hosting_bytes).get('project_id')
    if (not isinstance(project_id,str) or not project_id or
            digest(project_id.encode()) != profile['project_id_sha256'] or
            not valid(ORIGIN,origin) or digest(origin.encode()) != profile['site_origin_sha256'] or
            not valid(HEX,observed_acl_sha256) or observed_acl_sha256 != profile['access_identity_sha256'] or
            not valid(GATEWAY,gateway)):
        raise ValueError('wrong private TEST Site or ingress scope')
    if (not valid(HEX,observed_entry_sha256) or digest(entry_bytes)!=observed_entry_sha256 or
            entry_bytes.count(ANCHOR)!=1 or b'attachTestPause' in entry_bytes):
        raise ValueError('native source preimage changed')
    adapter = (b"import {attachTestPause} from './v25-bind.mjs';\n"
               b"import {matchesEnrolledOwner} from './test_owner_enrollment.mjs';\n")
    if b'{matchesEnrolledOwner}' in entry_bytes:
        # Avoid repeating an existing import, without assuming a loose rewrite is valid.
        raise ValueError('unexpected native owner import')
    binding = (b"export default attachTestPause({\n"
               b"  legacy: withVisualAtMcpOrigin('test',visualGraph,previous),\n"
               b"  withinEnrolledOwner: matchesEnrolledOwner,\n"
               + f'  origin: {json.dumps(origin)},\n  gateway: {json.dumps(gateway)},\n'.encode()
               + b'});')
    return {ENTRY:adapter+entry_bytes.replace(ANCHOR,binding),
            **{name:files['test/pause/'+name] for name in PAUSE_MODULES}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('bundle','checkout','output'):
        parser.add_argument('--'+field,type=Path,required=True)
    for field in ('archive-sha256','tag','release-sha','native-sha','observed-acl-sha256',
                  'observed-entry-sha256','origin','gateway'):
        parser.add_argument('--'+field,required=True)
    a=parser.parse_args()
    root=a.checkout.resolve(strict=True)
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args])
    if not valid(SHA,a.native_sha) or git('rev-parse','HEAD').decode().strip()!=a.native_sha or git('status','--porcelain=v1'):
        raise ValueError('native checkout is not the clean, independently verified source')
    def source(path):
        p=root/path
        if p.resolve(strict=True)!=p or not p.is_file() or p.stat().st_size>250000:
            raise ValueError('unsafe native source')
        raw=p.read_bytes()
        if raw!=git('show',a.native_sha+':'+path):
            raise ValueError('not the pinned native source')
        return raw
    source('test_owner_enrollment.mjs')
    if any((root/name).exists() or (root/name).is_symlink() for name in PAUSE_MODULES):
        raise ValueError('native pause module already present')
    if a.bundle.is_symlink() or not a.bundle.is_file() or a.bundle.stat().st_size>150000:
        raise ValueError('unsafe bundle')
    prepared=prepare(a.bundle.read_bytes(),archive_sha256=a.archive_sha256,tag=a.tag,
        release_sha=a.release_sha,observed_acl_sha256=a.observed_acl_sha256,
        observed_entry_sha256=a.observed_entry_sha256,hosting_bytes=source('.openai/hosting.json'),
        entry_bytes=source(ENTRY),origin=a.origin,gateway=a.gateway)
    out=a.output.resolve()
    if out.is_relative_to(root):
        raise ValueError('candidate must be outside the inspected native checkout')
    out.mkdir(mode=0o700)
    for name,raw in prepared.items():
        (out/name).write_bytes(raw)
    print(wire({'lane':'test','tag':a.tag,'release_sha':a.release_sha,'native_sha':a.native_sha,
                'archive_sha256':a.archive_sha256,'candidate_files':{key:digest(raw)
                for key,raw in sorted(prepared.items())}}).decode(),end='')


if __name__=='__main__':
    main()
