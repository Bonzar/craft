#!/usr/bin/python3 -I
"""Root-operated base cutover guard; deliberately does not edit ingress or SQLite."""
import fcntl
import hashlib
import pwd
import re
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time

DB=Path('/var/lib/assertion-memory/memory.sqlite3')
STATE=Path('/var/lib/assertion-base-prod-transition')
INSTALLED='/usr/local/sbin/assertion-base-prod-transition'
OLD=('assertion-memory-mcp.service','assertion-memory-graph.service')
NEW=('assertion-production-deploy-mcp.service','assertion-production-deploy-graph.service')
ACCOUNT='assertion-production-deploy'
LEGACY_ACCOUNT='assertion-memory-qa'
INGRESS=Path('/etc/nginx/conf.d/assertion-memory-probe-tls.conf')
FENCE=Path('/run/assertion-base-prod-transition')
SITES='memory-site-graph.service'
NEW_SITES='assertion-memory-prod-site-graph.service'
TIMER='assertion-base-prod-fallback'
SECONDS=180
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'}

def require(ok,msg):
    if not ok: raise RuntimeError(msg)

def run(args,check=True,timeout=18):
    return subprocess.run(args,check=check,timeout=timeout,stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=ENV).returncode

def userctl(root_side=True):
    uid=pwd.getpwnam(ACCOUNT).pw_uid
    bus=Path(f'/run/user/{uid}/bus')
    require(stat.S_ISSOCK(bus.stat().st_mode) and bus.stat().st_uid==uid,'missing/untrusted new runtime systemd bus')
    return (['/usr/sbin/runuser','-u',ACCOUNT,'--'] if root_side else [])+['/usr/bin/env',
            f'XDG_RUNTIME_DIR=/run/user/{uid}',f'DBUS_SESSION_BUS_ADDRESS=unix:path={bus}',
            '/usr/bin/systemctl','--user']

def active(unit):
    ctl=userctl() if unit in NEW else ['/usr/bin/systemctl']
    state=subprocess.run([*ctl,'show','--property=LoadState','--property=ActiveState',unit],
                         check=True,capture_output=True,text=True,timeout=10,env=ENV).stdout.splitlines()
    require('LoadState=loaded' in state,'expected systemd unit is not loaded')
    states={line.removeprefix('ActiveState=') for line in state if line.startswith('ActiveState=')}
    require(len(states)==1 and states<= {'active','inactive','failed'},'ambiguous/transitional systemd state')
    return states=={'active'}

def units(action, names):
    if action not in ('stop','start') or names not in (OLD,NEW,(SITES,),(NEW_SITES,)):
        raise ValueError('non-allowlisted systemd operation')
    ctl=userctl() if names==NEW else ['/usr/bin/systemctl']
    run([*ctl,action,*names])

def verify_database_access(account,writable):
    require(account in (ACCOUNT,LEGACY_ACCOUNT),'unexpected local database account')
    identity=['/usr/sbin/runuser','-u',account,'--']
    if writable:
        for path in (DB.parent,DB,*[p for p in (Path(str(DB)+'-wal'),Path(str(DB)+'-shm')) if p.exists()]):
            run([*identity,'/usr/bin/test','-w',str(path)])
        run([*identity,'/usr/bin/test','-x',str(DB.parent)])
    run([*identity,'/usr/bin/sqlite3','-batch','-readonly',str(DB),
         'PRAGMA query_only=ON; SELECT count(*) FROM sqlite_schema;'])

def safe_bytes(p):
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1 and
                not stat.S_IMODE(st.st_mode)&0o022 and st.st_size<128_000,'unsafe trusted ingress file')
        return f.read()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def replace_file(p,content,mode):
    fd,name=tempfile.mkstemp(prefix='.assertion-safe-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),mode); f.write(content); f.flush(); os.fsync(f.fileno())
        os.replace(name,p)
        fd=os.open(p.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW); os.fsync(fd); os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)

def restore_ingress(v):
    previous=safe_bytes(STATE/'baseline.nginx')
    require(digest(previous)==v['old_nginx'],'stored baseline ingress mismatch')
    current=safe_bytes(INGRESS)
    require(digest(current) in (v['old_nginx'],v['new_nginx']),'external ingress differs; refusing overwrite')
    if current!=previous:
        require(INGRESS.lstat().st_gid==0,'unexpected nginx group')
        replace_file(INGRESS,previous,v['old_nginx_mode'])
    run(['/usr/sbin/nginx','-t'])
    run(['/usr/bin/systemctl','reload','nginx.service'])

def fence_lock(root_side):
    if root_side:
        try: FENCE.mkdir(mode=0o755)
        except FileExistsError: pass
        else: FENCE.chmod(0o755)
        try: fd=os.open(FENCE/'fence.lock',os.O_CREAT|os.O_EXCL|os.O_RDONLY|os.O_NOFOLLOW,0o644)
        except FileExistsError: fd=os.open(FENCE/'fence.lock',os.O_RDONLY|os.O_NOFOLLOW)
        else: os.fchmod(fd,0o644)
    else:
        fd=os.open(FENCE/'fence.lock',os.O_RDONLY|os.O_NOFOLLOW)
    directory=FENCE.lstat()
    lock=os.fstat(fd)
    require(stat.S_ISDIR(directory.st_mode) and directory.st_uid==0 and
            stat.S_IMODE(directory.st_mode)==0o755 and stat.S_ISREG(lock.st_mode) and
            lock.st_uid==0 and lock.st_nlink==1 and stat.S_IMODE(lock.st_mode)==0o644,'unsafe local transition fence')
    return os.fdopen(fd,'rb')

def publish_permit(v):
    replace_file(FENCE/'permit.json',json.dumps({
        'generation':v['token'],'expiry':v['deadline'],'inode':v['inode']
    },sort_keys=True).encode('ascii'),0o644)

def revoke_permit():
    try: (FENCE/'permit.json').unlink()
    except FileNotFoundError: return
    fd=os.open(FENCE,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    os.fsync(fd); os.close(fd)

def receive_start():
    uid=pwd.getpwnam(ACCOUNT).pw_uid
    require(os.getuid()==uid and os.geteuid()==uid and uid!=0,'wrong receiver runtime identity')
    with fence_lock(False) as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        p=json.loads(safe_bytes(FENCE/'permit.json'))
        require(set(p)=={'generation','expiry','inode'} and
                re.fullmatch('[0-9a-f]{32}',p['generation']) and type(p['expiry']) is int and
                time.monotonic_ns()+50_000_000_000 < p['expiry'],'expired or invalid maintenance permit')
        exclusive_inode(p['inode'])
        # No root operation. Hold same kernel lock across the whole start/check.
        ctl=userctl(False)
        try:
            run([*ctl,'start',*NEW],timeout=18)
            result=subprocess.run([*ctl,'is-active',*NEW],check=True,capture_output=True,
                                  text=True,env=ENV,timeout=5)
            require(result.stdout.splitlines()==['active','active'],'new runtime did not activate')
            exclusive_inode(p['inode'])
            require(time.monotonic_ns()<p['expiry'],'maintenance expired during startup')
        except BaseException:
            run([*ctl,'stop',*NEW],timeout=15)
            raise

def inode():
    s=DB.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink==1,'canonical SQLite path is not a normal unmoved file')
    return [s.st_dev,s.st_ino]

def exclusive_inode(expected):
    require(inode()==expected,'canonical SQLite inode changed; no automatic activation')

def stable(old,new):
    require(all(active(u)==old for u in OLD) and all(active(u)==new for u in NEW),
            'unexpected producer unit state')

def syncdir():
    fd=os.open(STATE,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW); os.fsync(fd); os.close(fd)

def read():
    p=STATE/'pending.json'
    try: fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    except FileNotFoundError: return None
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno()); require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size<1024,'unsafe journal')
        v=json.load(f)
    require(set(v)=={'token','inode','old_nginx','new_nginx','old_nginx_mode','phase','deadline'} and type(v['token']) is str and len(v['token'])==32
            and isinstance(v['inode'],list) and len(v['inode'])==2 and all(type(x) is int for x in v['inode']),
            'unexpected journal')
    require(all(re.fullmatch('[a-f0-9]{64}',v[k]) for k in ('old_nginx','new_nginx')) and
            type(v['old_nginx_mode']) is int and v['old_nginx_mode'] in (0o600,0o640,0o644), 'invalid journal ingress scope')
    require(v['phase'] in ('armed','drained','ingress_switched') and type(v['deadline']) is int,'unexpected journal phase')
    return v

def write(v):
    fd,p=tempfile.mkstemp(prefix='.pending-', dir=STATE)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(v,f,sort_keys=True); f.flush(); os.fsync(f.fileno())
        os.replace(p,STATE/'pending.json'); syncdir()
    finally:
        if os.path.exists(p): os.unlink(p)

def clear():
    (STATE/'pending.json').unlink(); syncdir()

def arm():
    require(read() is None,'changed transition already armed'); revoke_permit()
    stable(True,False); require(active(SITES) and not active(NEW_SITES),'unexpected old/new Sites reader state')
    baseline=safe_bytes(INGRESS)
    candidate=safe_bytes(STATE/'approved-candidate.nginx')
    ports=((b'http://127.0.0.1:8803',b'http://127.0.0.1:8903'),
           (b'http://127.0.0.1:8818',b'http://127.0.0.1:8918'))
    expected=baseline
    for old,new in ports:
        require(expected.count(old)==1 and new not in expected,'unexpected unique upstream')
        expected=expected.replace(old,new)
    require(candidate==expected,'candidate must differ only by the two exact upstream ports')
    mode=stat.S_IMODE(INGRESS.lstat().st_mode)
    require(mode in (0o600,0o640,0o644) and INGRESS.lstat().st_gid==0,'unexpected ingress mode/group')
    run(['/usr/sbin/nginx','-t'])
    replace_file(STATE/'baseline.nginx',baseline,0o600)
    v={'token':secrets.token_hex(16),'inode':inode(),'old_nginx':digest(baseline),
       'new_nginx':digest(candidate),'old_nginx_mode':mode,'phase':'armed','deadline':time.monotonic_ns()+SECONDS*1_000_000_000}; write(v)
    try:
        run(['/usr/bin/systemd-run','--collect','--quiet','--unit='+TIMER,
             '--on-active='+str(SECONDS)+'s','--timer-property=AccuracySec=1s',
             '--property=RuntimeMaxSec=120s','--property=Restart=on-failure',
             '--property=RestartSec=5s','--property=StartLimitBurst=3',INSTALLED,'fallback',v['token']])
        require(active(TIMER+'.timer'),'fallback timer not active')
    except BaseException:
        # Do not allow subsequent cutover if arm receipt was uncertain.
        raise
    print('fallback armed; verify external ingress stays on legacy before switch')

def drain(v):
    require(active(TIMER+'.timer'),'fallback timer is not active')
    if v['phase']=='drained':
        stable(False,False); require(not active(SITES),'old adapter running'); exclusive_inode(v['inode']); publish_permit(v); return
    require(v['phase']=='armed','unexpected drain state')
    stable(True,False)
    require(active(SITES),'Sites reader not active before handoff')
    exclusive_inode(v['inode'])
    verify_database_access(LEGACY_ACCOUNT,True)
    units('stop',(SITES,))
    units('stop',OLD)
    stable(False,False)
    require(not active(SITES),'old adapter did not drain')
    exclusive_inode(v['inode'])
    write({**v,'phase':'drained'}); publish_permit(v)
    print('legacy units drained; no new writer started; do not change ingress yet')

def switch(v):
    require(active(TIMER+'.timer'),'fallback timer is not active')
    require(v['phase']=='drained','legacy not marked drained')
    stable(False,True) # Only successful separately authorized GitHub activation can make this true.
    exclusive_inode(v['inode'])
    require(not active(SITES) and active(NEW_SITES),'unexpected old/new Sites reader handoff')
    verify_database_access(ACCOUNT,True)
    require(digest(safe_bytes(INGRESS))==v['old_nginx'],'legacy ingress drift')
    candidate=safe_bytes(STATE/'approved-candidate.nginx')
    require(digest(candidate)==v['new_nginx'],'candidate changed after arming')
    try:
        replace_file(INGRESS,candidate,v['old_nginx_mode'])
        run(['/usr/sbin/nginx','-t'])
        run(['/usr/bin/systemctl','reload','nginx.service'])
        write({**v,'phase':'ingress_switched'})
        print('candidate ingress selected; verify MCP, graph and Sites reads before settle')
    except BaseException:
        rollback(v)
        raise

def rollback(v):
    revoke_permit()
    exclusive_inode(v['inode'])
    require(digest(safe_bytes(INGRESS)) in (v['old_nginx'],v['new_nginx']),
            'external ingress differs; refusing overwrite')
    units('stop',(NEW_SITES,))
    units('stop',NEW)
    require(not active(NEW_SITES) and not any(active(u) for u in NEW),'new units still active; refusing old restart')
    exclusive_inode(v['inode'])
    verify_database_access(LEGACY_ACCOUNT,True)
    units('start',OLD)
    stable(True,False)
    units('start',(SITES,))
    restore_ingress(v)
    require(active(SITES),'Sites reader not active after rollback')
    clear()
    print('legacy units active; SQLite inode retained')

def main(args):
    require(Path(__file__).resolve()==Path(INSTALLED),'use installed, reviewed fixed path')
    p=Path(INSTALLED).lstat()
    require(stat.S_ISREG(p.st_mode) and p.st_uid==0 and not stat.S_IMODE(p.st_mode)&0o022,'unsafe installed code')
    if args==['receiver-first-start']:
        return receive_start()
    require(os.getuid()==0 and os.geteuid()==0,'root only')
    os.umask(0o077); STATE.mkdir(mode=0o700,exist_ok=True)
    s=STATE.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o700,'unsafe state')
    with fence_lock(True) as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if args==['arm']: return arm()
        v=read()
        if len(args)==2 and args[0]=='fallback':
            if v is None or v['token']!=args[1]: return
            return rollback(v)
        require(v is not None,'no pending fallback')
        if args==['drain-after-ingress-frozen']: return drain(v)
        if args==['switch-after-github-activation']: return switch(v)
        if args==['rollback']: return rollback(v)
        if args==['settle-after-live-proof']:
            require(v['phase']=='ingress_switched','ingress not verified switched')
            exclusive_inode(v['inode']); stable(False,True)
            require(not active(SITES) and active(NEW_SITES),'unexpected old/new Sites reader after switch')
            require(digest(safe_bytes(INGRESS))==v['new_nginx'],'exact approved ingress not installed')
            revoke_permit(); clear(); print('fallback disarmed; external live proof operator attested')
            return
        raise RuntimeError('expected arm, drain-after-ingress-frozen, switch-after-github-activation, rollback, settle-after-live-proof, or timer fallback')
if __name__=='__main__':
    main(sys.argv[1:])
