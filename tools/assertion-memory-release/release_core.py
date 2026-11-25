"""One shared artifact-qualification and activation path for TEST and PROD."""
from dataclasses import dataclass
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import logging
import tarfile
import tempfile
from typing import Callable

SHA256 = re.compile(r'[a-f0-9]{64}\Z')
RELEASE_NAME = re.compile(r'(?:[a-f0-9]{40}-)?[a-f0-9]{64}\Z')
PENDING = '.activation-pending-v1'


@dataclass(frozen=True)
class ArchiveSpec:
    tar_mode: str
    max_archive_bytes: int
    max_expanded_bytes: int
    max_members: int
    accept_member: Callable[[tarfile.TarInfo, PurePosixPath], bool]
    allow_dirs: bool


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

def record_activation(root,old,release):
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
        os.replace(pending,root/PENDING)
        sync_dir(root)

def recover_activation(root,script,hook):
    journal=root/PENDING
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

def activate(root,release,script,hook):
    old=current_target(root)
    record_activation(root,old,release)
    try:
        swap(root,release)
        sync_dir(root)
        hook(script,'restart',release)
        hook(script,'smoke',release)
    except BaseException:
        recover_activation(root,script,hook)
        raise
    (root/PENDING).unlink()
    sync_dir(root)


def unpack(stream, destination, expected_sha, spec):
    if not isinstance(expected_sha,str) or not SHA256.fullmatch(expected_sha):
        raise ValueError('archive_digest')
    if spec.tar_mode not in ('r|','r|gz'):
        raise ValueError('invalid_tar_mode')
    archive_path=destination/'archive'
    digest=hashlib.sha256()
    size=0
    with archive_path.open('xb') as archive:
        while chunk:=stream.read(1024*1024):
            size+=len(chunk)
            if size>spec.max_archive_bytes:
                raise ValueError('archive_size')
            digest.update(chunk)
            archive.write(chunk)
    if not hmac.compare_digest(digest.hexdigest(),expected_sha):
        raise ValueError('archive_digest')
    staging=destination/'payload'
    staging.mkdir(mode=0o750)
    count=total=0
    seen=set()
    with tarfile.open(archive_path,mode=spec.tar_mode) as tar:
        for item in tar:
            count+=1
            if count>spec.max_members:
                raise ValueError('member_count')
            if item.isdir() and item.name in ('.','./') and spec.allow_dirs:
                continue
            path=PurePosixPath(item.name)
            if (path.is_absolute() or not path.parts or any(s in ('.','..') for s in path.parts)
                    or path in seen or any(not re.fullmatch(r'[A-Za-z0-9_.+@ -]{1,180}',s) for s in path.parts)):
                raise ValueError('member_path')
            if not spec.accept_member(item,path):
                raise ValueError('member_policy')
            seen.add(path)
            if not (item.isfile() or (spec.allow_dirs and item.isdir())) or getattr(item,'sparse',None) is not None:
                raise ValueError('member_type')
            target=staging.joinpath(*path.parts)
            if item.isdir():
                target.mkdir(parents=True,exist_ok=True,mode=0o750)
                continue
            if item.size<0:
                raise ValueError('member_size')
            total+=item.size
            if total>spec.max_expanded_bytes:
                raise ValueError('expanded_size')
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
            with target.open('xb') as out, tar.extractfile(item) as incoming:
                shutil.copyfileobj(incoming,out,1024*1024)
                if out.tell()!=item.size:
                    raise ValueError('member_size')
                out.flush()
                os.fsync(out.fileno())
            target.chmod(0o750 if item.mode&0o111 else 0o640)
    if not seen:
        raise ValueError('empty_archive')
    return staging


def prepare(payload, release, root, hook, retained):
    marker=release/'.prepared-v1'
    if release.is_symlink() or (release.exists() and not release.is_dir()):
        raise ValueError('invalid_staged_release')
    if release.exists():
        if is_prepared(release):
            if retained is not None:
                retained(payload,release)
            return
        prior=current_target(root)
        if prior is not None and Path(prior).resolve().is_relative_to(release.resolve()):
            raise ValueError('unverified_current_release')
        shutil.rmtree(release)
    os.rename(payload,release)
    hook('prepare',release)
    with tempfile.TemporaryDirectory(prefix='.prepare-',dir=root) as tmp:
        pending=Path(tmp)/'ok'
        with pending.open('xb') as out:
            out.write(b'ok\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(pending,marker)
        sync_dir(release)
        sync_dir(release.parent)


def prune_inactive_base(root, active, prove_inactive):
    """Only Base payloads, under the caller's lane activation lock; no state paths."""
    if root.resolve(strict=True)!=root:
        raise ValueError('retention_noncanonical_root')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('retention_requires_safe_rmtree')
    if current_target(root)!=str(active) or checked_release(root,active.name)!=active:
        raise ValueError('retention_current_unverified')
    try:
        (root/PENDING).lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError('retention_pending_activation')
    fd=os.open(root/'releases',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if not os.path.samestat(os.fstat(fd),(root/'releases').lstat()):
            raise ValueError('retention_release_directory_changed')
        for name in sorted(os.listdir(fd)):
            if name==active.name or not re.fullmatch(r'[a-f0-9]{40}-[a-f0-9]{64}',name):
                continue
            entry=os.stat(name,dir_fd=fd,follow_symlinks=False)
            path=root/'releases'/name
            if (not stat.S_ISDIR(entry.st_mode) or entry.st_uid!=os.geteuid()
                    or entry.st_dev!=os.fstat(fd).st_dev or not is_prepared(path)):
                continue
            if prove_inactive(active,path) is not True:
                raise ValueError('retention_inactivity_unproven')
            if (current_target(root)!=str(active) or not os.path.samestat(entry,os.stat(name,dir_fd=fd,follow_symlinks=False))
                    or not os.path.samestat(os.fstat(fd),(root/'releases').lstat())):
                raise ValueError('retention_identity_changed')
            # FD-relative hardened shutil never follows nested symlinks (venv uses them).
            shutil.rmtree(name,dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def _try_base_retention(root, active, prove):
    if prove is None or active is None:
        return
    try:
        prune_inactive_base(root,Path(active),prove)
    except Exception:
        # A committed healthy runtime must not be reported as rolled back.
        logging.getLogger(__name__).warning('base_release_retention_incomplete')



def deploy(stream, expected_sha, *, root, spec, validate, hook, retained=None, hook_factory=None,prove_inactive=None,after_committed=None):
    """Call after lane-specific authentication. Interpret only validated product artifacts."""
    root=Path(root)
    if not root.is_absolute() or root.is_symlink() or (root/'releases').is_symlink():
        raise ValueError('untrusted_release_root')
    lockpath=root/'deploy.lock'
    fd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'rb+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        dispatch=hook if hook_factory is None else hook_factory(lock.fileno())
        if dispatch is None:
            raise ValueError('missing_release_hook')
        runner=lambda _script,action,location: dispatch(action,location)
        recover_activation(root,None,runner)
        with tempfile.TemporaryDirectory(prefix='.incoming-',dir=root) as temp:
            staged=unpack(stream,Path(temp),expected_sha,spec)
            release_id=validate(staged)
            if not isinstance(release_id,str) or not RELEASE_NAME.fullmatch(release_id):
                raise ValueError('invalid_release_name')
            target=root/'releases'/release_id
            prepare(staged,target,root,dispatch,retained)
            try:
                activate(root,target,None,runner)
            except Exception:
                # Only attempt housekeeping after the existing restore+smoke
                # cleared its journal. If restore failed it is kept for recovery.
                if prove_inactive is not None:
                    try:
                        active=current_target(root)
                    except (OSError,ValueError):
                        active=None
                    _try_base_retention(root,active,prove_inactive)
                raise
            _try_base_retention(root,target,prove_inactive)
            if after_committed is not None:
                try:
                    after_committed(root,target)
                except Exception:
                    logging.getLogger(__name__).warning('release_post_commit_housekeeping_incomplete')
    return release_id
