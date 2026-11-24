"""Read-only TEST-specific environment proof. No generic MCP, tool proxy, or PROD fallback."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess

ROOT = Path('/srv/assertion-release/test')
DB = ROOT / 'state/fictional.sqlite3'
RELEASES = ROOT / 'releases'
MCP_CONFIG = ROOT / 'state/http.json'
PORTS = {'graph': 8891, 'mcp': 8893}
ROUTE = '/memory-mcp-test'
HEADER = 'x-memory-mcp-test-auth'
FIXTURE = 'thought-vm-synthetic-one'
EVENT = 'event-3d0c3deaf5cfad11b7dec7f7d8a53b27edbc888449d302a99791fcccea34506f'
REQUEST = {'operation': 'memory_environment', 'fixture_id': FIXTURE}
TAG = re.compile(r'assertion-memory-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,30})?')
SHA = re.compile(r'[0-9a-f]{64}')
MAX_BODY = 512


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
    # SQLite mode=ro alone can still create sidecars in a writable directory.
    # Require a kernel read-only view of the directory, DB and extant sidecars.
    for file in (path.parent, path, Path(str(path)+'-wal'), Path(str(path)+'-shm')):
        if file.exists() and (file.resolve(strict=True) != file or not (os.statvfs(file).f_flag & os.ST_RDONLY)):
            raise ValueError


def live_snapshot(release):
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


def fixture_exists(path):
    # mode=ro is intentional; immutable=1 could stale-read an active WAL database.
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.3)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN')
        row = connection.execute(
            'SELECT v.payload FROM memory_thoughts h JOIN memory_thought_versions v '
            'ON v.thought_id=h.id AND v.version=h.current_version WHERE h.id=? AND h.current_version=1',
            (FIXTURE,)).fetchone()
        if not row or not isinstance(row[0], str) or len(row[0]) > 4096:
            return False
        saved = json.loads(row[0], object_pairs_hook=pairs)
        raw = connection.execute(
            'SELECT prompt FROM memory_raw_events WHERE id=? AND session_id=? AND turn_id=? AND source=? AND scope=?',
            (EVENT, 'fixture-local','one','fixture://craft-vm','fictional-vm')).fetchone()
        return (saved.get('id') == FIXTURE and saved.get('version') == 1
            and saved.get('text') == 'В вымышленном ключе записано «ёж».'
            and saved.get('origin') == {'kind':'source_interpretation','event_id':EVENT,'scope':'fictional-vm'}
            and raw == ('Ключ 🧩: ёж.',))
    finally:
        connection.close()


def proof(tag, release, *, snapshot=live_snapshot, exists=fixture_exists):
    before = snapshot(release)
    if not exists(DB) or before != snapshot(release):
        raise ValueError
    return {'environment':'test','release':tag,'fixture_id':FIXTURE,'synthetic':True}


def handler(secret_hash, get_proof):
    if not SHA.fullmatch(secret_hash):
        raise ValueError
    expected = bytes.fromhex(secret_hash)

    class Provider(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(2)
            super().setup()

        def log_message(self, *_):
            pass

        def send_error(self, code, *_args, **_kwargs):
            self.reply(code, {'error':'request_rejected'})

        def reply(self, status, value):
            raw = json.dumps(value, separators=(',', ':')).encode()
            self.send_response(status)
            for k, v in {'Content-Type':'application/json','Content-Length':str(len(raw)),
                'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Connection':'close'}.items():
                self.send_header(k,v)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)
            self.close_connection = True

        def exact(self):
            return self.requestline.split()[1:2] == [ROUTE] and self.path == ROUTE

        def do_POST(self):
            if not self.exact():
                return self.reply(404, {'error':'not_found'})
            values = self.headers.get_all(HEADER) or []
            match = re.fullmatch('Bearer ([0-9a-f]{64})', values[0]) if len(values)==1 else None
            if not match or not hmac.compare_digest(hashlib.sha256(match[1].encode()).digest(), expected):
                return self.reply(401, {'error':'unauthorized'})
            lengths = self.headers.get_all('Content-Length') or []
            types = self.headers.get_all('Content-Type') or []
            if (len(lengths) != 1 or not re.fullmatch('[0-9]{1,3}',lengths[0]) or not 1 <= int(lengths[0]) <= MAX_BODY
                or len(types)!=1 or types[0].lower() != 'application/json' or self.headers.get('Transfer-Encoding') is not None):
                return self.reply(400, {'error':'invalid_request'})
            try:
                size = int(lengths[0])
                raw = self.rfile.read(size)
                if len(raw) != size or json.loads(raw.decode('utf-8'), object_pairs_hook=pairs) != REQUEST:
                    raise ValueError
            except (ValueError, UnicodeError, RecursionError, OSError):
                return self.reply(400, {'error':'invalid_request'})
            try:
                response = get_proof()
            except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError, RuntimeError,
                    subprocess.SubprocessError, sqlite3.Error):
                return self.reply(503, {'error':'test_unavailable'})
            return self.reply(200, response)

        def wrong_method(self):
            self.reply(405 if self.exact() else 404, {'error':'request_rejected'})

        do_GET = do_HEAD = do_PATCH = do_PUT = do_DELETE = do_OPTIONS = wrong_method

    return Provider


class Server(HTTPServer):
    def handle_error(self, *_):
        pass


def main():
    try:
        credentials = Path(os.environ['CREDENTIALS_DIRECTORY'])
        digest = read(credentials/'test_bearer_sha256', 65).decode('ascii').rstrip('\n')
        tag, release = expected_identity(read(credentials/'test_release_identity', 512))
        with Server(('127.0.0.1', 8831), handler(digest, lambda: proof(tag, release))) as running:
            running.serve_forever()
    except Exception:
        raise SystemExit('TEST environment proof service could not start') from None


if __name__=='__main__':
    main()
