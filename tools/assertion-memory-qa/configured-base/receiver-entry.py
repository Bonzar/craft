"""Install root-owned; invoked with the exact TEST-writer user and no argv."""
import json
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import sys

CONFIG=Path('/etc/assertion-release/configured-base-test')
STATE=Path('/var/lib/assertion-configured-base-test')
sys.path.insert(0,'/etc/assertion-release/shared')
sys.path.insert(0,str(CONFIG))
from configured_base_test_gate import Ledger, Policy, receive

def root_file(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as source:
        meta=os.fstat(source.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or meta.st_mode&0o022 or meta.st_size>4096:
            raise ValueError('Invalid root policy')
        return json.load(source)

def main():
    account=pwd.getpwuid(os.getuid())
    if (len(sys.argv)!=1 or os.getuid()!=os.geteuid() or account.pw_name!='assertion-test-deploy'
            or account.pw_dir!='/srv/assertion-release/test'):
        raise ValueError('Incorrect canonical TEST service user')
    os.environ['HOME']=account.pw_dir
    state=STATE.lstat()
    if (not stat.S_ISDIR(state.st_mode) or state.st_uid!=os.getuid()
            or stat.S_IMODE(state.st_mode)!=0o700 or STATE.resolve()!=STATE):
        raise ValueError('Unexpected configured TEST nonce state')
    config=root_file(CONFIG/'policy.json')
    if set(config)!={'reusable_sha','environment_node_id','vm_id','repository_id'}:
        raise ValueError('Unexpected configured TEST policy')
    policy=Policy(**config)
    import base_payload
    def deploy(revision,digest,stream):
        base_payload.deploy(stream,revision,digest,root=Path('/srv/assertion-release/test'),
                            config=Path('/etc/assertion-release/test'))
    ledger=STATE/'nonce.sqlite3'
    fd=os.open(ledger,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        m=os.fstat(fd)
        if not stat.S_ISREG(m.st_mode) or m.st_uid!=os.getuid() or stat.S_IMODE(m.st_mode)!=0o600:
            raise ValueError('Unexpected scoped nonce file')
        with sqlite3.connect(ledger,timeout=3) as db:
            return receive(sys.stdin.buffer,policy,Ledger(db),deploy)
    finally: os.close(fd)

if __name__=='__main__':
    os.umask(0o077)
    try:
        result=main()
    except Exception:
        print('{"status":"rejected","error":"configured_test_denied"}')
        sys.exit(1)
    print(json.dumps(result,separators=(',',':')))
