"""Exact root filesystem adaptor for the marker-dark source transaction.

No CLI, use only from the approved immutable operations execution.
Existing service state and credential paths are never opened by this module.
"""
import os
from pathlib import Path
import re
import secrets
import stat

from controller_install_lease import existing_root_install_lease
from fixed_source_installer import _open_parent,_regular_at,_stage_at
from sealed_host_install_bundle import INSTALL,MOUNT,STAGED
from dark_install_transaction import _sha

RECOVERY=Path(INSTALL+'.base-terminal-dark-inverse-v1')
MOUNT_DIR=Path(MOUNT)
MARKER=Path('/etc/assertion-release/production/base-browser-listener.json')
NATIVE=Path('/srv/assertion-release/production/.activation-pending-v1')
PERSISTENT=Path('/var/lib/assertion-base-prod-transition')
STATUS_PENDING=(PERSISTENT/'pending.json',PERSISTENT/'permit.json')
DIGEST=re.compile(r'[0-9a-f]{64}\Z')

def _exists(path):
    try:path.lstat()
    except FileNotFoundError:return False
    return True

def _open_dir(path,mode):
    if path.resolve(strict=True)!=path:
        raise ValueError('untrusted exact source directory')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    s=os.fstat(fd)
    if (s.st_uid!=0 or s.st_gid!=0 or stat.S_IMODE(s.st_mode)!=mode
            or not os.path.samestat(s,path.lstat())):
        os.close(fd);raise ValueError('untrusted exact source directory inode')
    return fd

def _rename_dir(path,suffix):
    fd=_open_parent(path)
    try:
        retired=path.name+'.retired-'+suffix+'-'+secrets.token_hex(8)
        if _exists(path.with_name(retired)):raise ValueError('foreign retired source namespace')
        os.rename(path.name,retired,src_dir_fd=fd,dst_dir_fd=fd)
        os.fsync(fd)
    finally:os.close(fd)

def _publish_directory(path,values,mode,mode_file):
    fd=_open_parent(path)
    staging='.'+path.name+'.stage-'+secrets.token_hex(12)
    try:
        if _exists(path):raise ValueError('protected source directory already exists')
        os.mkdir(staging,mode=0o700,dir_fd=fd)
        inner=os.open(staging,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        try:
            for name,value in sorted(values.items()):
                temp=_stage_at(inner,value,mode_file)
                os.rename(temp,name,src_dir_fd=inner,dst_dir_fd=inner)
            os.fchown(inner,0,0)
            os.fchmod(inner,mode)
            os.fsync(inner)
        finally:os.close(inner)
        if _exists(path):raise ValueError('protected source namespace appeared')
        os.rename(staging,path.name,src_dir_fd=fd,dst_dir_fd=fd)
        os.fsync(fd)
        # Interrupted unpublished root-private staging is kept for inspection
        # rather than pruning any path not part of the approved transaction.
    finally:os.close(fd)

class RootDarkIO:
    def lease(self):
        return existing_root_install_lease()
    def marker_absent(self):
        return not _exists(MARKER)
    def native_idle(self):
        return not any(_exists(path) for path in (NATIVE,*STATUS_PENDING))
    def read_exact(self,path,mode):
        if path not in STAGED or STAGED[path][0]!=mode:
            raise ValueError('foreign exact root stock path')
        p=Path(path);fd=_open_parent(p)
        try:return _regular_at(fd,p.name,mode)
        finally:os.close(fd)
    def recovery(self):
        if not _exists(RECOVERY):return None
        fd=_open_dir(RECOVERY,0o700)
        try:
            body=_regular_at(fd,'digest',0o600)
            if body[-1:]!=b'\n' or not DIGEST.fullmatch(body[:-1].decode('ascii')):
                raise ValueError('unknown exact dark source recovery')
            return body[:-1].decode('ascii')
        finally:os.close(fd)
    def originals(self,digest):
        if self.recovery()!=digest:raise ValueError('wrong dark source recovery')
        fd=_open_dir(RECOVERY,0o700)
        try:
            paths={p:Path(p).name for p in STAGED}
            if set(os.listdir(fd))!=set(paths.values())|{'digest'}:
                raise ValueError('unexpected dark source inverse directory')
            return {p:_regular_at(fd,n,0o600) for p,n in paths.items()}
        finally:os.close(fd)
    def stage_inverse(self,digest,originals):
        if type(digest) is not str or not DIGEST.fullmatch(digest) or set(originals)!=set(STAGED):
            raise ValueError('wrong scoped dark source recovery')
        body={Path(p).name:v for p,v in originals.items()}
        body['digest']=(digest+'\n').encode('ascii')
        _publish_directory(RECOVERY,body,0o700,0o600)
    def finish_inverse(self,digest):
        if self.recovery()!=digest:raise ValueError('wrong dark source inverse retirement')
        _rename_dir(RECOVERY,digest)
    def replace_exact(self,path,mode,before,after):
        if _sha(self.read_exact(path,mode))!=before:
            raise ValueError('stock root source changed')
        p=Path(path);fd=_open_parent(p)
        staged=None
        try:
            staged=_stage_at(fd,after,mode)
            if _sha(_regular_at(fd,p.name,mode))!=before:
                raise ValueError('stock root source changed before atomic swap')
            os.replace(staged,p.name,src_dir_fd=fd,dst_dir_fd=fd);staged=None
            os.fsync(fd)
        finally:
            if staged is not None:
                try:os.unlink(staged,dir_fd=fd)
                except FileNotFoundError:pass
            os.close(fd)
    def check_namespace(self,path,rows,*,allow_missing=False,require_absent=False):
        if path!=MOUNT:raise ValueError('foreign fixed Base module directory')
        if not _exists(MOUNT_DIR):
            if allow_missing or require_absent:return
            raise ValueError('missing fixed Base modules')
        if require_absent:raise ValueError('protected module namespace already exists')
        fd=_open_dir(MOUNT_DIR,0o755)
        try:
            if set(os.listdir(fd))!={Path(p).name for p in rows}:
                raise ValueError('unexpected fixed Base module namespace')
            for p,row in rows.items():
                if _sha(_regular_at(fd,Path(p).name,row['mode']))!=row['after']:
                    raise ValueError('changed fixed Base module namespace')
        finally:os.close(fd)
    def publish_namespace(self,path,rows,images):
        self.check_namespace(path,rows,require_absent=True)
        if set(images)!=set(rows) or any(_sha(images[p])!=rows[p]['after'] for p in rows):
            raise ValueError('unverified exact candidate image')
        _publish_directory(MOUNT_DIR,{Path(p).name:v for p,v in images.items()},0o755,0o644)
    def discard_namespace(self,path,rows):
        self.check_namespace(path,rows,allow_missing=True)
        if _exists(MOUNT_DIR):_rename_dir(MOUNT_DIR,'inverse')
