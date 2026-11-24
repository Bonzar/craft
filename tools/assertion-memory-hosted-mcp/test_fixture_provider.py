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
import subprocess

ROOT = Path('/srv/assertion-release/test')
DB = ROOT / 'state/fictional.sqlite3'
RELEASES = ROOT / 'releases'
MCP_CONFIG = ROOT / 'state/http.json'
PORTS = {'graph': 8891, 'mcp': 8893}
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


def live_snapshot(release):
    if os.getuid()==0 or pwd.getpwuid(os.getuid()).pw_name!='assertion-test-deploy':
        raise ValueError('not the scoped TEST service account')
    # The root-supplied immutable artifact identity is not an assertion of liveness.
    # Require both actual release-scope services and their expected inputs independently.
    if (not (ROOT / 'current').is_symlink() or (ROOT / 'current').resolve(strict=True) != release
            or release.resolve(strict=True) != release or DB.resolve(strict=True) != DB
            or not stat.S_ISREG(DB.stat().st_mode)):
        raise ValueError
    require_readonly_db_mount(DB)
    cfg = json.loads(read(MCP_CONFIG, 4096), object_pairs_hook=pairs)
    if cfg.get('port') != PORTS['mcp'] or cfg.get('public_host') != 'localhost' or cfg.get('service', {}).get('db') != str(DB):
        raise ValueError
    token = []
    for name, port in PORTS.items():
        text = subprocess.check_output(['/usr/bin/systemctl', '--user', 'show',
            '--property=MainPID,ActiveState', 'assertion-test-deploy-' + name + '.service'],
            text=True, timeout=1, stderr=subprocess.DEVNULL,
            env={'PATH':'/usr/bin:/bin', 'XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}', 'LANG':'C.UTF-8'})
        state = dict(line.split('=', 1) for line in text.splitlines())
        pid = int(state['MainPID'])
        if pid <= 1 or state['ActiveState'] != 'active':
            raise ValueError
        proc = Path('/proc') / str(pid)
        st = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        if proc.stat().st_uid != os.getuid() or (proc / 'cwd').resolve(strict=True) != release:
            raise ValueError
        cmd = read(proc / 'cmdline', 8192).decode('utf-8').split('\0')
        expected = (['-B','-m','apps.graph_viewer.server','--source',str(DB),
          '--state-dir',str(ROOT/'state/graph-state'),'--password-file',str(ROOT/'state/graph-password'),
          '--static-dir',str(ROOT/'current/apps/graph_viewer/web/dist'),'--port','8891','--synthetic-fixture']
            if name == 'graph' else ['-B','-m','prototypes.assertion_memory.http_server','--config',str(MCP_CONFIG)])
        if cmd != [str(ROOT/'current/venv/bin/python'), *expected, ''] or not process_listens(proc, port):
            raise ValueError
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            pass
        token.append((pid, st[19]))
    db = DB.stat()
    return (*token, db.st_dev, db.st_ino)


