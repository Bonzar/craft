"""Visual TEST origin runtime switch seam; no system commands or public listener."""
import fcntl
import os
from pathlib import Path
import re
import stat
import tempfile

DIGEST = re.compile(r'[0-9a-f]{64}')
ENTRY = 'asset-origin.py'
PENDING = '.runtime-transition'


def sync_directory(root):
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def target(root, revision):
    if type(revision) is not str or not DIGEST.fullmatch(revision):
        raise ValueError('Invalid Visual runtime digest')
    path=root/'versions'/revision
    if (path.resolve(strict=True)!=path or not path.is_dir()
            or (path/ENTRY).is_symlink() or not (path/ENTRY).is_file()):
        raise ValueError('Visual runtime is not an immutable prepared version')
    return 'versions/'+revision


def move(root, relative):
    with tempfile.TemporaryDirectory(dir=root,prefix='.runtime-link-') as t:
        staged=Path(t)/'current'
        staged.symlink_to(relative)
        os.replace(staged,root/'current')
        sync_directory(root)


def activate(root, revision, *, restart, smoke):
    """Single local TEST transition. Caller provides protected TEST scope and verified bytes.

    The unit always launches current/asset-origin.py. Restart/smoke are narrowly
    bound in host control, never arbitrary CLI input. Any unresolved rollback
    leaves a recovery marker and forbids later transitions.
    """
    root=Path(root)
    if root.resolve(strict=True)!=root:
        raise ValueError('Invalid TEST Visual runtime root')
    path=root/'runtime.lock'
    lock=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_uid!=os.geteuid():
            raise ValueError('Invalid TEST Visual runtime lock')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker=root/PENDING
        if os.path.lexists(marker):
            raise RuntimeError('Visual runtime requires recovery')
        new=target(root,revision)
        old=os.readlink(root/'current')
        if not old.startswith('versions/') or target(root,old.removeprefix('versions/'))!=old:
            raise ValueError('Current Visual runtime is not the same scoped origin')
        # An already active version still needs a service proof.
        if old==new:
            smoke(revision)
            return
        with marker.open('x',encoding='ascii') as pending:
            pending.write(old+'\n'+new+'\n')
            pending.flush()
            os.fsync(pending.fileno())
        sync_directory(root)
        try:
            move(root,new)
            restart()
            smoke(revision)
        except Exception:
            try:
                move(root,old)
                restart()
                smoke(old.removeprefix('versions/'))
            except Exception:
                raise RuntimeError('Visual runtime rollback was not verified') from None
            marker.unlink()
            sync_directory(root)
            raise RuntimeError('Visual runtime activation failed; previous runtime restored') from None
        marker.unlink()
        sync_directory(root)
    finally:
        os.close(lock)
