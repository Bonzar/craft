#!/usr/bin/python3 -I
"""Compiled-only SSH entry; all accepted requests go to an independently signed gate."""
import os
import pwd
import sys

FORCED='/etc/assertion-release/configured-base-test/forced-command.py'
TARGET='/etc/assertion-release/configured-base-test/receiver-entry.py'

if __name__=='__main__':
    uid=os.getuid()
    if (sys.argv[1:]!=['-c',FORCED] or uid==0 or uid!=os.geteuid()
            or pwd.getpwuid(uid).pw_name!='assertion-cfg-base-test-deploy'
            or os.environ.get('SSH_ORIGINAL_COMMAND')!='configured-base-test'):
        print('{"status":"rejected","error":"command_denied"}')
        sys.exit(1)
    os.execve('/usr/bin/sudo',('/usr/bin/sudo','-n','-u','assertion-test-deploy','--',
                   '/usr/bin/python3','-I','-B',TARGET),
          {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
