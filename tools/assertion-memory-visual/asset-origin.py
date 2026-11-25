"""Loopback Visual origin for immutable assets and optional TEST Base queue reads."""
from hashlib import sha256
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import stat
from threading import BoundedSemaphore

PATH = '/memory-visual-test-read'
ROOT = Path('/var/lib/assertion-memory-visual')
HEX = re.compile(r'[0-9a-f]{64}\Z')
ITEM = re.compile(r'(?:index\.html|assets/(?:graph\.worker-|index-)[A-Za-z0-9_-]+\.(?:js|css))\Z')
LIMIT = 2_400_000
BODY_LIMIT = 512
MIME = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}


def digest(data):
    return sha256(data).hexdigest()


def read_small(path, limit):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError('not a regular file')
        data = file.read(limit + 1)
        if len(data) > limit:
            raise ValueError('file exceeds bound')
        return data


def manifest(root, release):
    if not HEX.fullmatch(release):
        raise ValueError('invalid release')
    directory = root / 'releases' / release
    if directory.is_symlink() or (directory / 'assets').is_symlink():
        raise ValueError('invalid release path')
    body = read_small(directory / 'manifest.json', 1024)
    if digest(body) != release:
        raise ValueError('manifest changed')
    parsed = json.loads(body)
    if (not isinstance(parsed, dict) or len(parsed) != 4 or 'index.html' not in parsed
            or any(not ITEM.fullmatch(k) or not isinstance(v, str) or not HEX.fullmatch(v)
                   for k, v in parsed.items())):
        raise ValueError('invalid manifest')
    return directory, parsed


def current_release(root):
    """Read the shared journal receiver's atomic pointer, without following it."""
    target = Path(os.readlink(root / 'current'))
    if target.parent not in (Path('releases'), root.absolute() / 'releases'):
        raise ValueError('current escaped this visual environment')
    key = target.name
    if not HEX.fullmatch(key):
        raise ValueError('invalid visual release identity')
    return key


QUEUE_LIMIT = 256_000
BASE_ORIGIN = re.compile(r'http://127\.0\.0\.1:([1-9][0-9]{0,4})\Z')
SNAPSHOT_KEYS = {'stage', 'release_version', 'release_generation', 'global_order',
                 'snapshot_at', 'queues', 'processing_control'}


def unique_json(body):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('duplicate field')
            result[k] = v
        return result
    def reject_constant(_):
        raise ValueError('non-JSON value')
    return json.loads(body.decode('utf-8'), object_pairs_hook=unique, parse_constant=reject_constant)



def snapshot_queues(queues, limit, offset):
    if not isinstance(queues, list) or len(queues) > 16:
        return False
    ids = set()
    for q in queues:
        if (not isinstance(q, dict) or set(q) != {'id','status','ordering','total','next_offset','items'}
                or any(not isinstance(q[k], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',q[k])
                       for k in ('id','status','ordering'))
                or q['id'] in ids or type(q['total']) is not int or q['total'] < 0
                or type(q['next_offset']) not in (int, type(None))
                or q['next_offset'] != (offset+limit if offset+limit < q['total'] else None)
                or not isinstance(q['items'], list)
                or len(q['items']) != max(0, min(limit,q['total']-offset))):
            return False
        ids.add(q['id'])
        for row in q['items']:
            if (not isinstance(row, dict) or set(row) != {'id','status','phase','position','available_at'}
                    or not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 512
                    or any(not isinstance(row[k], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',row[k])
                           for k in ('status','phase'))
                    or (row['position'] is not None and (type(row['position']) is not int or row['position'] < 1))
                    or (row['available_at'] is not None and
                        (not isinstance(row['available_at'], str) or len(row['available_at']) > 64))):
                return False
    return True


def queue_client(origin, key):
    address = BASE_ORIGIN.fullmatch(origin)
    if not address or int(address[1]) > 65535 or not HEX.fullmatch(key):
        raise ValueError('invalid local TEST Base settings')
    def fetch(limit, offset):
        if (type(limit) is not int or not 1 <= limit <= 50 or
                type(offset) is not int or not 0 <= offset <= 1_000_000):
            raise ValueError('invalid page')
        http = HTTPConnection('127.0.0.1', int(address[1]), timeout=3)
        try:
            http.request('GET', f'/api/v1/queue-status?limit={limit}&offset={offset}',
                         headers={'Authorization': 'Bearer '+key, 'Accept': 'application/json',
                                  'Connection': 'close'})
            response = http.getresponse()
            if (response.status != 200 or response.headers.get_content_type() != 'application/json'
                    or response.headers.get('Content-Encoding') is not None):
                raise ValueError('Base queue unavailable')
            raw = response.read(QUEUE_LIMIT+1)
            if len(raw) > QUEUE_LIMIT:
                raise ValueError('Base queue page too large')
            value = unique_json(raw)
            ctl = value.get('processing_control') if isinstance(value, dict) else None
            if (not isinstance(value, dict) or set(value) != SNAPSHOT_KEYS or value['stage'] != 'test'
                    or not isinstance(value['release_version'], str)
                    or not re.fullmatch(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', value['release_version'])
                    or not isinstance(value['release_generation'], str) or not HEX.fullmatch(value['release_generation'])
                    or not isinstance(value['snapshot_at'], str) or len(value['snapshot_at']) > 64
                    or value['global_order'] is not False or not snapshot_queues(value['queues'],limit,offset)
                    or not isinstance(ctl, dict) or set(ctl) != {'paused', 'revision', 'can_change'}
                    or type(ctl['paused']) is not bool or ctl['can_change'] is not False
                    or not isinstance(ctl['revision'], str) or not re.fullmatch(r'[0-9a-f]{32}',ctl['revision'])):
                raise ValueError('invalid TEST Base queue page')
            return json.dumps({**value, 'capabilities': {'live': False}, 'readiness': []},
                              allow_nan=False, separators=(',', ':')).encode('utf-8')
        finally:
            http.close()
    return fetch


def make_server(root, token, port=8829, *, environment='test', queue_origin=None, queue_token=None, runtime_health=None):
    if environment not in ('test', 'prod') or not HEX.fullmatch(token):
        raise ValueError('invalid dedicated TEST token')

    if queue_origin is not None or queue_token is not None:
        if environment != 'test' or not queue_origin or not queue_token or token == queue_token:
            raise ValueError('TEST queue requires separate complete authority')
        fetch_queue = queue_client(queue_origin, queue_token)
    else:
        fetch_queue = None
    if runtime_health is not None:
        proof,key=runtime_health
        if environment!='test' or not HEX.fullmatch(proof) or not HEX.fullmatch(key) or key in (token,queue_token):
            raise ValueError('Invalid Visual TEST health authority')
    route = f'/memory-visual-{environment}-read'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(7)

        def log_message(self, *_):
            pass

        def handle_one_request(self):
            self.request_version, self.command = 'HTTP/1.0', ''
            try:
                self.raw_requestline = self.rfile.readline(4097)
                if not self.raw_requestline:
                    self.close_connection = True
                    return
                if len(self.raw_requestline) > 4096:
                    self.send_error(414)
                    return
                if self.parse_request():
                    # Dispatch even unknown methods through the same narrow route rules.
                    self.dispatch()
                    self.wfile.flush()
            except OSError:
                self.close_connection = True

        def send_error(self, code, *_args, **_kwargs):
            self.respond(code)

        def respond(self, status, data=b'', mime=None):
            self.close_connection = True
            self.send_response_only(status)
            self.send_header('Cache-Control', 'private, no-store, no-transform')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if mime:
                self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            if data and getattr(self, 'command', '') != 'HEAD':
                self.wfile.write(data)

        def dispatch(self):
            raw = self.requestline.split()
            if runtime_health is not None and len(raw)==3 and raw[1]=='/__visual-runtime-health':
                if self.command!='GET' or self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Length'):
                    return self.respond(404)
                headers=self.headers.get_all('Authorization',[])
                if len(headers)!=1 or not hmac.compare_digest(headers[0], 'Bearer '+runtime_health[1]):
                    return self.respond(401)
                try:
                    current=current_release(root)
                    manifest(root,current)
                    queue_ready=fetch_queue is not None and bool(fetch_queue(1,0))
                    return self.respond(200,json.dumps({'stage':'test','runtime_entry_sha':runtime_health[0],
                        'static_digest':current,'queue_ready':queue_ready},separators=(',',':')).encode(),'application/json')
                except Exception:
                    return self.respond(503)
            if len(raw) != 3 or raw[1] != route or self.command != 'POST':
                return self.respond(404)
            auth = self.headers.get_all('Authorization', [])
            got = re.fullmatch(r'Bearer ([0-9a-f]{64})', auth[0]) if len(auth) == 1 else None
            if not got or not hmac.compare_digest(got[1], token):
                return self.respond(401)
            if self.headers.get_all('Content-Type', []) != ['application/json']:
                return self.respond(415)
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get_all('Transfer-Encoding') or len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,9}', lengths[0]):
                return self.respond(400)
            length = int(lengths[0])
            if length > BODY_LIMIT:
                return self.respond(413)
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    return self.respond(400)
                value = unique_json(body)
            except (ValueError, OSError, RecursionError):
                return self.respond(404)
            if not isinstance(value, dict):
                return self.respond(400)
            if value.get('op') == 'queue_status':
                if fetch_queue is None:
                    return self.respond(404)
                if set(value) != {'op', 'limit', 'offset'}:
                    return self.respond(400)
                limit, offset = value['limit'], value['offset']
                if (type(limit) is not int or not 1 <= limit <= 50 or
                        type(offset) is not int or not 0 <= offset <= 1_000_000):
                    return self.respond(400)
                try:
                    return self.respond(200, fetch_queue(limit, offset), 'application/json')
                except Exception:
                    return self.respond(503)
            try:
                if set(value) == {'op'} and value['op'] == 'current':
                    rel = current_release(root)
                    manifest(root, rel)
                    return self.respond(200, json.dumps({'digest': rel}, separators=(',', ':')).encode(), 'application/json')
                release, name = value.get('digest'), value.get('name')
                if (set(value) != {'op', 'digest', 'name'} or value['op'] != 'asset'
                        or not isinstance(release, str) or not HEX.fullmatch(release)
                        or not isinstance(name, str) or not ITEM.fullmatch(name)):
                    return self.respond(400)
                directory, files = manifest(root, release)
                if name not in files:
                    return self.respond(404)
                asset = read_small(directory / name, LIMIT)
                if digest(asset) != files[name]:
                    return self.respond(404)
                return self.respond(200, asset, MIME[Path(name).suffix])
            except (ValueError, OSError, RecursionError):
                return self.respond(404)

    class BoundedServer(ThreadingHTTPServer):
        slots = BoundedSemaphore(16)

        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()

    return BoundedServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    import sys
    mode, port = sys.argv[1:]
    if mode not in ('test','prod') or not re.fullmatch(r'[1-9][0-9]{0,4}',port) or int(port)>65535:
        raise SystemExit('invalid isolated service settings')
    credential = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'asset-token'
    secret = read_small(credential, 66).decode('ascii').strip()
    queue_origin = os.environ.get('MEMORY_VISUAL_TEST_BASE_ORIGIN')
    queue_secret = (read_small(credential.with_name('queue-base-token'), 66).decode('ascii').strip()
                    if queue_origin else None)
    health_path=credential.with_name('runtime-health-token')
    health=(digest(read_small(Path(__file__).resolve(strict=True),64_000)),
            read_small(health_path,66).decode('ascii').strip()) if health_path.is_file() else None
    with make_server(ROOT / mode, secret, int(port), environment=mode,
                     queue_origin=queue_origin, queue_token=queue_secret, runtime_health=health) as server:
        server.serve_forever()
