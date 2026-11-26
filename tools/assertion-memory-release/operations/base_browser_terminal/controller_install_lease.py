"""Exact existing Base lock pair for versioned root-operated source changes.

No locks/files created; no units, routes, authorizer records or DB touched.
For installer only. Do not hold transition EX across service traffic.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import os
from pathlib import Path
import stat

DEPLOY=Path('/srv/assertion-release/production/deploy.lock')
TRANSITION=Path('/run/assertion-base-prod-transition/fence.lock')

@contextmanager
def _lock(path, *, owner, mode):
    if path.parent.resolve(strict=True)!=path.parent:
        raise ValueError('unexpected existing Base lock parent')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1
                or st.st_uid!=owner or stat.S_IMODE(st.st_mode)!=mode
                or not os.path.samestat(st,path.lstat())):
            raise ValueError('unexpected existing Base lock inode')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not os.path.samestat(st,path.lstat()):
            raise ValueError('Base lock changed during acquire')
        yield fd
    finally:
        os.close(fd)

@contextmanager
def existing_root_install_lease():
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('protected Base source installer must run under existing root operator')
    with ExitStack() as stack:
        # Native receive starts with deploy lock, and its settled guard then
        # enters the transition fence. Never take the opposite lock order.
        stack.enter_context(_lock(DEPLOY,owner=986,mode=0o600))
        stack.enter_context(_lock(TRANSITION,owner=0,mode=0o644))
        yield
