"""Resolve TEST current once before running the same Sites Graph wrapper."""
import os
from pathlib import Path
import pwd
import re
import stat
import sys

ROOT=Path('/srv/assertion-release/test')
WRAPPER=Path('/opt/assertion-memory-site-test-graph/start.py')
ACCOUNT='assertion-test-deploy'
NAME=re.compile(r'([a-f0-9]{40})-[a-f0-9]{64}\Z')


def command(root,wrapper,uid,*,trusted_owner=0):
    current=root/'current'
    if root.resolve(strict=True)!=root or not current.is_symlink():
        raise ValueError('Unverified TEST lane')
    release=current.resolve(strict=True)
    match=NAME.fullmatch(release.name)
    if match is None or release.parent!=root/'releases' or os.readlink(current)!=str(release):
        raise ValueError('Unverified TEST generation')
    for p,owner in ((root/'releases',uid),(release,uid),(wrapper.parent,trusted_owner),(wrapper,trusted_owner)):
        info=p.lstat()
        if (info.st_uid!=owner or info.st_mode&0o022 or p.resolve(strict=True)!=p
                or not (stat.S_ISREG(info.st_mode) if p==wrapper else stat.S_ISDIR(info.st_mode))):
            raise ValueError('Unverified TEST runtime path')
    for name,wanted in (('REVISION',match[1]+'\n'),('.prepared-v1','ok\n')):
        path=release/name
        entry=path.lstat()
        if not stat.S_ISREG(entry.st_mode) or entry.st_size!=len(wanted) or path.read_text()!=wanted:
            raise ValueError('Unverified TEST release marker')
    # Keep the venv invocation path (interpreter itself may link to system
    # Python). Preserve the original wrapper/working directory and all other
    # environment; only make its existing application source pin immutable.
    interpreter=release/'venv'/'bin'/'python'
    if not interpreter.is_file() or not os.access(interpreter,os.X_OK):
        raise ValueError('Missing TEST release interpreter')
    return [str(interpreter),'-s','-B',str(wrapper)],release


def main():
    if sys.argv[1:] or os.getuid()!=os.geteuid() or os.getuid()==0 or pwd.getpwuid(os.getuid()).pw_name!=ACCOUNT:
        raise SystemExit('Wrong TEST Graph launcher identity')
    try:
        argv,release=command(ROOT,WRAPPER,os.getuid())
        os.execve(argv[0],argv,{**os.environ,'PYTHONPATH':str(release)})
    except (OSError,ValueError):
        raise SystemExit('TEST Sites Graph immutable binding unavailable') from None


if __name__=='__main__':
    main()
