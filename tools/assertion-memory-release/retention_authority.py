"""Read-only release retirement witness. Never deletes or repairs host state."""
from array import array
import fcntl
import json
import hashlib
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import struct
import sys

OK = b'assertion-release-retention-v1 OK\n'
SOCKET_ROOT = Path('/run/assertion-release-retention')
PROFILE_ROOT = Path('/etc/assertion-release-retention')
SCOPES = {
    'base-test': ('assertion-test-deploy', '/srv/assertion-release/test'),
    'base-prod': ('assertion-production-deploy', '/srv/assertion-release/production'),
    **{f'visual-{lane}': (f'assertion-visual-{lane}-deploy',
                        f'/var/lib/assertion-memory-visual/{lane}') for lane in ('test','prod')},
}
DIGEST = re.compile('[a-f0-9]{64}')
BASE_ID = re.compile('[a-f0-9]{40}-[a-f0-9]{64}')
FIELDS = {'version','scope','store','active','inactive','dev','ino'}
SIZE = 768
PROFILE_SIZE = 16_384
MAX_PROC_ARGS = 4*1024*1024
PROD_GUARD = Path('/var/lib/assertion-base-prod-transition')
PIN_ROOTS = tuple(Path(p) for p in ('/etc/systemd/system','/etc/systemd/user',
             '/etc/assertion-release','/etc/assertion-memory-visual',
             '/opt/assertion-memory-prod-site-graph','/opt/assertion-memory-site-test-graph'))

class Denied(ValueError):
    pass

def require(condition):
    if not condition:
        raise Denied('retention_not_proven')

def pair(st):
    return st.st_dev, st.st_ino

def seal(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid, st.st_nlink,
            st.st_size, st.st_ctime_ns, st.st_mtime_ns)

def unique(pairs):
    out = dict(pairs)
    require(len(out) == len(pairs))
    return out

def request(raw, scope):
    require(0 < len(raw) <= SIZE and raw.count(b'\n') == 1 and raw.endswith(b'\n'))
    q = json.loads(raw, object_pairs_hook=unique)
    require(type(q) is dict and q.keys() == FIELDS and type(q['version']) is int
            and q['version'] == 1 and q['scope'] == scope and scope in SCOPES)
    allowed = ('base',) if scope.startswith('base-') else ('visual-static','visual-runtime')
    require(q['store'] in allowed)
    rx = BASE_ID if q['store'] == 'base' else DIGEST
    require(all(type(q[k]) is str and rx.fullmatch(q[k]) for k in ('active','inactive')))
    require(q['active'] != q['inactive'])
    require(all(type(q[k]) is int and 0 < q[k] < (1 << 64) for k in ('dev','ino')))
    return q

def real_dir(path, owners):
    require(path.resolve(strict=True) == path)
    for p in (*reversed(path.parents), path):
        st = p.lstat()
        require(stat.S_ISDIR(st.st_mode) and st.st_uid in owners and not st.st_mode & 0o022)
    return path.lstat()

def pinned_launchers(pins, *, roots=PIN_ROOTS, owner=0):
    # Root material includes unit fragments and non-secret, root-owned
    # effective drop-ins/wrappers. Never accept arbitrary host roots.
    require(type(pins) is dict and 2<=len(pins)<=32)
    out={}
    for text,want in pins.items():
        path=Path(text)
        require(type(text) is str and path.is_absolute() and str(path)==text
                and any(path.is_relative_to(root) for root in roots)
                and path.suffix in {'.service','.conf','.sh','.py'}
                and type(want) is str and DIGEST.fullmatch(want)
                and path.resolve(strict=True)==path)
        real_dir(path.parent,{owner})
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as source:
            info=os.fstat(source.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid==owner and info.st_nlink==1
                    and not info.st_mode&0o022 and 0<info.st_size<=262144)
            raw=source.read(262145)
            require(len(raw)==info.st_size and hashlib.sha256(raw).hexdigest()==want)
            require(seal(path.lstat())==seal(info))
            out[text]=seal(info),want
    require(any(text.endswith('.service') for text in out))
    return out


def trusted_profile(scope, uid):
    """A root operator must attest host PID visibility and deny non-current opens."""
    require(os.geteuid() == 0 and uid != 0)
    real_dir(PROFILE_ROOT, {0})
    f = PROFILE_ROOT / (scope + '.json')
    fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as src:
        st = os.fstat(src.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1
                and st.st_mode & 0o777 == 0o600 and 0 < st.st_size <= PROFILE_SIZE)
        raw = src.read(PROFILE_SIZE + 1)
    require(len(raw)==st.st_size)
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict and value.keys() == {'scope','uid','host_complete','current_only','pins'}
            and value['scope'] == scope and type(value['uid']) is int and value['uid'] == uid
            and value['host_complete'] is True and value['current_only'] is True)
    return seal(st), raw, pinned_launchers(value['pins'])

def verify_lock(root, fd, uid):
    st = os.fstat(fd)
    require(stat.S_ISREG(st.st_mode) and st.st_uid == uid and st.st_nlink == 1
            and not st.st_mode & 0o077 and seal(st) == seal((root/'deploy.lock').lstat()))
    dev = f'{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}'
    info = Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines()
    locks = [line.split() for line in info if line.startswith('lock:')]
    require(len(locks) == 1 and len(locks[0]) >= 9
            and locks[0][2:5] == ['FLOCK','ADVISORY','WRITE']
            and inode_field(locks[0][6]) == inode_field(dev) and locks[0][7:] == ['0','EOF'])
    # Only check the received OFD. Never acquire/upgrade a caller's bare lock.
    probe = os.open(root/'deploy.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(pair(os.fstat(probe)) == pair(st))
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return seal(st)
        raise Denied('retention_not_proven')
    finally:
        os.close(probe)

def inode_field(field):
    major, minor, ino = field.split(':')
    return os.makedev(int(major,16), int(minor,16)), int(ino)

def pointer_seal(root, is_visual, uid):
    roots = [(root,'releases','.activation-pending-v1')]
    if root == Path(SCOPES['base-prod'][1]):
        # Separate older PROD cutover/rollback control must not retain a
        # candidate reference while the common writer runs GC.
        require(not any(os.path.lexists(PROD_GUARD/name)
                        for name in ('pending.json','permit.json')))
    if is_visual:
        roots.append((root/'runtime','versions','.runtime-transition'))
        require(not os.path.lexists(root/'runtime'/'.visual-runtime-joint-transition'))
    out = {}
    for folder, store, journal in roots:
        require(not os.path.lexists(folder/journal))
        st = real_dir(folder, {0,uid})
        actual = folder/'current'
        link = actual.lstat()
        require(stat.S_ISLNK(link.st_mode) and link.st_uid == uid)
        raw = os.readlink(actual)
        rx = DIGEST if is_visual else BASE_ID
        name = Path(raw).name
        require(rx.fullmatch(name))
        absolute = folder/store/name
        require(raw in ({str(absolute), f'{store}/{name}'} if is_visual else {str(absolute)}))
        active = real_dir(absolute,{0,uid})
        out[store] = (name, seal(st), seal(link), seal(active))
    return out

VENV_PYTHON = frozenset({'python','python3', *(f'python3.{minor}' for minor in range(11,15))})

def generated_venv_link(relative, target):
    if relative.parts == ('venv','lib64'):
        return target == 'lib'
    if (len(relative.parts) != 3 or relative.parts[:2] != ('venv','bin')
            or relative.name not in VENV_PYTHON):
        return False
    # Python's POSIX venv symlink layout: interpreter aliases refer to a
    # sibling, and the base interpreter refers to its system binary.
    return (target != relative.name and target in VENV_PYTHON
            or target in {f'/usr/bin/{name}' for name in VENV_PYTHON})

def tree(path, uid, device, *, base=False):
    found = {}
    todo = [path]
    while todo:
        p = todo.pop()
        st = p.lstat()
        require(st.st_dev == device and st.st_uid in {0,uid})
        lexical = None
        if stat.S_ISLNK(st.st_mode):
            # Linux symlink mode is nominally 0777; require a single link
            # and exact known lexical shape; never follow it.
            require(base and st.st_nlink == 1)
            lexical = os.readlink(p)
            require(generated_venv_link(p.relative_to(path),lexical))
        else:
            require(not st.st_mode & 0o022)
            if stat.S_ISDIR(st.st_mode):
                todo.extend(p.iterdir())
            else:
                require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1)
        require(len(found) < 250000)
        found[str(p)] = (*seal(st), lexical)
    return found

def tasks(proc):
    return sorted((pid.name, task.name) for pid in proc.iterdir() if pid.name.isdecimal()
                  for task in (pid/'task').iterdir() if task.name.isdecimal())

def unescape(path):
    return re.sub(r'\\([0-7]{3})',lambda m: chr(int(m[1],8)),path)

def mount_entries(raw):
    for line in raw.splitlines():
        f=line.split()
        require(len(f)>=10 and '-' in f[6:])
        major,minor=f[2].split(':')
        yield (f[0],os.makedev(int(major),int(minor)),Path(unescape(f[3])),
               Path(unescape(f[4]))), frozenset(f[5].split(','))


def mounts(raw):
    return (mount for mount,_ in mount_entries(raw))


def intersect(a,b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def check_mounts(raw, host_mount, physical, logical, device, *, canonical_inode=None):
    for mount,options in mount_entries(raw):
        mid,dev,root,target=mount
        require(not target.is_relative_to(logical))
        if dev==device and intersect(root,physical):
            if mount==host_mount:
                continue
            # Linux copies a mount into another namespace with a new local ID.
            # Accept only a read-only copy at the exact same root and target,
            # confirmed by the task's actual namespace view of the candidate.
            # A relocated bind, writable alternate mount, hidden or foreign
            # inode, and every submount under the candidate still deny.
            require(mid!=host_mount[0] and mount[1:]==host_mount[1:]
                    and 'ro' in options and 'rw' not in options
                    and canonical_inode is not None and canonical_inode() is True)


def scan(candidate, tracked, proc=Path('/proc')):
    require(os.stat(proc/'self/ns/pid').st_ino == os.stat(proc/'1/ns/pid').st_ino)
    physical_candidates = [m for m in mounts((proc/'self/mountinfo').read_text())
                           if candidate.is_relative_to(m[3])]
    require(bool(physical_candidates))
    mount = max(physical_candidates, key=lambda m: len(m[3].parts))
    physical = mount[2]/candidate.relative_to(mount[3])
    require(mount[1] == candidate.stat().st_dev)
    baseline = tasks(proc)
    observed = {}
    mount_namespaces = {}
    for pid, tid in baseline:
        t = proc/pid/'task'/tid
        status = (t/'stat').read_text()
        state = status[status.rindex(')')+2:].split()
        require(len(state) >= 20)
        ident = state[19]
        flags = int(state[6])
        # Kernel threads have no user mm, but threads in any user process do.
        kernel = flags & 0x00200000
        if state[0] in ('Z','X','x') or kernel:
            observed[pid,tid] = (ident,state[0] if not kernel else 'kernel')
            continue
        ns = pair((t/'ns/mnt').stat())
        if ns not in mount_namespaces:
            data = (t/'mountinfo').read_text()
            def same_inode():
                return pair((t/'root'/candidate.relative_to('/')).stat())==pair(candidate.lstat())
            check_mounts(data, mount, physical, candidate, mount[1],canonical_inode=same_inode)
            mount_namespaces[ns] = data
        digest = hashlib.sha256()
        for field in ('cmdline','environ'):
            with (t/field).open('rb') as source:
                raw=source.read(MAX_PROC_ARGS+1)
            # A daemon may have a delayed import/restart path pinned in its
            # argv or environment without an open file. Do not print either.
            require(len(raw)<=MAX_PROC_ARGS and os.fsencode(candidate) not in raw)
            digest.update(raw)
        # Stat follows the kernel handle, even for an alternative mount alias.
        handles = [t/name for name in ('cwd','root','exe')]
        fd_dir = os.open(t/'fd',os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = sorted(os.listdir(fd_dir))
            if int(pid) == os.getpid():
                # Ignore precisely the observer's own directory FD, not arbitrary refs.
                names = [name for name in names if name != str(fd_dir)]
                # CPython dup()s an FD passed to listdir and closes that
                # exact temporary directory iterator before returning.
                ghosts = [name for name in names if not os.path.lexists(t/'fd'/name)]
                require(len(ghosts) <= 1)
                names = [name for name in names if name not in ghosts]
            handles.extend(t/'fd'/name for name in names)
            for fd in handles:
                target = os.readlink(fd)
                value = pair(fd.stat())
                require(value not in tracked)
                digest.update(repr((fd.name,value,target)).encode('utf-8','surrogateescape'))
                if target.startswith('/'):
                    require(not Path(target.removesuffix(' (deleted)')).is_relative_to(candidate))
        finally:
            os.close(fd_dir)
        with (t/'maps').open() as stream:
            for line in stream:
                info = line.split(None,5)
                require(len(info) >= 5)
                digest.update(line.encode('utf-8','surrogateescape'))
                key = inode_field(info[3]+':'+info[4])
                require(key not in tracked)
                if len(info) == 6 and info[5].startswith('/'):
                    require(not Path(unescape(info[5].strip().removesuffix(' (deleted)'))).is_relative_to(candidate))
        observed[pid,tid] = (ident,ns,digest.digest())
    require(tasks(proc) == baseline)
    for line in (proc/'locks').read_text().splitlines():
        keys = [field for field in line.split() if re.fullmatch('[0-9a-f]+:[0-9a-f]+:[0-9]+',field)]
        require(len(keys) == 1 and inode_field(keys[0]) not in tracked)
    return observed, mount_namespaces

def proof(q, root, uid, fd, inspect=scan):
    store = {'base':'releases','visual-static':'releases','visual-runtime':'versions'}[q['store']]
    parent = root/'runtime' if store == 'versions' else root
    real_dir(root,{0,uid})
    real_dir(parent/store,{0,uid})
    item = parent/store/q['inactive']
    st = real_dir(item,{0,uid})
    require(pair(st) == (q['dev'],q['ino']))
    visual = q['store'] != 'base'
    lock_sig = verify_lock(root,fd,uid)
    pointers = pointer_seal(root,visual,uid)
    require(pointers[store][0] == q['active'] and pointers[store][3][:2] != pair(st))
    original = tree(item,uid,st.st_dev,base=not visual)
    tracked = {value[:2] for value in original.values()}
    require(inspect(item,tracked) == inspect(item,tracked))
    require(tree(item,uid,st.st_dev,base=not visual) == original and pointer_seal(root,visual,uid) == pointers
            and verify_lock(root,fd,uid) == lock_sig)

def witness(conn, scope):
    fds = array('i')
    conn.settimeout(0.8)
    try:
        _, uid, _ = struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        account, path = SCOPES[scope]
        require(uid == pwd.getpwnam(account).pw_uid)
        approved = trusted_profile(scope,uid)
        packet, ancillary, flags, _ = conn.recvmsg(
            SIZE+1, socket.CMSG_SPACE(4*fds.itemsize), socket.MSG_CMSG_CLOEXEC)
        for level, kind, data in ancillary:
            require(level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS)
            fds.frombytes(data[:len(data)-(len(data)%fds.itemsize)])
            require(len(data) % fds.itemsize == 0)
        require(len(fds) == 1 and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
        while b'\n' not in packet and len(packet) <= SIZE:
            more = conn.recv(SIZE+1-len(packet))
            require(bool(more))
            packet += more
        q = request(packet,scope)
        proof(q,Path(path),uid,fds[0])
        require(trusted_profile(scope,uid) == approved)
        conn.sendall(OK)
    except (OSError,ValueError,TypeError,OverflowError,IndexError,KeyError):
        pass
    finally:
        for fd in fds:
            os.close(fd)

def main():
    # systemd Accept=yes, socket unit delivers the exact connection on stdin.
    require(len(sys.argv) == 2 and sys.argv[1] in SCOPES and os.geteuid() == 0)
    scope = sys.argv[1]
    real_dir(SOCKET_ROOT,{0})
    path = SOCKET_ROOT/(scope+'.sock')
    st = path.lstat()
    require(stat.S_ISSOCK(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o007)
    with socket.socket(fileno=os.dup(sys.stdin.fileno())) as conn:
        require(conn.getsockname() == str(path))
        witness(conn,scope)

if __name__ == '__main__':
    try:
        main()
    except (OSError,ValueError,TypeError,OverflowError,IndexError,KeyError):
        sys.exit(1)
