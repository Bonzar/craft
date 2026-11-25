"""Fixed privileged restart, provisioned independently of untrusted release bytes."""
import os
from pathlib import Path
import pwd
import stat
import sys

HOST_SCOPE={
 Path('/etc/assertion-memory-visual/configured/test/runtime-test-restart.py'):
     ('assertion-memory-visual-test.service','assertion-visual-test-deploy'),
 Path('/etc/assertion-memory-visual/configured/prod/runtime-prod-restart.py'):
     ('assertion-memory-visual-prod.service','assertion-visual-prod-deploy'),
}


def main():
    here=Path(__file__)
    if not sys.flags.isolated or len(sys.argv)!=1 or here not in HOST_SCOPE or os.geteuid()!=0:
        raise SystemExit(1)
    unit,caller=HOST_SCOPE[here]
    if os.environ.get('SUDO_USER')!=caller or os.environ.get('SUDO_UID')!=str(pwd.getpwnam(caller).pw_uid):
        raise SystemExit(1)
    for item in (here,*[p for p in here.parents if p != Path('/')]):
        info=item.lstat()
        correct=stat.S_ISREG if item==here else stat.S_ISDIR
        if info.st_uid!=0 or info.st_mode&0o022 or not correct(info.st_mode):
            raise SystemExit(1)
    os.execve('/usr/bin/systemctl', ('/usr/bin/systemctl','restart',unit),
              {'PATH':'/usr/bin:/bin','LANG':'C','SYSTEMCTL_PAGER':'','SYSTEMCTL_PAGERSECURE':'1'})


if __name__=='__main__':
    main()
