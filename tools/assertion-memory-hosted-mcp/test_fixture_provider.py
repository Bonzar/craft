"""Read-only TEST process/config attestation for hosted MCP transport.
The relay's mount is read-only. This does not assert that the canonical
TEST backend itself has a read-only mount or cannot manage its TEST state.
No SQLite connection, real-record query, model, or PROD binding."""
import json
import os
from pathlib import Path
import re
import pwd
import socket
import stat

ROOT = Path('/srv/assertion-release/test')
DB = ROOT / 'state/fictional.sqlite3'
RELEASES = ROOT / 'releases'
MCP_CONFIG = ROOT / 'state/http.json'
PORTS = {'graph': 8891, 'mcp': 8893}
CGROUP = Path('/sys/fs/cgroup')
TAG = re.compile(r'assertion-memory-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,30})?')
SHA = re.compile(r'[0-9a-f]{64}')


def pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError
        out[key] = value
    return out


def read(path, limit):
    with path.open('rb') as fd:
        data = fd.read(limit + 1)
    if len(data) > limit:
        raise ValueError
    return data


def expected_identity(data):
    obj = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(obj, dict) or set(obj) != {'tag', 'commit', 'archive_sha256'}:
        raise ValueError
    tag, commit, digest = (obj[x] for x in ('tag', 'commit', 'archive_sha256'))
    if not isinstance(tag, str) or not TAG.fullmatch(tag) or not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit) or not isinstance(digest, str) or not SHA.fullmatch(digest):
        raise ValueError
    return tag, RELEASES / (commit + '-' + digest)



def expected_cgroups(data, uid):
    """Only exact, root-reviewed cgroup-v2 paths for this scoped user/units."""
    obj=json.loads(data,object_pairs_hook=pairs)
    if type(uid) is not int or uid<1 or type(obj) is not dict or set(obj)!=set(PORTS):
        raise ValueError('invalid TEST cgroup evidence')
    prefix=f'/user.slice/user-{uid}.slice/user@{uid}.service/'
    for role,path in obj.items():
        if not isinstance(path,str) or len(path)>512 or not path.startswith(prefix):
            raise ValueError('wrong TEST user cgroup')
        rest=path[len(prefix):].split('/')
        if rest[-1]!=f'assertion-test-deploy-{role}.service' or not all(
            re.fullmatch(r'[A-Za-z0-9_@-][A-Za-z0-9_.@-]{0,95}\.slice',seg) for seg in rest[:-1]):
            raise ValueError('wrong TEST unit cgroup')
    if obj['graph']==obj['mcp']:raise ValueError('shared cgroup is not permitted')
    return obj


def unit_processes(cgroup_path):
    group=CGROUP.joinpath(cgroup_path.lstrip('/'))
    if group.resolve(strict=True)!=group or not (os.statvfs(group).f_flag & os.ST_RDONLY):
        raise ValueError('cgroup evidence mount is not canonical/read-only')
    try:raw=read(group/'cgroup.procs',4096).decode('ascii')
    except (OSError,UnicodeError) as e:raise ValueError('TEST cgroup unavailable') from e
    rows=raw.splitlines()
    if not 1<=len(rows)<=128 or any(not re.fullmatch(r'[1-9][0-9]{0,9}',row) or int(row)<=1 for row in rows):
        raise ValueError('invalid TEST cgroup process list')
    # In thread-mode views the same PID can be emitted more than once.
    return sorted({int(row) for row in rows})


def process_in_unit(proc, cgroup_path):
    return read(proc/'cgroup',4096).decode('ascii').splitlines()==['0::'+cgroup_path]


def listener_evidence(role, port, release, cgroup_path, uid, *, graph_fixture_flag=True):
    expected=(['-B','-m','apps.graph_viewer.server','--source',str(DB),
      '--state-dir',str(ROOT/'state/graph-state'),'--password-file',str(ROOT/'state/graph-password'),
      '--static-dir',str(ROOT/'current/apps/graph_viewer/web/dist'),'--port','8891']+(['--synthetic-fixture'] if graph_fixture_flag else [])
      if role=='graph' else ['-B','-m','prototypes.assertion_memory.http_server','--config',str(MCP_CONFIG)])
    command=[str(ROOT/'current/venv/bin/python'),*expected,'']
    found=[]
    for pid in unit_processes(cgroup_path):
        proc=Path('/proc')/str(pid)
        try:
            if proc.stat().st_uid!=uid or not process_in_unit(proc,cgroup_path):
                continue
            if read(proc/'cmdline',8192).decode('utf-8').split('\0')!=command:
                continue
            if (proc/'cwd').resolve(strict=True)!=release or not process_listens(proc,port):
                continue
            start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
            if not re.fullmatch(r'[1-9][0-9]*',start) or not process_in_unit(proc,cgroup_path):
                continue
            found.append((pid,start))
        except (FileNotFoundError,ProcessLookupError):continue
    if len(found)!=1:raise ValueError('TEST unit has no unique verified listener')
    with socket.create_connection(('127.0.0.1',port),timeout=.5):pass
    return found[0]

def process_listens(proc, port):
    # Verify the PID owns the loopback TCP listener rather than accepting a
    # similarly named process plus an unrelated service on the expected port.
    owned = set()
    for fd in (proc / 'fd').iterdir():
        try:
            match = re.fullmatch(r'socket:\[(\d+)\]', os.readlink(fd))
        except FileNotFoundError:
            continue
        if match:
            owned.add(match[1])
    local = f'0100007F:{port:04X}'
    for row in (proc / 'net/tcp').read_text().splitlines()[1:]:
        columns = row.split()
        if columns[1] == local and columns[3] == '0A' and columns[9] in owned:
            return True
    return False


def require_readonly_db_mount(path):
    # This statvfs runs in the adapter's mount namespace, not the canonical process.
    # Require kernel read-only view of directory, DB and extant sidecars for the adapter.
    for file in (path.parent, path, Path(str(path)+'-wal'), Path(str(path)+'-shm')):
        if file.exists() and (file.resolve(strict=True) != file or not (os.statvfs(file).f_flag & os.ST_RDONLY)):
            raise ValueError


def expected_real_database(data):
    value=json.loads(data,object_pairs_hook=pairs)
    if type(value) is not dict or set(value)!={'device','inode','graph_fixture_flag'}:
        raise ValueError('invalid real TEST pin shape')
    if (type(value['device']) is not int or value['device']<0 or type(value['inode']) is not int
            or value['inode']<1 or type(value['graph_fixture_flag']) is not bool):
        raise ValueError('invalid real TEST pin')
    return value


def verify_real_database_file(meta,pin,uid):
    if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or meta.st_uid!=uid or meta.st_mode&0o022
            or (meta.st_dev,meta.st_ino)!=(pin['device'],pin['inode'])):
        raise ValueError('real TEST database identity changed')


def live_snapshot(release,cgroups,*,real_pin=None):
    uid=os.getuid()
    if uid==0 or pwd.getpwuid(uid).pw_name!='assertion-test-deploy':
        raise ValueError('not the scoped TEST service account')
    cgroups=expected_cgroups(json.dumps(cgroups),uid)
    # The root-supplied immutable artifact identity is not an assertion of liveness.
    # Require both actual release-scope services and their expected inputs independently.
    if (not (ROOT / 'current').is_symlink() or (ROOT / 'current').resolve(strict=True) != release
            or release.resolve(strict=True) != release or DB.resolve(strict=True) != DB
            or not stat.S_ISREG(DB.stat().st_mode)):
        raise ValueError
    db=DB.lstat()
    if real_pin is not None:verify_real_database_file(db,real_pin,uid)
    require_readonly_db_mount(DB)
    cfg = json.loads(read(MCP_CONFIG, 4096), object_pairs_hook=pairs)
    if cfg.get('port') != PORTS['mcp'] or cfg.get('public_host') != 'localhost' or cfg.get('service', {}).get('db') != str(DB):
        raise ValueError
    graph_flag=True if real_pin is None else real_pin['graph_fixture_flag']
    token=[listener_evidence(role,port,release,cgroups[role],uid,graph_fixture_flag=graph_flag)
           for role,port in PORTS.items()]
    after=DB.lstat()
    if real_pin is not None:verify_real_database_file(after,real_pin,uid)
    if (db.st_dev,db.st_ino)!=(after.st_dev,after.st_ino):raise ValueError('TEST database changed')
    return (*token, db.st_dev, db.st_ino)


