"""TEST-only HTTP transport for immutable visual assets; no DB or outbound access."""
from hashlib import sha256
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


def make_server(root, token, port=8829, *, environment='test'):
    if environment not in ('test', 'prod') or not HEX.fullmatch(token):
        raise ValueError('invalid dedicated TEST token')

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
                value = json.loads(body)
            except (ValueError, OSError, RecursionError):
                return self.respond(404)
            if not isinstance(value, dict):
                return self.respond(400)
            try:
                if set(value) == {'op'} and value['op'] == 'current':
                    rel = read_small(root / 'current', 66).decode('ascii').strip()
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
    with make_server(ROOT / mode, secret, int(port), environment=mode) as server:
        server.serve_forever()
