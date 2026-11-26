"""Bounded kernel observation for the independent Base terminal verifier."""
from itertools import islice
from pathlib import Path
import os
import re
import stat

from terminal_verifier import BASE, GRAPH, Process, Snapshot
from native_boundary import observe_native_boundary

PROC=Path('/proc')
PORTS=(8901,8902,8903)
UID=986
BOOT=re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\n\Z')

def _read(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('invalid observation file')
        body=stream.read(limit+1)
    if len(body)>limit: raise ValueError('oversize observation')
    return body.decode('ascii')

def start_ticks(wire,pid):
    head,delimiter,tail=wire.rpartition(') ')
    if not delimiter or not head.startswith(str(pid)+' ('):
        raise ValueError('invalid process stat identity')
    parts=tail.split()
    if len(parts)<20 or not re.fullmatch('[1-9][0-9]{0,19}',parts[19]):
        raise ValueError('invalid process start ticks')
    return int(parts[19])

def _tcp(wire):
    """Every local TCP listener on our ports; reject wildcard/foreign use."""
    found={}
    for row in wire.splitlines()[1:]:
        parts=row.split()
        if len(parts)<10:
            raise ValueError('invalid kernel socket row')
        address,sep,port=parts[1].partition(':')
        if not sep or len(port)!=4:
            raise ValueError('invalid kernel socket address')
        number=int(port,16)
        if number not in PORTS or parts[3]!='0A':
            continue
        if address!='0100007F' or number in found or int(parts[7])!=UID:
            raise ValueError('noncanonical or competing Base socket')
        inode=int(parts[9])
        if inode<=0: raise ValueError('invalid Base socket inode')
        found[number]=inode
    return found

def _process(proc, *, pid, unit, release, physical):
    if type(pid) is not int or pid<=0 or unit not in (BASE,GRAPH):
        raise ValueError('unexpected observed service')
    root=proc/str(pid)
    first=start_ticks(_read(root/'stat',8192),pid)
    status=_read(root/'status',32768)
    uids=[line.split()[1:] for line in status.splitlines() if line.startswith('Uid:')]
    if uids!=[[str(UID)]*4]:
        raise ValueError('unexpected Base UID')
    groups=_read(root/'cgroup',16384)
    if not any(row.split(':',2)[-1].split('/')[-1]==unit for row in groups.splitlines()):
        raise ValueError('wrong systemd Base unit cgroup')
    if (root/'cwd').resolve(strict=True)!=physical:
        raise ValueError('wrong loaded Base archive working directory')
    if not os.path.samestat((root/'ns'/'net').stat(),(proc/'self'/'ns'/'net').stat()):
        raise ValueError('unexpected Base network namespace')
    exe=(root/'exe').resolve(strict=True)
    interpreter=(physical/'venv'/'bin'/'python').resolve(strict=True)
    if not interpreter.is_file() or exe!=interpreter:
        raise ValueError('wrong protected process executable')
    cmd=_read(root/'cmdline',32768).split('\0')
    module='prototypes.assertion_memory.http_server' if unit==BASE else 'apps.graph_viewer.server'
    if cmd.count('-m')!=1 or cmd[cmd.index('-m')+1:cmd.index('-m')+2]!=[module]:
        raise ValueError('wrong serving module')
    if unit==BASE:
        config='/srv/assertion-release/production/state/http.json'
        if cmd.count('--config')!=1 or cmd[cmd.index('--config')+1:cmd.index('--config')+2]!=[config]:
            raise ValueError('wrong protected Base config')
    fds=list(islice((root/'fd').iterdir(),4097))
    if len(fds)>4096: raise ValueError('too many open process descriptors')
    owned=set()
    for f in fds:
        try:
            link=os.readlink(f)
        except FileNotFoundError:
            continue
        m=re.fullmatch(r'socket:\[(\d+)\]',link)
        if m: owned.add(int(m[1]))
    if start_ticks(_read(root/'stat',8192),pid)!=first:
        raise ValueError('Base process changed during observation')
    return Process(unit,pid,first,UID,release),owned

def observe(request, *, trusted_pid):
    """trusted_pid is exact systemd unit query from the protected controller."""
    return _observe(PROC,request,trusted_pid,observe_native_boundary)

def _observe(proc,request,trusted_pid,boundary):
    boot=_read(proc/'sys/kernel/random/boot_id',64)
    if not BOOT.fullmatch(boot): raise ValueError('invalid kernel boot identity')
    release,journal=boundary(request)
    physical=Path('/srv/assertion-release/production/releases')/(release.revision+'-'+release.archive)
    main_pid=trusted_pid(BASE)
    base,base_inodes=_process(proc,pid=main_pid,unit=BASE,release=release,physical=physical)
    graph_pid=trusted_pid(GRAPH)
    if release.mode=='sidecar':
        if graph_pid!=0: raise ValueError('legacy Graph is still running')
        graph,graph_inodes=base,base_inodes
        graph_port=8902
    else:
        graph,graph_inodes=_process(proc,pid=graph_pid,unit=GRAPH,release=release,physical=physical)
        graph_port=8901
    sockets=_tcp(_read(proc/'net/tcp',1024*1024))
    if _tcp(_read(proc/'net/tcp6',1024*1024)):
        raise ValueError('unexpected IPv6 listener on Base ports')
    if (set(sockets)!={graph_port,8903} or sockets[8903] not in base_inodes
            or sockets[graph_port] not in graph_inodes or graph_port!=8903 and sockets[graph_port]==sockets[8903]):
        raise ValueError('Base process socket ownership mismatch')
    if (any(start_ticks(_read(proc/str(p.pid)/'stat',8192),p.pid)!=p.start_ticks for p in {base,graph})
            or _read(proc/'sys/kernel/random/boot_id',64)!=boot or trusted_pid(BASE)!=main_pid
            or trusted_pid(GRAPH)!=graph_pid or boundary(request)!=(release,journal)):
        raise ValueError('protected Base observation changed')
    return Snapshot(release,base,graph,((graph_port,graph.pid),(8903,base.pid)),journal,boot.strip())
