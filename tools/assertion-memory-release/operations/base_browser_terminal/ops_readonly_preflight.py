"""Immutable operations archive preflight: read only, no credentials."""
from dataclasses import asdict
import argparse
from hashlib import sha256
import os
import json
import subprocess
from pathlib import Path
import re
import stat

from release_core import PENDING,checked_release,current_target
from transition_witness import Admission,Release
from kernel_observer import observe,BASE,GRAPH
from terminal_lifecycle import TerminalRequest
from native_boundary import PROD_ROOT

CFG=Path('/etc/assertion-release/configured-base-prod')
PROD=Path('/etc/assertion-release/production')
GUARD=Path('/usr/local/sbin/assertion-base-prod-transition')
RUNTIME=Path('/run/assertion-base-prod-transition')
SOCKET=Path('/run/assertion-configured-base-prod-auth/production.sock')
STOCK_RO={
    CFG/'authorizer-entry.py':(0o644,'4fb7e3809728a4ac4d5cf70f9c61b240117a7fc1b91dafbc9ddb952dc4f781c7'),
    CFG/'receiver-entry.py':(0o755,'21b9b97ffacda9c1ef220ca4acd7d7f116aedffeb9fb270db01dd218f25c4872'),
    CFG/'forced-command.py':(0o755,'d644cfdc063f6cd91cfeaf254f50e17e996dfab55599a59f03120a0161afc1b6'),
    CFG/'configured_base_prod_gate.py':(0o644,'fa969a0424c9257ef09c9ec4f2c6bc8bb781010c1278b712b08552569af54ca6'),
    PROD/'prod_service_hook.py':(0o644,'0b3f585ffc1668fe3627a967de66d21fb275271c1e891915466542b9a1445c5e'),
    PROD/'service-hook.sh':(0o755,'9801563b96c7e6d3702f5a9d2635691f9c33c5361fff1907fff91efa823aa533'),
    GUARD:(0o755,'1b09e5d77c338a148a9df471e0ed010c82f104a2f310a0e45b73cd31f67c6c34'),
}
SWITCH=PROD/'base-browser-listener.json'
PHASE=RUNTIME/'base-terminal-phase.json'

def _absent(path):
    try:path.lstat()
    except FileNotFoundError:return
    raise ValueError('existing transition evidence or switch; require independent recovery')

def _metadata(path,kind,uid,mode):
    s=path.lstat()
    if (not kind(s.st_mode) or s.st_uid!=uid or stat.S_IMODE(s.st_mode)!=mode
            or path.resolve(strict=True)!=path):
        raise ValueError('unexpected exact preflight metadata')
    return s

def _read(path,limit,*,owner=None,mode=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or path.resolve(strict=True)!=path
                or s.st_mode&0o022 or not os.path.samestat(s,path.lstat())
                or owner is not None and (s.st_uid!=owner or s.st_gid!=owner)
                or mode is not None and stat.S_IMODE(s.st_mode)!=mode):
            raise ValueError('unexpected preflight file')
        data=f.read(limit+1)
    if not 0<len(data)<=limit:raise ValueError('unexpected preflight file size')
    return data

def classify_predecessor(manifest,expected):
    if type(expected) is not Release or expected.mode!='legacy':
        raise ValueError('initial cutover needs exact running legacy predecessor')
    if sha256(manifest).hexdigest()!=expected.manifest:
        raise ValueError('current predecessor manifest drift')
    # Current one-switch inverse only supports a legacy package without the
    # sidecar marker; a capable-but-dark predecessor needs a reviewed inverse.
    for line in manifest.splitlines():
        if line.partition(b'  ')[2] in (b'BASE_BROWSER_LISTENER_CONTRACT',b'./BASE_BROWSER_LISTENER_CONTRACT'):
            raise ValueError('capable-but-dark predecessor needs separately sealed inverse')
    return True

def run(expected):
    if os.getuid()!=0 or os.geteuid()!=0:
        raise ValueError('existing root operator preflight only')
    for path in (Path('/etc/assertion-release'),CFG,PROD,RUNTIME):
        _metadata(path,stat.S_ISDIR,0,0o755)
    for path,(mode,digest) in STOCK_RO.items():
        if sha256(_read(path,1024*1024,owner=0,mode=mode)).hexdigest()!=digest:
            raise ValueError('installed preflight source pin drift')
    _metadata(SOCKET,stat.S_ISSOCK,0,0o660)
    for path,owner,mode in ((RUNTIME/'fence.lock',0,0o644),(PROD_ROOT/'deploy.lock',986,0o600)):
        if _metadata(path,stat.S_ISREG,owner,mode).st_nlink!=1:
            raise ValueError('unexpected lock hard links')
    for path in (SWITCH,PHASE,PROD_ROOT/PENDING,RUNTIME/'pending.json',RUNTIME/'permit.json'):
        _absent(path)
    if type(expected) is not Release:raise ValueError('protected expected predecessor required')
    physical=checked_release(PROD_ROOT,expected.revision+'-'+expected.archive)
    if current_target(PROD_ROOT)!=str(physical):raise ValueError('verified predecessor is not current')
    classify_predecessor(_read(physical/'SHA256SUMS',5*1024*1024),expected)
    _absent(physical/'BASE_BROWSER_LISTENER_CONTRACT')
    if current_target(PROD_ROOT)!=str(physical):raise ValueError('read-only predecessor changed')
    return {'stock_pins':len(STOCK_RO),'predecessor':asdict(expected),'read_only':True,'prepared':False}

def trusted_pid(unit):
    if unit not in (BASE,GRAPH):
        raise ValueError('foreign Base unit probe')
    bus=Path('/run/user/986/bus')
    if bus.resolve(strict=True)!=bus:
        raise ValueError('unexpected existing user bus')
    info=bus.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=986:
        raise ValueError('unexpected existing user bus identity')
    command=['/usr/bin/sudo','-n','-u','assertion-production-deploy','--',
             '/usr/bin/env','-i','XDG_RUNTIME_DIR=/run/user/986',
             'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/986/bus',
             '/usr/bin/systemctl','--user','show','--property=MainPID',unit]
    done=subprocess.run(command,check=True,capture_output=True,timeout=4,env={'PATH':'/usr/bin:/bin','LANG':'C'})
    match=re.fullmatch(rb'MainPID=([0-9]{1,9})\n',done.stdout)
    if match is None:
        raise ValueError('unverified MainPID result')
    return int(match[1])

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('revision')
    parser.add_argument('archive')
    parser.add_argument('manifest')
    opts=parser.parse_args()
    prior=Release(opts.revision,opts.archive,opts.manifest,'legacy')
    evidence=run(prior)
    # Pure, ephemeral readback identity label. This is not an authorization,
    # persisted admission, or action; preflight requires no native journal.
    probe=TerminalRequest(Admission('1','0'*64,prior),prior,'restore')
    observed=observe(probe,trusted_pid=trusted_pid)
    if observed.journal is not None or observed.current!=prior:
        raise ValueError('preflight observed active native transition')
    evidence['legacy_runtime_identity_ok']=True
    print(json.dumps(evidence,sort_keys=True))

if __name__=='__main__':
    main()
