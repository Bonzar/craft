"""Authorizer-only exact state record CAS; not an exposed endpoint."""
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import secrets
import stat

DIRECTORY=Path('/var/lib/assertion-configured-base-prod-auth')
AUTHORIZER_UID=980
STATE='base-terminal-witness.v1'
LOCK='base-terminal-witness.lock'
LIMIT=32768

def _regular(fd,uid):
    s=os.fstat(fd)
    if (not stat.S_ISREG(s.st_mode) or s.st_uid!=uid or s.st_nlink!=1
            or stat.S_IMODE(s.st_mode)!=0o600):
        raise ValueError('untrusted authorizer witness file')
    return s

def _current(root,uid):
    try:
        fd=os.open(STATE,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=root)
    except FileNotFoundError:
        return None
    with os.fdopen(fd,'rb') as f:
        st=_regular(f.fileno(),uid)
        if not os.path.samestat(st,os.stat(STATE,dir_fd=root,follow_symlinks=False)):
            raise ValueError('authorizer state changed')
        data=f.read(LIMIT+1)
        if not 0<len(data)<=LIMIT:
            raise ValueError('invalid authorizer record size')
        return data

def _write(path,uid,expected,replacement):
    if (type(replacement) is not bytes or not 0<len(replacement)<=LIMIT
        or expected is not None and (type(expected) is not bytes or not 0<len(expected)<=LIMIT)
        or replacement==expected):
        raise ValueError('invalid exact authorizer compare-and-set')
    if path.resolve(strict=True)!=path:
        raise ValueError('noncanonical authorizer state directory')
    root=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(root)
        if (info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o700
                or not os.path.samestat(info,path.lstat())):
            raise ValueError('untrusted authorizer state directory')
        lock=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,
                     0o600,dir_fd=root)
        try:
            st=_regular(lock,uid)
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if not os.path.samestat(st,os.stat(LOCK,dir_fd=root,follow_symlinks=False)):
                raise ValueError('authorizer lock changed')
            current=_current(root,uid)
            if current!=expected:
                raise ValueError('authorizer state CAS rejected')
            staged='.base-terminal-'+secrets.token_hex(16)
            fd=os.open(staged,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                       0o600,dir_fd=root)
            try:
                with os.fdopen(fd,'wb') as f:
                    _regular(f.fileno(),uid)
                    f.write(replacement)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(staged,STATE,src_dir_fd=root,dst_dir_fd=root)
                os.fsync(root)
            finally:
                try: os.unlink(staged,dir_fd=root)
                except FileNotFoundError: pass
            return sha256(replacement).hexdigest()
        finally:
            os.close(lock)
    finally:
        os.close(root)

def publish(*,expected,replacement):
    """Only internal verified authorizer transition code may call this."""
    if os.getuid()!=AUTHORIZER_UID or os.geteuid()!=AUTHORIZER_UID:
        raise ValueError('wrong protected authorizer identity')
    return _write(DIRECTORY,AUTHORIZER_UID,expected,replacement)
