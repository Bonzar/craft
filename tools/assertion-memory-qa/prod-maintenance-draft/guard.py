#!/usr/bin/python3 -I
"""Root-operated base cutover guard; deliberately does not edit ingress or SQLite."""
from contextlib import ExitStack, contextmanager
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
STATUS=Path('/var/lib/assertion-base-prod-status')
SITES='memory-site-graph.service'
NEW_SITES='assertion-memory-prod-site-graph.service'
TIMER='assertion-base-prod-fallback'
SECONDS=600
SQLITE_PROBE=(
    "from pathlib import Path; import sqlite3,sys; "
    "uri=Path(sys.argv[1]).as_uri()+'?mode=ro'; "
    "with_conn=sqlite3.connect(uri,uri=True); "
    "with_conn.execute('PRAGMA query_only=ON'); "
    "with_conn.execute('SELECT count(*) FROM sqlite_schema').fetchone(); "
    "with_conn.close()"
)
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
    run([*identity,'/usr/bin/python3','-I','-B','-c',SQLITE_PROBE,str(DB)],timeout=8)

def exact_owner(account):
    entry=pwd.getpwnam(account)
    require(entry.pw_uid>0 and entry.pw_gid>0,'unexpected custody account')
    return (entry.pw_uid,entry.pw_gid)

@contextmanager
def custody_objects(v):
    """Hold actual inodes open; no path-following, arbitrary paths or SQLite I/O."""
    with ExitStack() as close:
        dfd=os.open(DB.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        close.callback(os.close,dfd)
        ds=os.fstat(dfd)
        require([ds.st_dev,ds.st_ino]==v['directory'] and
                stat.S_ISDIR(ds.st_mode) and stat.S_IMODE(ds.st_mode)==0o700,'database directory drift')
        objects=[(dfd,ds)]; roles={}
        for suffix in ('','-wal','-shm'):
            try:
                fd=os.open(DB.name+suffix,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=dfd)
            except FileNotFoundError:
                require(bool(suffix),'canonical SQLite file missing')
                continue
            close.callback(os.close,fd)
            st=os.fstat(fd)
            require(stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode)==0o600 and st.st_nlink==1 and
                    st.st_dev==ds.st_dev,'unexpected SQLite sidecar type/mode/device')
            if not suffix:
                require([st.st_dev,st.st_ino]==v['inode'],'canonical SQLite inode drift')
            objects.append((fd,st)); roles[suffix]=(st.st_dev,st.st_ino)
        def recheck():
            ds2=DB.parent.lstat()
            require((ds2.st_dev,ds2.st_ino)==(ds.st_dev,ds.st_ino),'directory path replaced')
            present={}
            for suffix in ('','-wal','-shm'):
                try: st=os.stat(DB.name+suffix,dir_fd=dfd,follow_symlinks=False)
                except FileNotFoundError: continue
                present[suffix]=(st.st_dev,st.st_ino)
            require(present==roles,'SQLite pathname set changed during custody handoff')
        recheck()
        yield objects
        recheck()

def assert_unheld(objects):
    want={(s.st_dev,s.st_ino) for _,s in objects}
    require(Path('/proc/self/fd').is_dir(),'Linux process fd inspection required')
    own=os.getpid()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal() or int(proc.name)==own: continue
        try:
            for fd in (proc/'fd').iterdir():
                try: st=fd.stat()
                except FileNotFoundError: continue
                require((st.st_dev,st.st_ino) not in want,
                        f'unquiesced SQLite holder pid {proc.name}')
            with (proc/'maps').open(encoding='ascii',errors='replace') as maps:
                for line in maps:
                    fields=line.split(maxsplit=5)
                    if len(fields)<5 or not fields[4].isdecimal() or int(fields[4])==0: continue
                    major,minor=(int(x,16) for x in fields[3].split(':'))
                    require((os.makedev(major,minor),int(fields[4])) not in want,
                            f'unquiesced SQLite mapping pid {proc.name}')
        except (FileNotFoundError,ProcessLookupError): continue

def no_extended_acl(fd):
    attrs=os.listxattr(fd)
    require(not {'system.posix_acl_access','system.posix_acl_default'}.intersection(attrs),
            'extended SQLite ACL present; refusing ownership handoff')

def custody(v,destination,*,inspect=False):
    old,new=exact_owner(LEGACY_ACCOUNT),exact_owner(ACCOUNT)
    require(old[0]!=new[0] and old[1]!=new[1],'distinct custody identities required')
    require(destination in (old,new),'unexpected custody destination')
    with custody_objects(v) as objects:
        for fd,s in objects:
            no_extended_acl(fd)
            owner=(s.st_uid,s.st_gid)
            require(owner==old if inspect else owner in (old,new),'unexpected SQLite owner')
        if inspect: return
        assert_unheld(objects)
        for fd,st in reversed(objects):
            if (st.st_uid,st.st_gid)!=destination:
                os.fchown(fd,*destination)
            now=os.fstat(fd)
            require((now.st_uid,now.st_gid)==destination and
                    (now.st_dev,now.st_ino)==(st.st_dev,st.st_ino) and
                    stat.S_IMODE(now.st_mode)==stat.S_IMODE(st.st_mode),'custody check failed')
            no_extended_acl(fd)
            os.fsync(fd)
        assert_unheld(objects)

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
    require(digest(current) in (v['old_nginx'],v['new_nginx'],v['frozen_nginx']),'external ingress differs; refusing overwrite')
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

def receiver_identity():
    uid=pwd.getpwnam(ACCOUNT).pw_uid
    require(os.getuid()==uid and os.geteuid()==uid and uid!=0,'wrong receiver runtime identity')

def receive_restart_after_settlement():
    receiver_identity()
    with fence_lock(False) as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        verify_settled()
        permit=FENCE/'permit.json'
        require(not permit.exists() and not permit.is_symlink(),'first-rollout permit still present')
        require(not any(active(u) for u in OLD+(SITES,)),
                'legacy services unexpectedly active; refusing restart')
        run([*userctl(False),'restart',*NEW],timeout=18)
        verify_settled()

def receive_start():
    receiver_identity()
    with fence_lock(False) as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        settled=STATUS/'settled.json'
        require(not settled.exists() and not settled.is_symlink(),'first rollout already settled')
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
    require(set(v)=={'token','inode','old_nginx','new_nginx','old_nginx_mode','phase','deadline','directory','frozen_nginx'} and type(v['token']) is str and len(v['token'])==32
            and all(isinstance(v[k],list) and len(v[k])==2 and all(type(x) is int for x in v[k]) for k in ('inode','directory')),
            'unexpected journal')
    require(all(re.fullmatch('[a-f0-9]{64}',v[k]) for k in ('old_nginx','new_nginx','frozen_nginx')) and
            type(v['old_nginx_mode']) is int and v['old_nginx_mode'] in (0o600,0o640,0o644), 'invalid journal ingress scope')
    require(v['phase'] in ('armed','freeze_pending','frozen','drain_pending','drained','ingress_switched','settled') and type(v['deadline']) is int,'unexpected journal phase')
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

def maintenance_config(baseline):
    candidate=baseline
    for location,port in ((b'/mcp',b'8803'),(b'/memory-graph-read',b'8818')):
        selector=rb'(?m)^(?P<indent>[ \t]*)location[ \t]+=[ \t]+'+re.escape(location)+rb'[ \t]*\{[ \t]*\n(?P<body>.*?)^(?P=indent)\}'
        pattern=re.compile(selector,re.S)
        matches=list(pattern.finditer(candidate))
        require(len(matches)==1,'expected one exact ingress location')
        m=matches[0]; body=m['body']
        upstream=rb'(?m)^([ \t]*)proxy_pass[ \t]+http://127\.0\.0\.1:'+port+rb';[ \t]*$'
        replacement,n=re.subn(upstream,rb'\g<1>return 503;',body)
        require(n==1 and body.count(b'{')==0 and body.count(b'}')==0,
                'unexpected nested or ambiguous ingress block')
        candidate=candidate[:m.start('body')]+replacement+candidate[m.end('body'):]
    return candidate

def status_dir(create=False):
    if create:
        try: STATUS.mkdir(mode=0o755)
        except FileExistsError: pass
        else: STATUS.chmod(0o755)
    st=STATUS.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and st.st_gid==0 and
            stat.S_IMODE(st.st_mode)==0o755,'unsafe stable status directory')

def put_settled(v):
    status_dir(create=True)
    marker=STATUS/'settled.json'
    exact={'version':1,'state':'settled','inode':v['inode']}
    if marker.exists() or marker.is_symlink():
        require(json.loads(safe_bytes(marker))==exact,'stable marker mismatch')
    else:
        replace_file(marker,json.dumps(exact,sort_keys=True).encode('ascii'),0o644)

def verify_settled():
    status_dir()
    marker=STATUS/'settled.json'
    require(stat.S_IMODE(marker.lstat().st_mode)==0o644 and marker.lstat().st_gid==0,'unsafe stable marker mode')
    data=json.loads(safe_bytes(marker))
    require(set(data)=={'version','state','inode'} and type(data['version']) is int and data['version']==1
            and data['state']=='settled' and isinstance(data['inode'],list) and len(data['inode'])==2
            and all(type(i) is int for i in data['inode']),'invalid stable marker')
    exclusive_inode(data['inode'])

def finish_settled(v):
    require(v['phase']=='settled','commit journal not established')
    exclusive_inode(v['inode'])
    stable(False,True)
    require(not active(SITES) and active(NEW_SITES),'unexpected Sites state during settle')
    require(digest(safe_bytes(INGRESS))==v['new_nginx'],'exact approved ingress not installed')
    put_settled(v)
    revoke_permit(); clear()

def arm():
    require(not (STATUS/'settled.json').exists() and not (STATUS/'settled.json').is_symlink(),
            'first rollout already settled')
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
    frozen=maintenance_config(baseline)
    mode=stat.S_IMODE(INGRESS.lstat().st_mode)
    require(mode in (0o600,0o640,0o644) and INGRESS.lstat().st_gid==0,'unexpected ingress mode/group')
    run(['/usr/sbin/nginx','-t'])
    replace_file(STATE/'baseline.nginx',baseline,0o600)
    replace_file(STATE/'frozen.nginx',frozen,0o600)
    parent=DB.parent.lstat()
    v={'token':secrets.token_hex(16),'inode':inode(),'directory':[parent.st_dev,parent.st_ino],'old_nginx':digest(baseline),
       'new_nginx':digest(candidate),'frozen_nginx':digest(frozen),'old_nginx_mode':mode,'phase':'armed','deadline':time.monotonic_ns()+SECONDS*1_000_000_000}
    custody(v,exact_owner(LEGACY_ACCOUNT),inspect=True); write(v)
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

def freeze(v):
    require(active(TIMER+'.timer'),'fallback timer is not active')
    require(v['phase'] in ('armed','freeze_pending','frozen'),'unsafe freeze phase')
    stable(True,False)
    require(active(SITES) and not active(NEW_SITES),'unexpected Sites services before freeze')
    exclusive_inode(v['inode'])
    custody(v,exact_owner(LEGACY_ACCOUNT),inspect=True)
    previous=safe_bytes(STATE/'baseline.nginx')
    frozen=safe_bytes(STATE/'frozen.nginx')
    require(digest(previous)==v['old_nginx'] and
            digest(frozen)==v['frozen_nginx'] and frozen==maintenance_config(previous),'frozen preset drift')
    current=safe_bytes(INGRESS)
    require(digest(current) in (v['old_nginx'],v['frozen_nginx']),'ingress changed before freeze')
    write({**v,'phase':'freeze_pending'})
    if current!=frozen:
        replace_file(INGRESS,frozen,v['old_nginx_mode'])
    run(['/usr/sbin/nginx','-t'])
    run(['/usr/bin/systemctl','reload','nginx.service'])
    write({**v,'phase':'frozen'})
    print('two exact ingress routes frozen; verify external 503 before drain')

def drain(v):
    require(active(TIMER+'.timer'),'fallback timer is not active')
    if v['phase']=='drained':
        stable(False,False); require(not active(SITES),'old adapter running'); exclusive_inode(v['inode']); publish_permit(v); return
    require(v['phase']=='frozen' and digest(safe_bytes(INGRESS))==v['frozen_nginx'],'exact ingress freeze missing')
    stable(True,False)
    require(active(SITES),'Sites reader not active before handoff')
    exclusive_inode(v['inode'])
    verify_database_access(LEGACY_ACCOUNT,True)
    write({**v,'phase':'drain_pending'})
    units('stop',(SITES,))
    units('stop',OLD)
    stable(False,False)
    require(not active(SITES),'old adapter did not drain')
    exclusive_inode(v['inode'])
    custody(v,exact_owner(ACCOUNT))
    write({**v,'phase':'drained'}); publish_permit(v)
    print('legacy units drained; no new writer started; do not change ingress yet')

def switch(v):
    require(active(TIMER+'.timer'),'fallback timer is not active')
    require(v['phase']=='drained','legacy not marked drained')
    stable(False,True) # Only successful separately authorized GitHub activation can make this true.
    exclusive_inode(v['inode'])
    require(not active(SITES) and active(NEW_SITES),'unexpected old/new Sites reader handoff')
    verify_database_access(ACCOUNT,True)
    require(digest(safe_bytes(INGRESS))==v['frozen_nginx'],'frozen ingress drift')
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
    if v['phase']=='settled':
        finish_settled(v)
        return
    revoke_permit()
    exclusive_inode(v['inode'])
    current=digest(safe_bytes(INGRESS))
    require(current in (v['old_nginx'],v['new_nginx'],v['frozen_nginx']),
            'external ingress differs; refusing overwrite')
    if v['phase'] in ('armed','freeze_pending','frozen'):
        stable(True,False)
        require(active(SITES) and not active(NEW_SITES),'unexpected Sites state before drain')
        custody(v,exact_owner(LEGACY_ACCOUNT),inspect=True)
        require(current in (v['old_nginx'],v['frozen_nginx']),'unexpected candidate route before drain')
        if v['phase']!='armed' or current!=v['old_nginx']:
            restore_ingress(v)
        clear()
        print('legacy unchanged; no custody or service mutation')
        return
    units('stop',(NEW_SITES,))
    units('stop',NEW)
    require(not active(NEW_SITES) and not any(active(u) for u in NEW),'new units still active; refusing old restart')
    exclusive_inode(v['inode'])
    custody(v,exact_owner(LEGACY_ACCOUNT))
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
    if args==['receiver-check-settled']:
        receiver_identity()
        return verify_settled()
    if args==['receiver-restart-after-settlement']:
        return receive_restart_after_settlement()
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
        if args==['freeze-exact-ingress']: return freeze(v)
        if args==['drain-after-ingress-frozen']: return drain(v)
        if args==['switch-after-github-activation']: return switch(v)
        if args==['rollback']: return rollback(v)
        if args==['settle-after-live-proof']:
            require(v['phase']=='ingress_switched','ingress not verified switched')
            exclusive_inode(v['inode']); stable(False,True)
            require(not active(SITES) and active(NEW_SITES),'unexpected old/new Sites reader after switch')
            require(digest(safe_bytes(INGRESS))==v['new_nginx'],'exact approved ingress not installed')
            v={**v,'phase':'settled'}
            write(v) # Crash recovery must finish this transition, never reverse an attested settlement.
            finish_settled(v)
            print('fallback disarmed; external live proof operator attested')
            return
        raise RuntimeError('expected arm, drain-after-ingress-frozen, switch-after-github-activation, rollback, settle-after-live-proof, or timer fallback')
if __name__=='__main__':
    main(sys.argv[1:])
