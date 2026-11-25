"""TEST-only Linux inactivity attestation. This process never removes a file."""
import array
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import struct
import subprocess

ROOT=Path('/srv/assertion-release/test')
POLICY=Path('/etc/assertion-release/test/retention-inventory-v1.json')
PROC=Path('/proc')
ACCOUNT='assertion-test-deploy'
UNIT_NAMES=frozenset({'memory-site-test-graph.service','assertion-test-deploy-mcp.service',
                      'assertion-test-deploy-graph.service'})
NAME=re.compile(r'[a-f0-9]{40}-[a-f0-9]{64}\Z')
SHA=re.compile(r'[a-f0-9]{64}\Z')
OK=b'assertion-base-test-retention-v1 OK\n'
MAX_PROC_BYTES=16*1024*1024


def need(condition):
    if not condition:
        raise ValueError('TEST retention proof unavailable')


def read_bound(path, limit=MAX_PROC_BYTES, *, protected=False):
    flags=os.O_RDONLY|os.O_CLOEXEC
    if protected:
        flags|=os.O_NOFOLLOW|os.O_NONBLOCK
        need(path.resolve(strict=True)==path)
        for parent in path.parents:
            p=parent.lstat()
            need(stat.S_ISDIR(p.st_mode) and p.st_uid==0 and not p.st_mode&0o022)
    with os.fdopen(os.open(path,flags),'rb') as stream:
        if protected:
            meta=os.fstat(stream.fileno())
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid==0 and meta.st_nlink==1 and not meta.st_mode&0o022)
        raw=stream.read(limit+1)
    need(len(raw)<=limit)
    return raw


def exact_json(raw):
    def unique(pairs):
        obj={}
        for k,v in pairs:
            need(k not in obj)
            obj[k]=v
        return obj
    return json.loads(raw,object_pairs_hook=unique)


def authority_inventory(uid):
    obj=exact_json(read_bound(POLICY,12000,protected=True))
    need(type(obj) is dict and set(obj)=={'version','uid','units','pins'}
         and type(obj['version']) is int and obj['version']==1 and type(obj['uid']) is int and obj['uid']==uid)
    units=obj['units']
    need(type(units) is dict and set(units)==UNIT_NAMES and len(set(units.values()))==3)
    for name,group in units.items():
        need(type(group) is str and group.startswith('/') and not group.startswith('//') and
             '/..' not in group and group.endswith('/'+name) and '\n' not in group)
        need(group==f'/system.slice/{name}' if name=='memory-site-test-graph.service' else
             group.startswith(f'/user.slice/user-{uid}.slice/user@{uid}.service/'))
    # Pins are baseline attestations, not automatic discovery: require each
    # systemd fragment, all material drop-ins and the fixed wrapper.
    pins=obj['pins']
    need(type(pins) is dict and 4<=len(pins)<=24)
    for path,digest in pins.items():
        item=Path(path)
        authorized=(item.is_relative_to('/etc/systemd/system') or
                    item.is_relative_to('/etc/systemd/user') or
                    item==Path('/opt/assertion-memory-site-test-graph/start.py'))
        need(authorized and str(item)==path and isinstance(digest,str) and SHA.fullmatch(digest))
        need(hashlib.sha256(read_bound(item,131072,protected=True)).hexdigest()==digest)
    required={'/etc/systemd/system/memory-site-test-graph.service',
              '/etc/systemd/user/assertion-test-deploy-graph.service',
              '/etc/systemd/user/assertion-test-deploy-mcp.service',
              '/opt/assertion-memory-site-test-graph/start.py'}
    need(required<=pins.keys())
    for name,group in units.items():
        argv=['/usr/bin/systemctl']
        if name!='memory-site-test-graph.service':
            argv.extend(['--user','--machine='+ACCOUNT+'@.host'])
        argv.extend(['show','--property=FragmentPath,DropInPaths,ControlGroup,ActiveState','--',name])
        result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              timeout=2,check=True,env={'PATH':'/usr/bin:/bin','LANG':'C'})
        lines=result.stdout.decode().splitlines()
        need(len(lines)==4)
        actual=dict(line.split('=',1) for line in lines)
        need(set(actual)=={'FragmentPath','DropInPaths','ControlGroup','ActiveState'})
        need(actual['ActiveState']=='active' and actual['ControlGroup']==group)
        need(all(f in pins for f in [actual['FragmentPath'],*actual['DropInPaths'].split()]))
    return units


def check_lock(root, fd):
    path=root/'deploy.lock'
    lock=path.lstat()
    need(stat.S_ISREG(lock.st_mode) and lock.st_nlink==1 and os.path.samestat(lock,os.fstat(fd)))
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as other:
        need(os.path.samestat(lock,os.fstat(other.fileno())))
        try:
            fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise ValueError('TEST deployment lock not held')
        # Duplicate of already holding description succeeds; an unrelated
        # descriptor of the same inode remains blocked. Never unlock this FD.
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)


def within(candidate, value):
    """Path-component boundary even for deleted proc targets."""
    value=value.removesuffix(b' (deleted)')
    return value==candidate or value.startswith(candidate+b'/')


def mount_hits(records, candidate, source_dev=None):
    count=0
    for record in records.splitlines():
        fields=record.split()
        need(len(fields)>=10 and b'-' in fields[6:])
        if source_dev is not None and fields[2]==source_dev:
            count+=1
        for part in fields[3:5]:
            path=re.sub(rb'\\([0-7]{3})',lambda m:bytes((int(m[1],8),)),part)
            if within(candidate,path):
                return True
    # Extra mounts of the same filesystem may expose the release through an
    # alias whose mountinfo root is not an absolute spelling of the live path.
    return source_dev is not None and count!=1


def listed_pids(proc):
    return sorted((x for x in proc.iterdir() if x.name.isascii() and x.name.isdecimal()),key=lambda x:int(x.name))


def seal(pid):
    raw=read_bound(pid/'stat',32768)
    end=raw.rfind(b')')
    fields=raw[end+2:].split()
    need(end>0 and len(fields)>=20)
    return fields[19],fields[0]


def processes(proc, root, active, stale, units, uid):
    candidate=os.fsencode(stale)
    now=os.fsencode(active)
    member=set()
    initial=listed_pids(proc)
    sealed={}
    namespaces=set()
    dev=stale.stat().st_dev
    devnum=f'{os.major(dev)}:{os.minor(dev)}'.encode()
    for p in initial:
        identity,kind=seal(p)
        sealed[p]=identity
        if kind in (b'Z',b'X'):
            continue
        status=read_bound(p/'status',65536)
        kernel=re.search(rb'^Kthread:\s+1$',status,re.M)
        cmd=read_bound(p/'cmdline',4*1024*1024)
        # Empty args alone is not sufficient to classify a kernel thread.
        if kernel and not cmd:
            continue
        paths=[os.fsencode(os.readlink(p/part)) for part in ('cwd','root','exe')]
        paths.extend(os.fsencode(os.readlink(fd)) for fd in (p/'fd').iterdir())
        need(not any(within(candidate,path) for path in paths))
        for line in read_bound(p/'maps').splitlines():
            cols=line.split(None,5)
            if len(cols)==6:
                need(not within(candidate,cols[5]))
        # Paths can be delimited within assigned environment/argument values.
        realm=read_bound(p/'environ',4*1024*1024)
        need(candidate not in cmd and candidate not in realm)
        ns=os.readlink(p/'ns'/'mnt')
        if ns not in namespaces:
            need(not mount_hits(read_bound(p/'mountinfo'),candidate,devnum))
            namespaces.add(ns)
        cgroups=read_bound(p/'cgroup',65536).splitlines()
        if re.search(rb'^Uid:\s+'+str(uid).encode()+rb'\s',status,re.M):
            test_manager=f'/user.slice/user-{uid}.slice/user@{uid}.service/'.encode()
            for group in cgroups:
                if group.startswith(b'0::'+test_manager):
                    # Any newly configured TEST service could later restart
                    # with a historical release, so require explicit inventory.
                    suffix=group[len(b'0::'+test_manager):]
                    if b'.service' in suffix:
                        need(any(group==b'0::'+cg.encode() or group.startswith(b'0::'+cg.encode()+b'/')
                                 for cg in units.values()))
        for name,cgroup in units.items():
            if (b'0::'+cgroup.encode()) in cgroups and (now in cmd or now in realm or any(within(now,x) for x in paths)):
                need(re.search(rb'^Uid:\s+'+str(uid).encode()+rb'\s+'+str(uid).encode()+rb'\s',status,re.M))
                member.add(name)
    need(member==UNIT_NAMES and listed_pids(proc)==initial)
    need(all(seal(p)[0]==identity for p,identity in sealed.items()))


def receive_request(conn):
    fds=array.array('i')
    raw,ancillary,flags,_=conn.recvmsg(512,socket.CMSG_SPACE(2*fds.itemsize))
    try:
        for level,kind,data in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                fds.frombytes(data[:len(data)-len(data)%fds.itemsize])
        need(flags==0 and len(fds)==1 and len(ancillary)==1)
        need(0<len(raw)<512 and raw.endswith(b'\n') and conn.recv(1)==b'')
        r=exact_json(raw)
        need(type(r) is dict and set(r)=={'version','lane','active','inactive'}
             and type(r['version']) is int and r['version']==1 and r['lane']=='test')
        need(all(type(r[k]) is str and NAME.fullmatch(r[k]) for k in ('active','inactive'))
             and r['active']!=r['inactive'])
        return r,fds.pop()
    finally:
        for fd in fds:
            os.close(fd)


def prove(conn):
    conn.settimeout(5)
    _,uid,_=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    need(os.geteuid()==0 and uid==pwd.getpwnam(ACCOUNT).pw_uid)
    req,lock=receive_request(conn)
    try:
        need(ROOT.resolve(strict=True)==ROOT)
        check_lock(ROOT,lock)
        active,stale=(ROOT/'releases'/req[k] for k in ('active','inactive'))
        def current():
            need(not os.path.lexists(ROOT/'.activation-pending-v1') and os.readlink(ROOT/'current')==str(active))
            for item in (active,stale):
                st=item.lstat()
                need(item.resolve(strict=True)==item and stat.S_ISDIR(st.st_mode) and st.st_uid==uid)
        current()
        units=authority_inventory(uid)
        processes(PROC,ROOT,active,stale,units,uid)
        need(authority_inventory(uid)==units)
        current()
        check_lock(ROOT,lock)
        conn.sendall(OK)
    finally:
        os.close(lock)


def main():
    # Socket activation for fixed local authority only; never open a port or
    # run a command supplied by a client.
    need(os.environ.get('LISTEN_PID')==str(os.getpid()) and os.environ.get('LISTEN_FDS')=='1')
    with socket.socket(fileno=3) as server:
        need(server.family==socket.AF_UNIX and server.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)==1
             and server.getsockname()=='/run/assertion-base-test-retention/authority.sock')
        while True:
            conn,_=server.accept()
            with conn:
                try:
                    prove(conn)
                except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError):
                    pass  # Empty response is denial; never log proc fields.


if __name__=='__main__':
    main()
