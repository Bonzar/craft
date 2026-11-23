"""Production gate is separate; stage/smoke/rollback are the same base path as TEST."""
import json
import os
from pathlib import Path
import pwd
import sys

import base_payload
import receiver_gate

ROOT=Path('/srv/assertion-release/production')
CONFIG=Path('/etc/assertion-release/production')
USER='assertion-production-deploy'


def activate_exact(revision,sha256,stream,*,root=ROOT,config=CONFIG,hook=None):
    adapt=None if hook is None else lambda action,location: hook(config/'service-hook.sh',action,location)
    base_payload.deploy(stream,revision,sha256,root=root,config=config,hook=adapt)
    return {'status':'ok','environment':'production','revision':revision,'sha256':sha256}


def main():
    try:
        user=pwd.getpwuid(os.getuid())
        if (sys.argv[1:]!=['production'] or user.pw_name!=USER or user.pw_dir!=str(ROOT)
                or os.geteuid()!=os.getuid() or os.getuid()==0):
            raise ValueError('wrong_account')
        result=receiver_gate.handle(os.environ.get('SSH_ORIGINAL_COMMAND',''),sys.stdin.buffer,activate_exact)
        print(json.dumps(result,separators=(',',':')))
        return 0
    except Exception as exc:
        # Do not echo authorization frame, token, server capabilities or paths.
        code=str(exc) if str(exc) in ('command_denied','archive_digest','wrong_account') else 'deployment_failed'
        print(json.dumps({'status':'rejected','error':code},separators=(',',':')))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
