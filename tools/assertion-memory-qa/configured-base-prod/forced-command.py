#!/usr/bin/python3 -I
"""Restricted login shell for the configured Base PROD account only."""
import os
import pwd
import sys
USER='assertion-cfg-base-prod-deploy'
FORCED='configured-prod-receive'
SUDO='/usr/bin/sudo'
ENTRY='/etc/assertion-release/configured-base-prod/receiver-entry.py'

def main():
    if (sys.argv[1:]!=['-c',FORCED] or os.getuid()!=os.geteuid() or not os.getuid()
            or pwd.getpwuid(os.getuid()).pw_name!=USER
            or os.environ.get('SSH_ORIGINAL_COMMAND')!=FORCED):
        return 126
    os.execve(SUDO,[SUDO,'-n','-u','assertion-production-deploy','--',ENTRY],
              {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
if __name__=='__main__':raise SystemExit(main())
