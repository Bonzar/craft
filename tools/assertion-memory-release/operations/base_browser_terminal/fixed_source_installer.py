"""Source-review prototype, no CLI or host execution.

Only an independently verified immutable operations bundle may invoke this
module from the protected root controller. Neither its caller nor this code
changes service/policy/DB/key/credential paths. The bundle and recovery
controller are not installed by this module.
"""
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import secrets
import re
import stat
from typing import Callable

from pinned_install_plan import GUARD, HOOK, KNOWN

_TARGETS=(GUARD,HOOK)
MAX=512*1024

@dataclass(frozen=True)
class VerifiedSource:
    path: str
    mode: int
    before: bytes
    after: bytes

def _digest(data):
    return sha256(data).hexdigest()

def _regular_at(fd,name,mode):
    item=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
    with os.fdopen(item,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_gid!=0 or info.st_nlink!=1
            or stat.S_IMODE(info.st_mode)!=mode
            or not os.path.samestat(info,os.stat(name,dir_fd=fd,follow_symlinks=False))):
            raise ValueError('unverified protected source file')
        body=stream.read(MAX+1)
        if not 0<len(body)<=MAX:
            raise ValueError('invalid protected source size')
        return body

def _open_parent(path):
    parent=path.parent
    for ancestor in (parent,*parent.parents):
        s=ancestor.lstat()
        if (not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or s.st_mode&0o022):
            raise ValueError('untrusted protected installation parent')
    fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    if not os.path.samestat(os.fstat(fd),parent.lstat()):
        os.close(fd)
        raise ValueError('protected parent changed')
    return fd

def _stage_at(fd,source,mode):
    scratch='.base-terminal-source-'+secrets.token_hex(16)
    created=os.open(scratch,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
    try:
        with os.fdopen(created,'wb') as stream:
            stream.write(source)
            stream.flush()
            os.fchmod(stream.fileno(),mode)
            os.fchown(stream.fileno(),0,0)
            os.fsync(stream.fileno())
    except BaseException:
        os.unlink(scratch,dir_fd=fd)
        raise
    return scratch

def _verify_set(bundle):
    if type(bundle) is not tuple or len(bundle)!=len(_TARGETS):
        raise ValueError('missing exact two-source protected profile')
    found=[]
    for entry in bundle:
        if (type(entry) is not VerifiedSource or entry.path not in _TARGETS or entry.path in found
                or entry.mode not in (0o555,0o644,0o755)
                or any(type(data) is not bytes or not 0<len(data)<=MAX for data in (entry.before,entry.after))
                or _digest(entry.before)!=KNOWN[entry.path] or entry.before==entry.after):
            raise ValueError('unexpected fixed protected source')
        compile(entry.before,entry.path,'exec',dont_inherit=True)
        compile(entry.after,entry.path,'exec',dont_inherit=True)
        found.append(entry.path)
    return tuple(sorted(bundle,key=lambda entry:entry.path))

def apply_exact(*, bundle: tuple, immutable_bundle_sha: str, verify_bundle: Callable,
                release_lease: Callable, write_recovery: Callable,
                read_recovery: Callable, finish_recovery: Callable,
                namespace_preflight: Callable, inverse=False):
    """Only callable under owned versioned release controller; no daemon control.

    Recovery callbacks must persist existing protected operation lock/journal:
    paired exact old/new hashes and stable lease under independent root owner.
    No record supplied/edited by the deploy UID can be accepted. Detached
    callback mocks are not production authority.
    """
    if (os.getuid()!=0 or os.geteuid()!=0 or type(inverse) is not bool
            or not all(callable(x) for x in (verify_bundle,release_lease,write_recovery,
               read_recovery,finish_recovery,namespace_preflight))):
        raise ValueError('fixed protected root installer required')
    if type(immutable_bundle_sha) is not str or not re.fullmatch('[0-9a-f]{64}',immutable_bundle_sha):
        raise ValueError('unverified immutable ops bundle digest')
    ordered=_verify_set(bundle)
    if verify_bundle(immutable_bundle_sha,ordered) is not True:
        raise ValueError('unverified immutable ops bundle')
    with release_lease():
        if namespace_preflight() is not True:
            raise ValueError('existing exact protected route is not quiesced')
        handles=[]
        try:
            durable=read_recovery()
            if inverse and durable!=immutable_bundle_sha or not inverse and durable is not None:
                raise ValueError('existing incident needs exact protected recovery')
            for item in ordered:
                path=Path(item.path); fd=_open_parent(path)
                handles.append([item,path,fd,None])
                current=_regular_at(fd,path.name,item.mode)
                if (current not in (item.before,item.after) if inverse else current!=item.before):
                    raise ValueError('exact installer preimage mismatch')
            if not inverse:
                write_recovery(immutable_bundle_sha)
                if read_recovery()!=immutable_bundle_sha:
                    raise ValueError('missing durable exact inverse record')
            for work in handles:
                item,path,fd,_=work
                work[3]=_stage_at(fd,item.before if inverse else item.after,item.mode)
            for item,path,fd,staged in handles:
                current=_regular_at(fd,path.name,item.mode)
                if (current not in (item.before,item.after) if inverse else current!=item.before):
                    raise ValueError('protected source changed before installation')
                os.replace(staged,path.name,src_dir_fd=fd,dst_dir_fd=fd)
                os.fsync(fd)
            for item,path,fd,_ in handles:
                if _regular_at(fd,path.name,item.mode)!=(item.before if inverse else item.after):
                    raise ValueError('protected source outcome mismatch')
            if inverse:
                finish_recovery(immutable_bundle_sha)
            # On forward success keep inverse information until separately
            # verified release/rollback and immutable retention.
        finally:
            for _,_,fd,staged in handles:
                if staged is not None:
                    try: os.unlink(staged,dir_fd=fd)
                    except FileNotFoundError: pass
                os.close(fd)
