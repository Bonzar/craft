"""Root-operator foreground phase publication for one preapproved Base run.

No service control, new listener, long-running agent, or generic command.
Call from the pinned operator process, never as the native receiver.
"""
from dataclasses import dataclass
import fcntl
import os
import secrets
import stat

from authorizer_cas import DIRECTORY,AUTHORIZER_UID,_current
from controller_install_lease import TRANSITION,DEPLOY,_lock
from configured_terminal_state import read_exact
from native_boundary import observe_native_boundary
from root_phase_binding import MAX,_safe_parent,document
from root_run_records import record
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

@dataclass(frozen=True)
class ApprovedPair:
    admitted: Admission
    prior: Release

def _require_root():
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('one-run foreground root operator required')

def _read_authorizer():
    path=DIRECTORY
    if path.resolve(strict=True)!=path:
        raise ValueError('noncanonical authorizer state directory')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        if (s.st_uid!=AUTHORIZER_UID or stat.S_IMODE(s.st_mode)!=0o700
                or not os.path.samestat(s,path.lstat())):
            raise ValueError('untrusted authorizer state directory')
        return _current(fd,AUTHORIZER_UID)
    finally:
        os.close(fd)

def planned_phase(pair, *, state, prior_phase, native_observer):
    if type(pair) is not ApprovedPair:
        raise ValueError('exact foreground pair required')
    active,admission,_=read_exact(state)
    if (active,admission)!=(pair.prior,pair.admitted):
        raise ValueError('signed operator pair is no longer admitted')
    selected=[]
    for phase in ('restart','restore'):
        request=TerminalRequest(admission,active,phase)
        try:
            identity,journal=native_observer(request)
        except (OSError,ValueError):
            continue
        expected=admission.candidate if phase=='restart' else active
        if identity==expected and journal==(admission.run,admission.nonce,active,admission.candidate):
            selected.append(request)
    if len(selected)!=1:
        raise ValueError('no unique physical transaction boundary')
    request=selected[0]
    restart=document(TerminalRequest(admission,active,'restart'))
    restore=document(TerminalRequest(admission,active,'restore'))
    allowed=(None,restart) if request.phase=='restart' else (restart,restore)
    if prior_phase not in allowed:
        raise ValueError('no allowed monotonic one-run phase transition')
    return document(request)

def _prior(dirfd,name):
    try:
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=dirfd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or s.st_nlink!=1
                or stat.S_IMODE(s.st_mode)!=0o644 or not os.path.samestat(s,os.stat(name,dir_fd=dirfd,follow_symlinks=False))):
            raise ValueError('untrusted root phase inode')
        raw=f.read(MAX+1)
        if not 0<len(raw)<=MAX:
            raise ValueError('invalid root phase size')
        return raw

def _require_active_native_lock():
    fd=os.open(DEPLOY,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        s=os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=986 or s.st_nlink!=1 or stat.S_IMODE(s.st_mode)!=0o600
                or not os.path.samestat(s,DEPLOY.lstat())):
            raise ValueError('untrusted native deploy lock')
        try:
            fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:
            return
        raise ValueError('no live native exclusive release')
    finally:
        os.close(fd)

def publish_one_observation(pair):
    _require_root()
    path=record(pair.admitted,'phase')
    name=path.name
    _safe_parent(path.parent)
    # This is a bounded observation between native effects, not a lease across
    # restart or smoke. Never reacquire the live native deploy lock.
    with _lock(TRANSITION,owner=0,mode=0o644):
        _require_active_native_lock()
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            state=_read_authorizer()
            old=_prior(parent,name)
            new=planned_phase(pair,state=state,prior_phase=old,native_observer=observe_native_boundary)
            if old==new:
                os.fsync(parent)
                return
            staged='.terminal-phase-'+secrets.token_hex(16)
            fd=os.open(staged,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644,dir_fd=parent)
            try:
                with os.fdopen(fd,'wb') as f:
                    os.fchmod(f.fileno(),0o644)
                    f.write(new);f.flush();os.fsync(f.fileno())
                _require_active_native_lock()
                if (_read_authorizer()!=state or _prior(parent,name)!=old
                        or planned_phase(pair,state=state,prior_phase=old,native_observer=observe_native_boundary)!=new):
                    raise ValueError('native phase changed before publication')
                os.replace(staged,name,src_dir_fd=parent,dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:os.unlink(staged,dir_fd=parent)
                except FileNotFoundError:pass
        finally:os.close(parent)
