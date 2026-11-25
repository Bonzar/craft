"""Fixed privileged restart, provisioned independently of untrusted release bytes."""
import os
from pathlib import Path
import pwd
import stat
import sys

HERE=Path('/etc/assertion-memory-visual/configured/test/runtime-test-restart.py')
UNIT='assertion-memory-visual-test.service'
CALLER='assertion-visual-test-deploy'


def main():
    if not sys.flags.isolated or len(sys.argv)!=1 or Path(__file__)!=HERE or os.geteuid()!=0:
        raise SystemExit(1)
    if os.environ.get('SUDO_USER')!=CALLER or os.environ.get('SUDO_UID')!=str(pwd.getpwnam(CALLER).pw_uid):
        raise SystemExit(1)
    for item in (HERE,*[p for p in HERE.parents if p != Path('/')]):
        info=item.lstat()
        correct=stat.S_ISREG if item==HERE else stat.S_ISDIR
        if info.st_uid!=0 or info.st_mode&0o022 or not correct(info.st_mode):
            raise SystemExit(1)
    os.execve('/usr/bin/systemctl', ('/usr/bin/systemctl','restart',UNIT),
              {'PATH':'/usr/bin:/bin','LANG':'C','SYSTEMCTL_PAGER':'','SYSTEMCTL_PAGERSECURE':'1'})


if __name__=='__main__':
    main()
