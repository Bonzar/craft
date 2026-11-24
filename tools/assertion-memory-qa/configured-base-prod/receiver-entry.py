#!/usr/bin/python3 -I
"""Fixed sudo target; the legacy writer must not be invoked until a permit matches."""
import json
import os
from pathlib import Path
import pwd
import sys

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE/'code/tools/assertion-memory-qa')]
sys.path.insert(0,'/etc/assertion-release/production/authorization')
# Installed canonical Base payload and release transaction code are shared.
sys.path.insert(1,'/etc/assertion-release/shared')
from configured_base_prod_gate import receive
from receive_authorized import activate_exact
from reusable_proof import require
from receiver_gate import exchange

def main():
    try:
        require(len(sys.argv)==1 and os.getuid()==os.geteuid()!=0
                and pwd.getpwuid(os.getuid()).pw_name=='assertion-production-deploy','Wrong execution UID')
        result=receive(sys.stdin.buffer,
            lambda frame:exchange(frame,socket_path=Path('/run/assertion-configured-base-prod-auth/production.sock')),
            activate_exact)
        print(json.dumps(result,separators=(',',':')))
        return 0
    except Exception:
        print('{"status":"rejected"}')
        return 1
if __name__=='__main__':raise SystemExit(main())
