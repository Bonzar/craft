"""Bounded one-run root foreground observer; service start is not exposed."""
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat

from configured_terminal_state import read_exact,encode
from controller_install_lease import TRANSITION,_lock
from foreground_terminal import (observe_before_unlink,observe_after_unlink,
    precommit_wire,restore_precommit)
from kernel_observer import observe
from native_boundary import PROD_ROOT
from operator_foreground_phase import _read_authorizer,_require_root,_require_active_native_lock,ApprovedPair
from ops_readonly_preflight import trusted_pid
from root_phase_binding import PHASE,_read_root_phase,observe_record,_safe_parent
from root_terminal_publisher import publish_with_existing_authorizer,canonical_terminal
from terminal_lifecycle import TerminalRequest

FILES={'restart':'base-terminal-restart.precommit','restore':'base-terminal-restore.precommit'}
MAX=16384

def _get(parent,name):
    try: fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent)
    except FileNotFoundError:return None
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or s.st_nlink!=1
                or stat.S_IMODE(s.st_mode)!=0o644
                or not os.path.samestat(s,os.stat(name,dir_fd=parent,follow_symlinks=False))):
            raise ValueError('unverified foreground precommit inode')
        data=f.read(MAX+1)
        if not 0<len(data)<=MAX:raise ValueError('unverified foreground precommit size')
        return data

def _record(parent,name,body):
    existing=_get(parent,name)
    if existing is not None:
        if existing!=body:raise ValueError('conflicting durable foreground precommit')
        os.fsync(parent)
        return
    tmp='.terminal-precommit-'+secrets.token_hex(16)
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644,dir_fd=parent)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o644)
            f.write(body);f.flush();os.fsync(f.fileno())
        if _get(parent,name) is not None:raise ValueError('foreground precommit changed')
        os.replace(tmp,name,src_dir_fd=parent,dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:os.unlink(tmp,dir_fd=parent)
        except FileNotFoundError:pass

def _fsync_native():
    if PROD_ROOT.resolve(strict=True)!=PROD_ROOT:
        raise ValueError('noncanonical native release root')
    fd=os.open(PROD_ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        if not os.path.samestat(os.fstat(fd),PROD_ROOT.lstat()):
            raise ValueError('native durability inode changed')
        os.fsync(fd)
    finally:os.close(fd)

def settle_step(pair):
    _require_root()
    if type(pair) is not ApprovedPair:
        raise ValueError('signed run/prior foreground scope required')
    _safe_parent(PHASE.parent)
    # The native transaction holds deploy lock; never runs a
    # service command and writes only its own fixed root record.
    with _lock(TRANSITION,owner=0,mode=0o644):
        _require_active_native_lock()
        state=_read_authorizer()
        known,admitted,receipt=read_exact(state)
        if admitted is None and receipt is not None:
            req=TerminalRequest(pair.admitted,pair.prior,receipt['phase'])
            selected=pair.admitted.candidate if req.phase=='restart' else pair.prior
            if encode(receipt)!=canonical_terminal(req,selected):raise ValueError('foreign settled receipt')
            return 'already-settled'
        if (known,admitted)!=(pair.prior,pair.admitted):
            raise ValueError('not the foreground admitted transaction')
        phase=observe_record(_read_root_phase(),state)
        req=TerminalRequest(admitted,known,phase)
        sample=lambda r:observe(r,trusted_pid=trusted_pid)
        result=sample(req)
        name=FILES[phase]
        parent=os.open(PHASE.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            if result.journal is not None:
                pre=observe_before_unlink(req,sample)
                if _read_authorizer()!=state:
                    raise ValueError('foreground admission changed before precommit')
                _record(parent,name,precommit_wire(pre))
                return 'precommit-ready'
            previous=restore_precommit(_get(parent,name),req)
            observe_after_unlink(req,previous,sample,_fsync_native,publish_with_existing_authorizer)
            return 'settled'
        finally:os.close(parent)
