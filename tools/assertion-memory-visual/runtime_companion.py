"""Source-only TEST Visual runtime companion. No signing key or host access."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import zipfile

RUNTIME_FILES = ('asset-origin.py', 'sites-shell.mjs')
SCOPE = 'visual-runtime-test-v1'
TAG = re.compile(r'assertion-memory-visual-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?')
SHA = re.compile(r'[a-f0-9]{40}')
DIGEST = re.compile(r'[a-f0-9]{64}')
ID = re.compile(r'[1-9][0-9]{0,19}')
MAX_FILE = 64_000
MAX_ZIP = 140_000


def digest(b):
    return sha256(b).hexdigest()


def wire(obj):
    return (json.dumps(obj, separators=(',', ':'), sort_keys=True, allow_nan=False)+'\n').encode('ascii')


def identity(tag, revision, release_id, build_run_id, static_sha):
    fields=(tag, revision, release_id, build_run_id, static_sha)
    shapes=(TAG, SHA, ID, ID, DIGEST)
    if not all(type(v) is str and pat.fullmatch(v) for v,pat in zip(fields,shapes)):
        raise ValueError('Invalid exact Visual producer identity')
    return dict(zip(('tag','revision','release_id','build_run_id','static_sha'),fields))


def artifact_name(expected):
    return f"assertion-memory-visual-runtime-{expected['release_id']}-{expected['revision']}"


def signing_message(expected, archive_sha):
    """A distinct TEST namespace/message; does not grant the legacy receiver authority."""
    identity(**expected)
    if type(archive_sha) is not str or not DIGEST.fullmatch(archive_sha):
        raise ValueError('Invalid runtime digest')
    return SCOPE.encode()+b' '+wire({**expected,'runtime_sha':archive_sha})



def sign(expected, archive_sha, signing_key):
    """Use only the existing protected TEST Visual signing key on the runner."""
    message=signing_message(expected,archive_sha)
    with TemporaryDirectory(prefix='visual-runtime-sign-') as tmp:
        key=Path(tmp)/'identity'
        proof=Path(tmp)/'manifest'
        key.touch(mode=0o600)
        key.write_text(signing_key.strip()+'\n',encoding='ascii')
        proof.write_bytes(message)
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key),'-n',SCOPE,str(proof)],
                       check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        signature=proof.with_suffix('.sig').read_bytes()
    if not 0<len(signature)<=4096:
        raise ValueError('Invalid TEST Visual signature size')
    return signature


def verify_signed(raw, expected, archive_sha, signature, signers):
    """Receiver invokes with its already pinned root-owned TEST Visual signer policy."""
    if type(signature) is not bytes or not 0<len(signature)<=4096:
        raise ValueError('Invalid TEST Visual signature size')
    message=signing_message(expected,archive_sha)
    with TemporaryDirectory(prefix='visual-runtime-proof-') as tmp:
        proof=Path(tmp)/'signature'
        proof.write_bytes(signature)
        result=subprocess.run(['/usr/bin/ssh-keygen','-Y','verify','-f',str(signers),'-I','visual-test',
                               '-n',SCOPE,'-s',str(proof)],input=message,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,timeout=10)
    if result.returncode:
        raise ValueError('Untrusted TEST Visual runtime signature')
    return verify(raw,expected,archive_sha)


def build(expected, files):
    identity(**expected)
    if set(files) != set(RUNTIME_FILES) or any(type(v) is not bytes or not 0<len(v)<=MAX_FILE
                                               for v in files.values()):
        raise ValueError('Invalid Visual runtime members')
    receipt={'scope':SCOPE, 'identity':expected, 'files':{name:digest(data) for name,data in files.items()}}
    out=BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:
        for name,data in sorted({**files,'receipt.json':wire(receipt)}.items()):
            member=zipfile.ZipInfo(name)
            member.create_system=3
            member.external_attr=0o100644 << 16
            z.writestr(member,data)
    return out.getvalue()


def verify(raw, expected, archive_sha):
    signing_message(expected,archive_sha)
    if type(raw) is not bytes or not 0<len(raw)<=MAX_ZIP or digest(raw)!=archive_sha:
        raise ValueError('Wrong Visual runtime archive')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            members=z.infolist()
            if (len(members)!=3 or {m.filename for m in members} != {*RUNTIME_FILES,'receipt.json'}
                    or any(not 0<m.file_size<=(4096 if m.filename=='receipt.json' else MAX_FILE)
                           or m.is_dir() or m.external_attr>>16!=0o100644
                           or m.compress_type!=zipfile.ZIP_STORED
                           or m.flag_bits & 1 for m in members)):
                raise ValueError('Unexpected Visual runtime ZIP members')
            files={name:z.read(name) for name in RUNTIME_FILES}
            receipt=z.read('receipt.json')
    except (zipfile.BadZipFile, KeyError, RuntimeError) as e:
        raise ValueError('Invalid runtime archive') from e
    if receipt!=wire({'scope':SCOPE,'identity':expected,'files':{k:digest(v) for k,v in files.items()}}):
        raise ValueError('Runtime content or producer identity mismatch')
    return files


def verify_provider(meta, downloaded_zip, expected, artifact_id):
    """Consumer preflight; caller resolves expected from the protected deployment controller."""
    identity(**expected)
    if type(artifact_id) is not str or not ID.fullmatch(artifact_id):
        raise ValueError('Invalid companion artifact ID')
    run=meta.get('workflow_run') if type(meta) is dict else None
    if (type(run) is not dict or str(meta.get('id'))!=artifact_id
            or meta.get('name')!=artifact_name(expected) or meta.get('expired') is not False
            or str(run.get('id'))!=expected['build_run_id'] or run.get('head_sha')!=expected['revision']
            or type(downloaded_zip) is not bytes or not 0<len(downloaded_zip)<=2*MAX_ZIP
            or meta.get('digest')!='sha256:'+digest(downloaded_zip)):
        raise ValueError('Untrusted original-run Visual companion')
    try:
        with zipfile.ZipFile(BytesIO(downloaded_zip)) as z:
            members=z.infolist()
            if (len(members)!=2 or {m.filename for m in members}!={'runtime.zip','runtime.sha256'}
                    or any(m.is_dir() or not 0<m.file_size<=(65 if m.filename=='runtime.sha256' else MAX_ZIP)
                           for m in members)):
                raise ValueError('Unexpected provider archive members')
            raw=z.read('runtime.zip')
            digest_line=z.read('runtime.sha256').decode('ascii')
    except (zipfile.BadZipFile, RuntimeError, KeyError) as e:
        raise ValueError('Invalid provider archive') from e
    if not digest_line.endswith('\n'):
        raise ValueError('Missing runtime digest')
    sha=digest_line[:-1]
    return verify(raw,expected,sha),sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for field in ('tag','revision','release-id','build-run-id','static-sha'):
        p.add_argument('--'+field,required=True)
    p.add_argument('--out',type=Path,required=True)
    opts=vars(p.parse_args())
    out=opts.pop('out')
    expected=identity(**opts)
    rev=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if expected['revision']!=rev:
        raise ValueError('Producer checkout is not original release commit')
    files={}
    relative=Path('tools/assertion-memory-visual')
    for name in RUNTIME_FILES:
        path=relative/name
        tracked=subprocess.check_output(['git','show',rev+':'+path.as_posix()])
        if not path.is_file() or path.is_symlink() or tracked!=path.read_bytes():
            raise ValueError('Runtime source differs from original release commit')
        files[name]=tracked
    raw=build(expected,files)
    out.mkdir(mode=0o700)
    (out/'runtime.zip').write_bytes(raw)
    (out/'runtime.sha256').write_bytes(digest(raw).encode()+b'\n')


if __name__=='__main__':
    main()
