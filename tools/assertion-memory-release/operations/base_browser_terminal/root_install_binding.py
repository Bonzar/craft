"""Fixed root-owned operations metadata for the reversible source installer.

The authenticated immutable operator artifact must supply the two reviewed
source images and approved archive preflight. No service action occurs
here. No executable entry point is provided by this draft.
"""
import os
from pathlib import Path
import re
from controller_install_lease import DEPLOY, existing_root_install_lease
from fixed_source_installer import _open_parent,_regular_at,_stage_at,apply_exact

RECORD=Path('/etc/assertion-release/production/.base-terminal-source-ops-v1')
ACTIVE_RECEIVER_JOURNAL=DEPLOY.parent/'.activation-pending-v1'
DIGEST=re.compile(r'[a-f0-9]{64}\Z')

def _present(path):
    try: path.lstat()
    except FileNotFoundError: return False
    return True

class RootRecovery:
    def __init__(self): pass

    def read(self):
        if not _present(RECORD):return None
        fd=_open_parent(RECORD)
        try:
            body=_regular_at(fd,RECORD.name,0o644)
        finally:
            os.close(fd)
        if len(body)!=65 or body[-1:]!=b'\n' or not DIGEST.fullmatch(body[:-1].decode('ascii')):
            raise ValueError('invalid exact Base source inverse')
        return body[:-1].decode('ascii')

    def write(self,digest):
        if not DIGEST.fullmatch(digest):
            raise ValueError('invalid versioned ops recovery digest')
        fd=_open_parent(RECORD)
        staged=None
        try:
            staged=_stage_at(fd,digest.encode('ascii')+b'\n',0o644)
            # The enclosing exact installer holds both locks, so we can
            # reject a pre-existing inode and atomically rename without a
            # hardlink/crash window. The parent is root-owned throughout.
            try:
                os.stat(RECORD.name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError('protected source inverse already exists')
            os.replace(staged,RECORD.name,src_dir_fd=fd,dst_dir_fd=fd)
            staged=None
            os.fsync(fd)
        finally:
            if staged is not None:
                os.unlink(staged,dir_fd=fd)
            os.close(fd)

    def finish(self,digest):
        if self.read()!=digest:
            raise ValueError('foreign versioned source inverse')
        fd=_open_parent(RECORD)
        try:
            if _regular_at(fd,RECORD.name,0o644)!=(digest+'\n').encode('ascii'):
                raise ValueError('versioned source inverse changed')
            os.unlink(RECORD.name,dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(fd)

def apply_versioned_source(*, verified_bundle, immutable_bundle_sha,
                           approve_exact_bundle, verify_quiesced, inverse=False):
    """For the protected operator, after independent immutable bundle review."""
    state=RootRecovery()
    def preflight():
        return not _present(ACTIVE_RECEIVER_JOURNAL) and verify_quiesced() is True
    return apply_exact(bundle=verified_bundle,immutable_bundle_sha=immutable_bundle_sha,
        verify_bundle=approve_exact_bundle,release_lease=existing_root_install_lease,
        write_recovery=state.write,read_recovery=state.read,finish_recovery=state.finish,
        namespace_preflight=preflight,inverse=inverse)
