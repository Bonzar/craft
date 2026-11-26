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


# Read-only, exact, root-installed ops directory. Source-only builds leave it absent.
PROTECTED=Path('/etc/assertion-release/configured-base-prod/base-terminal-binding')

def protected_binding():
    import stat
    marker=Path('/etc/assertion-release/production/base-browser-listener.json')
    if not (marker.exists() or marker.is_symlink()):
        return None
    require(PROTECTED.resolve()==PROTECTED,'Wrong protected binding path')
    for directory in (PROTECTED,*PROTECTED.parents):
        m=directory.lstat()
        require(stat.S_ISDIR(m.st_mode) and m.st_uid==0 and m.st_gid==0 and not m.st_mode&0o022,
                'Unprotected terminal binding directory')
    sys.path.insert(0,str(PROTECTED))
    import configured_entry_binding
    return configured_entry_binding

def main():
    try:
        require(len(sys.argv)==1 and os.getuid()==os.geteuid()!=0
                and pwd.getpwuid(os.getuid()).pw_name=='assertion-production-deploy','Wrong execution UID')
        authorize=lambda frame:exchange(frame,socket_path=Path('/run/assertion-configured-base-prod-auth/production.sock'))
        binding=protected_binding()
        if binding is None:
            result=receive(sys.stdin.buffer,authorize,activate_exact)
        else:
            # Fixed, independently reviewable host integration; never run the
            # old un-witnessed activation with a protected terminal permit.
            from protected_native_activation import activate_terminal
            result=binding.receive_terminal(sys.stdin.buffer,authorize,activate_terminal)
        print(json.dumps(result,separators=(',',':')))
        return 0
    except Exception:
        print('{"status":"rejected"}')
        return 1
if __name__=='__main__':raise SystemExit(main())
