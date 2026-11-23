"""Production-only receiver. Install root-owned and route only dedicated PROD SSH user."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import tempfile

import receiver_gate
from receiver import (
    activate_test as activate,
    prepare_test as prepare,
    recover_test_activation as recover,
    run_hook,
    stage,
    verify_manifest,
)

ROOT = Path('/srv/assertion-release/production')
CONFIG = Path('/etc/assertion-release/production')
USER = 'assertion-production-deploy'


def trusted_regular(path):
    entry=path.lstat()
    return stat.S_ISREG(entry.st_mode) and entry.st_uid == 0 and entry.st_mode & 0o022 == 0


@contextmanager
def locked(root):
    with (root/'deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield lock.fileno()


def activate_exact(revision, sha256, stream, *, root=ROOT, config=CONFIG, hook=None):
    """This function takes only an already-authorized stream, then re-hashes it independently."""
    new=root/'releases'/f'{revision}-{sha256}'
    if not re.fullmatch(r'[0-9a-f]{40}',revision) or not re.fullmatch(r'[0-9a-f]{64}',sha256):
        raise ValueError('invalid_release_path')
    script=config/'service-hook.sh'
    if not trusted_regular(script):
        raise ValueError('untrusted_hook')
    with locked(root) as lockfd:
        runner=hook or (lambda script,action,location: run_hook(script,action,location,lock_fd=lockfd))
        # Crash journal uses this environment's directory only.
        recover(root,script,runner)
        with tempfile.TemporaryDirectory(prefix='.incoming-',dir=root) as scratch:
            payload=stage(stream,Path(scratch),sha256)
            app=payload if (payload/'prototypes/assertion_memory/http_server.py').is_file() else payload/'source'
            if not (app/'prototypes/assertion_memory/http_server.py').is_file():
                raise ValueError('source_layout')
            if not (app/'apps/graph_viewer/web/dist/index.html').is_file():
                raise ValueError('compiled_viewer_missing')
            if (payload/'REVISION').read_text().strip()!=revision:
                raise ValueError('revision_mismatch')
            if (payload/'QA_PROFILE').read_text().strip()!='assertion-memory-synthetic-qa-v1':
                raise ValueError('release_profile')
            verify_manifest(payload)
            prepare(payload,new,root,script,runner)
            activate(root,new,script,runner)
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
