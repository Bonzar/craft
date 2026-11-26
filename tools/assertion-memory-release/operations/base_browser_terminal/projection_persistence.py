"""Scoped durable UID980 projection, sharing the existing authorizer lease."""
import fcntl
import json
import os
import secrets
import stat
from hashlib import sha256

from authorizer_cas import DIRECTORY,AUTHORIZER_UID,LOCK,_current,_regular
from configured_terminal_state import encode,read_exact
from terminal_projection import _identity,_event,_advance

MAX=1024

def _name(run,nonce):
    _identity({'run':run,'nonce':nonce})
    return 'base-terminal-projection-'+sha256((run+':'+nonce).encode('ascii')).hexdigest()+'.json'

def _open():
    if os.getuid()!=AUTHORIZER_UID or os.geteuid()!=AUTHORIZER_UID:
        raise ValueError('scoped authorizer projection only')
    if DIRECTORY.resolve(strict=True)!=DIRECTORY:
        raise ValueError('noncanonical configured authorizer directory')
    fd=os.open(DIRECTORY,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    s=os.fstat(fd)
    if (s.st_uid!=AUTHORIZER_UID or stat.S_IMODE(s.st_mode)!=0o700
            or not os.path.samestat(s,DIRECTORY.lstat())):
        os.close(fd);raise ValueError('untrusted configured authorizer directory')
    return fd

def _get(fd,name):
    try: item=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=fd)
    except FileNotFoundError:return None
    with os.fdopen(item,'rb') as stream:
        s=_regular(stream.fileno(),AUTHORIZER_UID)
        if not os.path.samestat(s,os.stat(name,dir_fd=fd,follow_symlinks=False)):
            raise ValueError('authorizer projection inode changed')
        data=stream.read(MAX+1)
    if not 0<len(data)<=MAX:
        raise ValueError('oversize configured authorizer projection')
    parsed=json.loads(data)
    if type(parsed) is not dict or encode(parsed)!=data:
        raise ValueError('noncanonical configured authorizer projection')
    return _event(parsed)

def read_projection(run,nonce):
    fd=_open()
    try:return _get(fd,_name(run,nonce))
    finally:os.close(fd)

def persist_projection(*,expected_admission,expected,replacement,identical):
    replacement=_event(replacement)
    _,admitted,_=read_exact(expected_admission)
    run,nonce=_identity(replacement)
    if (type(identical) is not bool or admitted is None or
            (admitted.run,admitted.nonce)!=(run,nonce)
            or _advance(expected,replacement)==identical):
        raise ValueError('inconsistent exact projection CAS')
    fd=_open()
    try:
        lock=os.open(LOCK,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        try:
            before=_regular(lock,AUTHORIZER_UID)
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if not os.path.samestat(before,os.stat(LOCK,dir_fd=fd,follow_symlinks=False)):
                raise ValueError('configured authorizer lock changed')
            if _current(fd,AUTHORIZER_UID)!=expected_admission:
                raise ValueError('configured admission CAS changed')
            name=_name(run,nonce)
            if _get(fd,name)!=expected:
                raise ValueError('configured projection CAS changed')
            if identical:
                os.fsync(fd)
                return
            staged='.terminal-projection-'+secrets.token_hex(16)
            item=os.open(staged,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                         0o600,dir_fd=fd)
            try:
                with os.fdopen(item,'wb') as stream:
                    _regular(stream.fileno(),AUTHORIZER_UID)
                    stream.write(encode(replacement));stream.flush();os.fsync(stream.fileno())
                os.replace(staged,name,src_dir_fd=fd,dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:os.unlink(staged,dir_fd=fd)
                except FileNotFoundError:pass
        finally:os.close(lock)
    finally:os.close(fd)

def read_locked_view(run,nonce):
    """One authoritative snapshot for expected run; do not trust it as a detached permit."""
    fd=_open()
    try:
        lock=os.open(LOCK,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        try:
            before=_regular(lock,AUTHORIZER_UID)
            fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
            if not os.path.samestat(before,os.stat(LOCK,dir_fd=fd,follow_symlinks=False)):
                raise ValueError('configured authorizer lock changed')
            state=_current(fd,AUTHORIZER_UID)
            _,pending,_=read_exact(state)
            event=_get(fd,_name(run,nonce)) if pending is not None and (pending.run,pending.nonce)==(run,nonce) else None
            return state,event
        finally:os.close(lock)
    finally:os.close(fd)
