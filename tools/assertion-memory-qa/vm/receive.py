#!/usr/bin/python3
"""Environment-scoped release receiver. No shells or remote arguments executed."""
import fcntl, hashlib, hmac, json, os, re, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path, PurePosixPath

MAX_BYTES = 96 * 1024**2
MAX_EXPANDED = 350 * 1024**2
MAX_FILES = 20_000
COMMAND = re.compile(r'deploy-release (test|production) ([a-f0-9]{40}) ([a-f0-9]{64})')
ROOT = Path('/srv/assertion-release')
CONFIG = Path('/etc/assertion-release')
RECEIPT = dict(status='rejected')
RELEASE_NAME = re.compile(r'[a-f0-9]{40}-[a-f0-9]{64}')
TEST_PENDING = '.activation-pending-v1'


def stage(stream,destination,expected):
    source=destination/'incoming.tar.gz'
    digest=hashlib.sha256()
    size=0
    with source.open('xb') as out:
        for chunk in iter(lambda: stream.read(1024*1024),b''):
            size+=len(chunk)
            if size>MAX_BYTES: raise ValueError('archive_size')
            digest.update(chunk)
            out.write(chunk)
    if not hmac.compare_digest(digest.hexdigest(),expected): raise ValueError('archive_digest')
    release=destination/'release'
    release.mkdir(mode=0o750)
    total=0
    count=0
    seen=set()
    with tarfile.open(source,mode='r|gz') as archive:
        for entry in archive:
            count+=1
            if count>MAX_FILES: raise ValueError('member_count')
            if entry.isdir() and entry.name in ('.','./'):
                continue
            path=PurePosixPath(entry.name)
            if (path.is_absolute() or not path.parts or any(p in {'.','..'} or (p.startswith('.') and p!='.gitignore') for p in path.parts)
                    or any(not re.fullmatch(r'[A-Za-z0-9_.+@ -]{1,180}',p) for p in path.parts)
                    or str(path) in seen):
                raise ValueError('member_path')
            if any(p.endswith(('.db','.sqlite','.sqlite3','.pem','.key','.db-wal','.sqlite3-wal','.env'))
                    or p in {'__pycache__','node_modules','secrets'} for p in path.parts):
                raise ValueError('member_private')
            seen.add(str(path))
            target=release.joinpath(*path.parts)
            if entry.isdir():
                target.mkdir(parents=True,exist_ok=True,mode=0o750)
                continue
            if not entry.isfile() or getattr(entry,'sparse',None) is not None: raise ValueError('member_type')
            total+=entry.size
            if total>MAX_EXPANDED: raise ValueError('expanded_size')
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
            with target.open('xb') as out:
                with archive.extractfile(entry) as incoming:
                    shutil.copyfileobj(incoming,out,1024*1024)
            target.chmod(0o750 if entry.mode&0o111 else 0o640)
    if not seen: raise ValueError('empty_archive')
    return release


def verify_manifest(payload):
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


def swap(root,destination):
    current=root/'current'
    if destination is None:
        current.unlink(missing_ok=True)
    else:
        with tempfile.TemporaryDirectory(prefix='.link-',dir=root) as temp:
            pointer=Path(temp)/'current'
            pointer.symlink_to(destination)
            os.replace(pointer,current)


def current_target(root):
    current=root/'current'
    if current.exists() and not current.is_symlink(): raise ValueError('invalid_current')
    old=os.readlink(current) if current.is_symlink() else None
    if old is not None and not Path(old).is_relative_to(root/'releases'):
        raise ValueError('invalid_current')
    return old


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def is_prepared(path):
    marker=path/'.prepared-v1'
    return (not marker.is_symlink() and marker.is_file() and marker.stat().st_size==3
            and marker.read_bytes()==b'ok\n')


def checked_release(root,name):
    if not isinstance(name,str) or not RELEASE_NAME.fullmatch(name):
        raise ValueError('invalid_activation_journal')
    path=root/'releases'/name
    if path.is_symlink() or not path.is_dir() or not is_prepared(path):
        raise ValueError('unverified_activation_release')
    return path


def record_test_activation(root,old,release):
    if old is not None and Path(old)!=checked_release(root,Path(old).name):
        raise ValueError('invalid_current')
    checked_release(root,release.name)
    txn={'old':Path(old).name if old is not None else None,'new':release.name}
    with tempfile.TemporaryDirectory(prefix='.activation-',dir=root) as tmp:
        pending=Path(tmp)/'pending'
        with pending.open('x',encoding='utf-8') as out:
            json.dump(txn,out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(pending,root/TEST_PENDING)
        sync_dir(root)


def recover_test_activation(root,script,hook):
    journal=root/TEST_PENDING
    if journal.is_symlink():
        raise ValueError('invalid_activation_journal')
    if not journal.exists():
        return
    if not journal.is_file() or journal.stat().st_size>1024:
        raise ValueError('invalid_activation_journal')
    try:
        txn=json.loads(journal.read_bytes())
        if not isinstance(txn,dict) or txn.keys()!={'old','new'}:
            raise ValueError('invalid_activation_journal')
    except (UnicodeError,json.JSONDecodeError) as exc:
        raise ValueError('invalid_activation_journal') from exc
    candidate=checked_release(root,txn['new'])
    old=None if txn['old'] is None else checked_release(root,txn['old'])
    prior=str(old) if old is not None else None
    if current_target(root) not in (prior,str(candidate)):
        raise ValueError('unexpected_activation_current')
    swap(root,old)
    sync_dir(root)
    if old is not None:
        hook(script,'restore',old)
        hook(script,'smoke',old)
    else:
        hook(script,'stop',candidate)
    journal.unlink()
    sync_dir(root)


def activate_test(root,release,script,hook):
    old=current_target(root)
    record_test_activation(root,old,release)
    try:
        swap(root,release)
        sync_dir(root)
        hook(script,'restart',release)
        hook(script,'smoke',release)
    except BaseException:
        recover_test_activation(root,script,hook)
        raise
    (root/TEST_PENDING).unlink()
    sync_dir(root)


def prepare_test(payload,release,root,script,hook):
    marker=release/'.prepared-v1'
    if release.is_symlink() or (release.exists() and not release.is_dir()):
        raise ValueError('invalid_staged_release')
    if release.exists():
        if is_prepared(release):
            return
        old=current_target(root)
        if old is not None and Path(old).resolve().is_relative_to(release.resolve()):
            # Legacy or interrupted release is current: never mutate its files in place.
            raise ValueError('unverified_current_release')
        shutil.rmtree(release)
    os.rename(payload,release)
    # Preparation uses the final absolute path (the venv is not relocatable).
    hook(script,'prepare',release)
    with tempfile.TemporaryDirectory(prefix='.prepare-',dir=root) as tmp:
        pending=Path(tmp)/'prepared'
        with pending.open('xb') as out:
            out.write(b'ok\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(pending,marker)
        sync_dir(release)


def deploy(environment, revision, sha256, stream, *, root=None, config=None, hook=None):
    root=ROOT/environment if root is None else root
    config=CONFIG/environment if config is None else config
    release=root/'releases'/f'{revision}-{sha256}'
    script=config/'service-hook.sh'
    if not script.is_file(): raise ValueError('missing_hook')
    with (root/'deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if hook is None:
            # An orphaned hook must finish before the next receiver cleans its directory.
            def hook(script,action,location):
                run_hook(script,action,location,lock_fd=lock.fileno())
        if environment=='test':
            recover_test_activation(root,script,hook)
        with tempfile.TemporaryDirectory(prefix='.incoming-',dir=root) as scratch:
            payload=stage(stream,Path(scratch),sha256)
            if (payload/'prototypes/assertion_memory/http_server.py').is_file():
                app=payload
            elif (payload/'source/prototypes/assertion_memory/http_server.py').is_file():
                app=payload/'source'
            else:
                raise ValueError('source_layout')
            if not (app/'apps/graph_viewer/web/dist/index.html').is_file():
                raise ValueError('compiled_viewer_missing')
            if not (payload/'REVISION').is_file() or (payload/'REVISION').read_text().strip()!=revision:
                raise ValueError('revision_mismatch')
            if not (payload/'QA_PROFILE').is_file() or (payload/'QA_PROFILE').read_text().strip()!='assertion-memory-synthetic-qa-v1':
                raise ValueError('release_profile')
            verify_manifest(payload)
            if environment=='test':
                prepare_test(payload,release,root,script,hook)
                activate_test(root,release,script,hook)
                return 'ok'
            else:
                if not release.exists():
                    os.rename(payload,release)
                gate=config/'allow-activation'
                # No hook/dependencies/source execution on an unapproved production stage.
                if not gate.is_file() or gate.is_symlink() or gate.stat().st_uid != 0 or gate.stat().st_mode&0o022:
                    return 'staged'
                if gate.read_text().strip()!=f'{revision} {sha256}':
                    return 'staged'
                hook(script,'prepare',release)
            old=current_target(root)
            swap(root,release)
            try:
                hook(script,'restart',release)
                hook(script,'smoke',release)
            except BaseException:
                swap(root,old)
                hook(script,'restore' if old is not None else 'stop',Path(old) if old else release)
                raise
        return 'ok'


def main():
    try:
        import pwd
        if len(sys.argv)!=2 or sys.argv[1] not in ('test','production'):raise ValueError('expected_environment')
        env=sys.argv[1]
        if pwd.getpwuid(os.getuid()).pw_name != {'test':'assertion-test-deploy','production':'assertion-production-deploy'}[env]:
            raise ValueError('wrong_account')
        match=COMMAND.fullmatch(os.environ.get('SSH_ORIGINAL_COMMAND',''))
        if not match or match[1]!=env:raise ValueError('command_denied')
        _,revision,sha=match.groups()
        status=deploy(env,revision,sha,sys.stdin.buffer)
        print(json.dumps(dict(status=status,revision=revision,sha256=sha,environment=env),separators=(',',':')))
    except Exception as exc:
        code=str(exc) if isinstance(exc,(ValueError,RuntimeError)) and re.fullmatch(r'[a-z_]+',str(exc)) else 'deployment_failed'
        print(json.dumps(dict(status='rejected',error=code),separators=(',',':')))
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
