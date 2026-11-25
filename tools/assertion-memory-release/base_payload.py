"""Base archive policy shared by both scopes; no authorization decisions here."""
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import stat
import json
import socket
import struct

from release_core import ArchiveSpec, deploy as common_deploy


def permitted(item, path):
    return not any((part.startswith('.') and part!='.gitignore')
        or part.endswith(('.db','.sqlite','.sqlite3','.pem','.key','.db-wal','.sqlite3-wal','.env'))
        or part in {'__pycache__','node_modules','secrets'} for part in path.parts)


SPEC=ArchiveSpec('r|gz',96*1024**2,350*1024**2,20_000,permitted,True)


def read_manifest(payload):
    manifest=payload/'SHA256SUMS'
    if not manifest.is_file() or manifest.stat().st_size>5*1024**2: raise ValueError('missing_manifest')
    expected={}
    for line in manifest.read_text().splitlines():
        digest,sep,location=line.partition('  ')
        path=PurePosixPath(location)
        if (not sep or not re.fullmatch('[a-f0-9]{64}',digest) or path.is_absolute()
                or '..' in path.parts or not path.parts or path in expected):
            raise ValueError('manifest_invalid')
        expected[path]=digest
    return expected


def verify_manifest(payload):
    manifest=payload/'SHA256SUMS'
    expected=read_manifest(payload)
    actual={PurePosixPath(path.relative_to(payload)) for path in payload.rglob('*')
            if path!=manifest and path.is_file()}
    if actual!=expected.keys(): raise ValueError('manifest_membership')
    for name,digest in expected.items():
        with (payload/name).open('rb') as stream:
            if not hmac.compare_digest(hashlib.file_digest(stream,'sha256').hexdigest(),digest):
                raise ValueError('manifest_digest')

def run_hook(script,action,location,*,lock_fd):
    result=subprocess.run(['/bin/bash',str(script),action,str(location)],
        cwd=location,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':os.environ['HOME'],
          'XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}','LANG':'C.UTF-8'},pass_fds=(lock_fd,),timeout=300)
    if result.returncode!=0: raise RuntimeError(action)

def validate(staged, revision, digest):
    standard=staged/'prototypes/assertion_memory/http_server.py'
    app=staged if standard.is_file() else staged/'source'
    if not (app/'prototypes/assertion_memory/http_server.py').is_file():
        raise ValueError('source_layout')
    if not (app/'apps/graph_viewer/web/dist/index.html').is_file():
        raise ValueError('compiled_viewer_missing')
    if (staged/'REVISION').read_text().strip()!=revision:
        raise ValueError('revision_mismatch')
    if (staged/'QA_PROFILE').read_text().strip()!='assertion-memory-synthetic-qa-v1':
        raise ValueError('release_profile')
    verify_manifest(staged)
    return f'{revision}-{digest}'


def validate_retained(incoming,release):
    """Recheck original immutable manifest paths; only the prepared venv is mutable."""
    manifest=incoming/'SHA256SUMS'
    baseline=read_manifest(incoming)
    expected=set(baseline)|{PurePosixPath('SHA256SUMS')}
    expected_dirs={d for name in expected for d in name.parents}
    seen=set()
    for directory,dirs,files in os.walk(release,followlinks=False):
        here=Path(directory)
        relative=here.relative_to(release)
        if not relative.parts and 'venv' in dirs:
            # The venv is produced by the independently owned service hook,
            # not part of the raw artifact. Never permit a venv-root symlink.
            if (here/'venv').is_symlink() or PurePosixPath('venv') in expected:
                raise ValueError('retained_release_changed')
            dirs.remove('venv')
        for name in dirs:
            path=here/name
            relative_path=PurePosixPath(path.relative_to(release))
            if path.is_symlink() or relative_path not in expected_dirs:
                # Empty immutable directories are harmless but a directory not
                # in the original archive cannot shadow an expected module.
                if path.is_symlink() or not (incoming/relative_path).is_dir():
                    raise ValueError('retained_release_changed')
        for name in files:
            path=here/name
            relative_path=PurePosixPath(path.relative_to(release))
            if relative_path==PurePosixPath('.prepared-v1'):
                continue
            if relative_path not in expected or path.is_symlink():
                raise ValueError('retained_release_changed')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('retained_release_changed')
                if relative_path==PurePosixPath('SHA256SUMS'):
                    if stream.read(manifest.stat().st_size+1)!=manifest.read_bytes():
                        raise ValueError('retained_release_changed')
                elif not hmac.compare_digest(hashlib.file_digest(stream,'sha256').hexdigest(),baseline[relative_path]):
                    raise ValueError('retained_release_changed')
            seen.add(relative_path)
    if seen!=expected:
        raise ValueError('retained_release_changed')


TEST_RETENTION_ROOT=Path('/srv/assertion-release/test')
TEST_RETENTION_CONFIG=Path('/etc/assertion-release/test')
TEST_RETENTION_SOCKET=Path('/run/assertion-base-test-retention/authority.sock')
TEST_RETENTION_NAME=re.compile(r'[a-f0-9]{40}-[a-f0-9]{64}\Z')


def test_retention_authority(root, config):
    """Default off, only an authenticated host root gate can attest TEST inactivity."""
    if Path(root)!=TEST_RETENTION_ROOT or Path(config)!=TEST_RETENTION_CONFIG:
        return None
    enabled=TEST_RETENTION_CONFIG/'enable-host-retention-v1'
    try:
        marker=enabled.lstat()
    except FileNotFoundError:
        return None
    if (enabled.resolve(strict=True)!=enabled or marker.st_uid!=0 or marker.st_gid!=0
            or not stat.S_ISREG(marker.st_mode) or stat.S_IMODE(marker.st_mode)!=0o644
            or marker.st_nlink!=1 or marker.st_size!=0):
        raise ValueError('unsafe_test_retention_switch')

    def bind(lock_fd):
        def proof(active,inactive):
            endpoint=TEST_RETENTION_SOCKET
            parent=endpoint.parent.lstat()
            entry=endpoint.lstat()
            if (endpoint.parent.resolve(strict=True)!=endpoint.parent or parent.st_uid!=0
                    or not stat.S_ISDIR(parent.st_mode) or parent.st_mode&0o022
                    or entry.st_uid!=0 or not stat.S_ISSOCK(entry.st_mode)):
                return False
            names=(active.name,inactive.name)
            if (names[0]==names[1] or not all(TEST_RETENTION_NAME.fullmatch(n) for n in names)
                    or any(p.parent!=TEST_RETENTION_ROOT/'releases' for p in (active,inactive))):
                return False
            request=json.dumps({'version':1,'lane':'test','active':names[0],'inactive':names[1]},
                               separators=(',',':')).encode('ascii')+b'\n'
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
                conn.settimeout(5)
                conn.connect(str(endpoint))
                _,uid,_=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if uid!=0:
                    return False
                # A duplicate of the actual current lock, never an untrusted pathname.
                written=conn.sendmsg([request],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,struct.pack('i',lock_fd))])
                if written!=len(request):
                    return False
                conn.shutdown(socket.SHUT_WR)
                with conn.makefile('rb') as response:
                    return response.read(64)==b'assertion-base-test-retention-v1 OK\n'
        return proof
    return bind


def deploy(stream, revision, digest, *, root, config, hook=None, prove_inactive=None):
    if not re.fullmatch(r'[0-9a-f]{40}',revision):
        raise ValueError('invalid_release_revision')
    checked_hook=Path(config)/'service-hook.sh'
    if hook is not None:
        factory=None
    else:
        data=checked_hook.lstat()
        if not stat.S_ISREG(data.st_mode) or data.st_uid!=0 or data.st_mode&0o022:
            raise ValueError('untrusted_hook')
        def factory(fd):
            return lambda action,release: run_hook(checked_hook,action,release,lock_fd=fd)
    authority=None if hook is not None or prove_inactive is not None else test_retention_authority(root,config)
    return common_deploy(stream,digest,root=root,spec=SPEC,
        validate=lambda staged:validate(staged,revision,digest),hook=hook,retained=validate_retained,hook_factory=factory,prove_inactive=prove_inactive,retention_factory=authority)
