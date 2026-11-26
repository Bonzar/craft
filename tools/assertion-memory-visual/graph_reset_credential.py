"""Stage one existing lane's Visual credential to the canonical Graph-user-only /run file."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import pwd
import re
import stat
import tempfile

ORIGIN=Path('/etc/assertion-memory-visual/configured')
RUNTIME=Path('/run/assertion-graph-reset-read')
ACCOUNTS={'test':'assertion-test-deploy','prod':'assertion-production-deploy'}
TOKEN='standalone-graph-token'

def verify_dir(path, mode=None):
    st=path.lstat()
    if (path.resolve(strict=True)!=path or not stat.S_ISDIR(st.st_mode) or st.st_uid!=0
            or st.st_mode&0o022 or (mode is not None and stat.S_IMODE(st.st_mode)!=mode)):
        raise ValueError('Untrusted Graph readiness directory')

def exact_source(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as file:
        st=os.fstat(file.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or stat.S_IMODE(st.st_mode)!=0o600
                or st.st_nlink!=1 or not (64<=st.st_size<=66)):
            raise ValueError('Untrusted existing Visual credential')
        value=file.read(67)
        if not re.fullmatch(rb'[0-9a-f]{64}(?:\r?\n)?',value) or not os.path.samestat(st,path.lstat()):
            raise ValueError('Unchanged exact Visual credential required')
        return value[:64]

def stage(lane):
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('Graph readiness staging requires root')
    account=pwd.getpwnam(ACCOUNTS[lane])
    other=pwd.getpwnam(ACCOUNTS['prod' if lane=='test' else 'test'])
    if not account.pw_uid or account.pw_uid==other.pw_uid:
        raise ValueError('Graph lane identity is not isolated')
    for directory in (ORIGIN.parent,ORIGIN,ORIGIN/lane):
        verify_dir(directory)
    value=exact_source(ORIGIN/lane/TOKEN)
    verify_dir(RUNTIME.parent)
    for directory in (RUNTIME,RUNTIME/lane):
        directory.mkdir(mode=0o755,exist_ok=True)
        verify_dir(directory,0o755)
    directory=RUNTIME/lane
    target=directory/TOKEN
    try:
        current=target.lstat()
    except FileNotFoundError:
        current=None
    if current is not None and (not stat.S_ISREG(current.st_mode) or current.st_uid!=account.pw_uid
                                or current.st_nlink!=1 or stat.S_IMODE(current.st_mode)!=0o400):
        raise ValueError('Unrecognized readiness credential destination')
    fd,temp=tempfile.mkstemp(prefix='.stage-',dir=directory)
    try:
        with os.fdopen(fd,'wb') as file:
            file.write(value)
            file.flush()
            os.fchown(file.fileno(),account.pw_uid,account.pw_gid)
            os.fchmod(file.fileno(),0o400)
            os.fsync(file.fileno())
        os.replace(temp,target)
        dfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        Path(temp).unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lane',choices=tuple(ACCOUNTS))
    stage(parser.parse_args().lane)

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError):
        raise SystemExit('Graph reset credential staging refused') from None
