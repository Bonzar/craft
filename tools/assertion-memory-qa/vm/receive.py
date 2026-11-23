#!/usr/bin/python3
"""Base TEST entrypoint; shared receiver never grants production access."""
import json
import os
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
SHARED=Path('/etc/assertion-release/shared') if HERE==Path('/etc/assertion-release/test') else HERE.parents[1]/'assertion-memory-release'
sys.path.insert(0,str(SHARED))
import base_payload
from release_core import unpack

ROOT=Path('/srv/assertion-release')
CONFIG=Path('/etc/assertion-release')
COMMAND=re.compile(r'deploy-release (test|production) ([a-f0-9]{40}) ([a-f0-9]{64})')


def stage(stream, destination, expected):
    return unpack(stream,destination,expected,base_payload.SPEC)


def deploy(environment,revision,sha256,stream,*,root=None,config=None,hook=None):
    if environment!='test':
        raise ValueError('wrong_environment')
    root=ROOT/'test' if root is None else root
    config=CONFIG/'test' if config is None else config
    adapt=None if hook is None else lambda action,location: hook(config/'service-hook.sh',action,location)
    base_payload.deploy(stream,revision,sha256,root=root,config=config,hook=adapt)
    return 'ok'


def main():
    try:
        import pwd
        if sys.argv[1:]!=['test']:
            raise ValueError('expected_environment')
        user=pwd.getpwuid(os.getuid())
        if user.pw_name!='assertion-test-deploy' or os.getuid()!=os.geteuid() or os.getuid()==0:
            raise ValueError('wrong_account')
        match=COMMAND.fullmatch(os.environ.get('SSH_ORIGINAL_COMMAND',''))
        if not match or match[1]!='test':
            raise ValueError('command_denied')
        _,revision,sha=match.groups()
        status=deploy('test',revision,sha,sys.stdin.buffer)
        print(json.dumps(dict(status=status,revision=revision,sha256=sha,environment='test'),separators=(',',':')))
    except Exception as exc:
        code=str(exc) if isinstance(exc,(ValueError,RuntimeError)) and re.fullmatch(r'[a-z_]+',str(exc)) else 'deployment_failed'
        print(json.dumps(dict(status='rejected',error=code),separators=(',',':')))
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
