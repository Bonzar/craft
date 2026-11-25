"""Loopback Visual origin for immutable assets and optional TEST Base queue reads."""
from hashlib import sha256
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
import stat
from threading import BoundedSemaphore
from urllib.parse import parse_qs, unquote, urlsplit

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


def queue_client(origin, key, *, stage='test', disclose_pause=False):
    address = BASE_ORIGIN.fullmatch(origin)
    if stage not in ('test','prod') or not address or int(address[1]) > 65535 or not HEX.fullmatch(key):
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
            if (not isinstance(value, dict) or set(value) != SNAPSHOT_KEYS or value['stage'] != stage
                    or not isinstance(value['release_version'], str)
                    or not re.fullmatch(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', value['release_version'])
                    or not isinstance(value['release_generation'], str) or not HEX.fullmatch(value['release_generation'])
                    or not isinstance(value['snapshot_at'], str) or len(value['snapshot_at']) > 64
                    or value['global_order'] is not False or not snapshot_queues(value['queues'],limit,offset)
                    or not isinstance(ctl, dict) or set(ctl) != {'paused', 'revision', 'can_change'}
                    or type(ctl['paused']) is not bool or type(ctl['can_change']) is not bool
                    or not isinstance(ctl['revision'], str) or not re.fullmatch(r'[0-9a-f]{32}',ctl['revision'])):
                raise ValueError('invalid TEST Base queue page')
            capabilities={'live':False}
            if disclose_pause and ctl['can_change']:
                capabilities['standalone_pause']=True
            # Local Base readiness never grants direct browser writes or a
            # mutation capability on the legacy Sites transport.
            ctl['can_change']=False
            return json.dumps({**value, 'capabilities': capabilities, 'readiness': []},
                              allow_nan=False, separators=(',', ':')).encode('utf-8')
        finally:
            http.close()
    return fetch


STANDALONE_HOST = re.compile(r'https://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)\Z')
GRAPH_RE = re.compile(r'/api/v1/(?:capabilities|graph|search|events|bookmarks|'
    r'graph/[A-Za-z0-9:_-]+/(?:nodes\.bin|edges\.bin|topics|sessions|nodes/[0-9]+(?:/edges)?|edges/[0-9]+)|'
    r'(?:nodes|edges|sources|overviews)/[A-Za-z0-9:/_@.-]+)\Z')
GRAPH_LIMIT = 32_000_000
STANDALONE_AUTH = 'X-Memory-Visual-Owner-Ingress'
PAUSE_OWNER_AUTH = 'X-Memory-Visual-Pause-Owner-Ingress'


def standalone_config(origin, ingress, graph_origin, graph_key):
    parsed=STANDALONE_HOST.fullmatch(origin)
    graph=BASE_ORIGIN.fullmatch(graph_origin)
    if (not parsed or len(parsed[1])>253 or not HEX.fullmatch(ingress)
            or not graph or int(graph[1])>65535 or not HEX.fullmatch(graph_key)
            or ingress==graph_key):
        raise ValueError('Invalid standalone Visual owner boundary')
    return (origin,parsed[1],ingress,int(graph[1]),graph_key)


def path_and_query(raw):
    if (not raw.startswith('/') or raw.startswith('//') or '#' in raw or '\\' in raw or len(raw)>3000
            or any(ord(c)<=32 or ord(c)>126 for c in raw)):
        raise ValueError('Invalid standalone request path')
    parsed=urlsplit(raw)
    if re.search(r'%(?:2f|5c)',parsed.path,re.I):
        raise ValueError('Ambiguous standalone request path')
    path=unquote(parsed.path,errors='strict')
    if any(x in ('.','..') for x in path.split('/')) or '%' in path or '\\' in path:
        raise ValueError('Invalid standalone request path')
    return path,parsed.query


BROWSER_PAUSE = '/api/v1/processing/pause'
BASE_PAUSE = '/api/v1/queue-pause'
REVISION = re.compile(r'[0-9a-f]{32}\Z')


def pause_state(value, stage):
    if (type(value) is not dict or set(value) != {'stage','paused','revision'}
            or value['stage'] != stage or type(value['paused']) is not bool
            or type(value['revision']) is not str or not REVISION.fullmatch(value['revision'])):
        raise ValueError('invalid Base pause state')
    return value


def pause_client(origin, read_key, write_key, signer, *, stage):
    """Consume only the dedicated loopback REST surface, never the Graph/MCP bearer."""
    address = BASE_ORIGIN.fullmatch(origin)
    if (not address or int(address[1]) > 65535 or not callable(signer)
            or any(type(k) is not str or not HEX.fullmatch(k) for k in (read_key,write_key))
            or read_key == write_key):
        raise ValueError('invalid Base pause transport')
    def call(method, command=None):
        body = None
        headers = {'Accept':'application/json','Connection':'close',
                   'Authorization':'Bearer '+(read_key if method=='GET' else write_key)}
        if method == 'PUT':
            body = json.dumps(command, separators=(',',':')).encode('ascii')
            proof = signer(body, command['expected_revision'])
            if type(proof) is not dict or set(proof) != {
                'X-Memory-Pause-Owner','X-Memory-Pause-Issued-At','X-Memory-Pause-Expires-At',
                'X-Memory-Pause-Nonce','X-Memory-Pause-Signature'
            } or any(type(v) is not str or not re.fullmatch(r'[0-9a-f]{1,64}',v) for v in proof.values()):
                raise ValueError('invalid trusted signer response')
            headers.update(proof)
            headers['Content-Type'] = 'application/json'
        conn = HTTPConnection('127.0.0.1', int(address[1]), timeout=4)
        try:
            conn.request(method, BASE_PAUSE, body=body, headers=headers)
            rsp = conn.getresponse()
            if method == 'PUT' and rsp.status == 409:
                return None
            if (rsp.status != 200 or rsp.headers.get_content_type() != 'application/json'
                    or rsp.headers.get('Content-Encoding') is not None):
                raise ValueError('Base pause unavailable')
            raw = rsp.read(1025)
            if len(raw) > 1024:
                raise ValueError('invalid Base pause response')
            return pause_state(unique_json(raw), stage)
        finally:
            conn.close()
    return call


def pause_signer(stage, owner, secret, *, clock=time.time):
    """Service-only signer: supplied to handlers guarded by the dedicated verified owner ingress."""
    if (stage not in ('test','prod') or type(owner) is not str or not HEX.fullmatch(owner)
            or type(secret) is not bytes or len(secret) != 32):
        raise ValueError('invalid pause signer')
    def sign(body, revision):
        iat = int(clock())
        expires = iat+15
        nonce = secrets.token_hex(16)
        message = ('\n'.join(('memory-queue-pause-v1','PUT',BASE_PAUSE,stage,
                   digest(body),owner,revision,str(iat),str(expires),nonce))+'\n').encode('ascii')
        return dict(zip(('X-Memory-Pause-Owner','X-Memory-Pause-Issued-At','X-Memory-Pause-Expires-At',
                         'X-Memory-Pause-Nonce','X-Memory-Pause-Signature'),
                        (owner,str(iat),str(expires),nonce,hmac.digest(secret,message,'sha256').hex())))
    return sign


def make_server(root, token, port=8829, *, environment='test', queue_origin=None, queue_token=None, runtime_health=None, standalone=None, pause=None, clock=time.time):
    if environment not in ('test', 'prod') or not HEX.fullmatch(token):
        raise ValueError('invalid dedicated TEST token')

    if queue_origin is not None or queue_token is not None:
        if (environment=='prod' and standalone is None and runtime_health is None) or not queue_origin or not queue_token or token==queue_token:
            raise ValueError('Visual queue requires separate complete authority')
        fetch_queue = queue_client(queue_origin, queue_token, stage=environment)
    else:
        fetch_queue = None
    if runtime_health is not None:
        proof,key=runtime_health
        if not HEX.fullmatch(proof) or not HEX.fullmatch(key) or key in (token,queue_token):
            raise ValueError('Invalid scoped Visual health authority')
    if standalone is not None:
        public_origin,public_host,edge_key,graph_port,graph_key=standalone_config(*standalone)
        keys=[v for v in (token,queue_token,edge_key,graph_key,runtime_health[1] if runtime_health else None) if v]
        if len(keys)!=len(set(keys)) or fetch_queue is None:
            raise ValueError('Standalone Visual requires independent complete authorities')
    if pause is not None:
        if standalone is None:
            raise ValueError('pause requires verified standalone owner ingress')
        origin,read_key,write_key,csrf_key,pause_ingress,owner,proof_key = pause
        if (origin != queue_origin or type(csrf_key) is not bytes or len(csrf_key)!=32
                or type(proof_key) is not bytes or len(proof_key)!=32
                or type(pause_ingress) is not str or not HEX.fullmatch(pause_ingress)
                or len(set(keys+[read_key,write_key,csrf_key.hex(),pause_ingress,proof_key.hex()]))!=len(keys)+5):
            raise ValueError('pause requires distinct local authorities')
        fetch_owner_queue = queue_client(queue_origin,queue_token,stage=environment,disclose_pause=True)
        pause_call = pause_client(origin,read_key,write_key,
                                  pause_signer(environment,owner,proof_key,clock=clock),stage=environment)
        def csrf_token(revision, expiry):
            msg = f'visual-pause-v1\n{environment}\n{public_origin}\n{revision}\n{expiry}'.encode('ascii')
            return f'{expiry}.'+hmac.digest(csrf_key,msg,'sha256').hex()

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

        def respond(self, status, data=b'', mime=None, *, headers=None):
            self.close_connection = True
            self.send_response_only(status)
            self.send_header('Cache-Control', 'private, no-store, no-transform')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if mime:
                self.send_header('Content-Type', mime)
            for k,v in (headers or {}).items():
                self.send_header(k,v)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            if data and getattr(self, 'command', '') != 'HEAD':
                self.wfile.write(data)

        def standalone(self, raw):
            # Only the trusted HTTPS owner gate may wrap a request on this exact host.
            pin=self.headers.get_all(STANDALONE_AUTH,[])
            origins=self.headers.get_all('Origin',[])
            if len(pin)!=1 or not hmac.compare_digest(pin[0],edge_key):
                return self.respond(403)
            if origins and origins!=[public_origin]:
                return self.respond(403)
            safe={'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                  'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Strict-Transport-Security':'max-age=31536000'}
            try:
                path,query=path_and_query(raw)
            except (ValueError,UnicodeError):
                return self.respond(400)
            try:
                if self.headers.get('Sec-Fetch-Site')=='cross-site' and path.startswith('/api/'):
                    return self.respond(403)
                if pause is not None and path in (BROWSER_PAUSE,BROWSER_PAUSE+'/prepare'):
                    if raw != path or query:
                        return self.respond(404)
                    return self.pause_request(path,safe)
                if self.command not in ('GET','HEAD') or self.headers.get_all('Transfer-Encoding') or any(
                        n!='0' for n in self.headers.get_all('Content-Length',[])):
                    return self.respond(405)
                if path in ('/','/visual','/visual/'):
                    release=current_release(root)
                    manifest(root,release)
                    return self.respond(302,headers={**safe,'Location':'/visual/'+release+'/'+('?' + query if query else '')})
                if path=='/api/v1/session':
                    if query:
                        return self.respond(404)
                    return self.respond(200,b'{"authenticated":true}','application/json',headers=safe)
                if path=='/api/v1/capabilities':
                    q=parse_qs(query,keep_blank_values=True,max_num_fields=12)
                    if 'queue_status' in q:
                        if set(q)-{'queue_status','limit','offset'} or q['queue_status']!=['1'] or any(len(v)!=1 for v in q.values()):
                            return self.respond(400)
                        l,o=q.get('limit',['50'])[0],q.get('offset',['0'])[0]
                        if any(not re.fullmatch(r'(?:0|[1-9][0-9]{0,6})',v) for v in (l,o)) or not 1<=int(l)<=50 or int(o)>1_000_000:
                            return self.respond(400)
                        fetch = fetch_owner_queue if self.has_pause_owner() else fetch_queue
                        return self.respond(200,fetch(int(l),int(o)),'application/json',headers=safe)
                if GRAPH_RE.fullmatch(path):
                    return self.graph(raw,safe)
                match=re.fullmatch(r'/visual/([a-f0-9]{64})/(.*)',path)
                if match:
                    release,name=match[1],match[2] or 'index.html'
                    if release!=current_release(root) or not ITEM.fullmatch(name):
                        return self.respond(404)
                    directory,lookup=manifest(root,release)
                    if name not in lookup:
                        return self.respond(404)
                    asset=read_small(directory/name,LIMIT)
                    if digest(asset)!=lookup[name] or release!=current_release(root):
                        return self.respond(404)
                    return self.respond(200,asset,MIME[Path(name).suffix],headers=safe)
                return self.respond(404)
            except Exception:
                return self.respond(503)

        def has_pause_owner(self):
            proofs = self.headers.get_all(PAUSE_OWNER_AUTH,[])
            return pause is not None and len(proofs)==1 and hmac.compare_digest(proofs[0],pause_ingress)

        def pause_request(self,path,safe):
            if not self.has_pause_owner():
                return self.respond(403)
            prepare = path.endswith('/prepare')
            if self.command != ('POST' if prepare else 'PUT'):
                return self.respond(405)
            if self.headers.get_all('Origin',[]) != [public_origin]:
                return self.respond(403)
            lengths = self.headers.get_all('Content-Length',[])
            if (self.headers.get_all('Transfer-Encoding') or len(lengths)!=1
                    or not re.fullmatch(r'[1-9][0-9]{0,2}',lengths[0])):
                return self.respond(400)
            if self.headers.get_all('Content-Type',[]) != ['application/json']:
                return self.respond(415)
            try:
                raw = self.rfile.read(int(lengths[0]))
                if len(raw)!=int(lengths[0]):
                    return self.respond(400)
                request = unique_json(raw)
            except (ValueError, OSError, RecursionError):
                return self.respond(400)
            def reply(status,value):
                return self.respond(status,json.dumps(value,separators=(',',':')).encode(),
                                    'application/json',headers=safe)
            if prepare:
                if type(request) is not dict or request:
                    return self.respond(400)
                state = pause_call('GET')
                return reply(200,{**state,'csrf_token':csrf_token(state['revision'],int(clock())+30)})
            if (type(request) is not dict or set(request)!={'stage','paused','expected_revision'}
                    or type(request['paused']) is not bool or type(request['expected_revision']) is not str
                    or not REVISION.fullmatch(request['expected_revision'])):
                return self.respond(400)
            if request['stage']!=environment:
                return reply(409,{'error':'queue_control_changed'})
            revision = request['expected_revision']
            tokens = self.headers.get_all('X-CSRF-Token',[])
            proof = re.fullmatch(r'([0-9]{1,12})\.[0-9a-f]{64}',tokens[0]) if len(tokens)==1 else None
            if not proof:
                return self.respond(403)
            expiry, now = int(proof[1]), int(clock())
            if not now < expiry <= now+30 or not hmac.compare_digest(tokens[0],csrf_token(revision,expiry)):
                return self.respond(403)
            result = pause_call('PUT',{'paused':request['paused'],'expected_revision':revision})
            if result is None:
                return reply(409,{'error':'queue_control_changed'})
            if result['paused'] is not request['paused'] or result['revision']==revision:
                return self.respond(503)
            return reply(200,result)

        def graph(self, raw, safe):
            # The browser's Origin/Authorization/Cookie/Sites claims never leave Visual.
            conn=HTTPConnection('127.0.0.1',graph_port,timeout=8)
            streaming=False
            try:
                outbound={'Authorization':'Bearer '+graph_key,'Accept-Encoding':'identity',
                          'Connection':'close'}
                since=self.headers.get_all('Last-Event-ID',[])
                if len(since)==1 and re.fullmatch(r'[A-Za-z0-9-]{1,80}(?::[A-Za-z0-9-]{1,80}){0,2}',since[0]):
                    outbound['Last-Event-ID']=since[0]
                conn.request(self.command,raw,headers=outbound)
                rsp=conn.getresponse()
                typ=rsp.headers.get_content_type()
                if (rsp.status not in (200,202,400,404,409,429) or rsp.headers.get('Content-Encoding') is not None
                    or typ not in ('application/json','application/octet-stream','text/event-stream')):
                    return self.respond(503)
                if typ=='text/event-stream' and rsp.status==200 and self.command=='GET':
                    streaming=True
                    self.send_response_only(200)
                    for k,v in {**safe,'Cache-Control':'private, no-store','Content-Type':'text/event-stream',
                                'X-Content-Type-Options':'nosniff','X-Accel-Buffering':'no','Connection':'close'}.items():
                        self.send_header(k,v)
                    self.end_headers()
                    self.close_connection=True
                    while chunk:=rsp.read1(16_384):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                blob=rsp.read(GRAPH_LIMIT+1)
                if len(blob)>GRAPH_LIMIT:
                    return self.respond(503)
                return self.respond(rsp.status,blob,typ,headers=safe)
            except (OSError,ValueError):
                if not streaming:
                    return self.respond(503)
            finally:
                conn.close()

        def dispatch(self):
            raw = self.requestline.split()
            if standalone is not None and len(raw)==3 and self.headers.get_all('Host',[])==[public_host]:
                return self.standalone(raw[1])
            if self.headers.get_all(STANDALONE_AUTH,[]) or self.headers.get_all(PAUSE_OWNER_AUTH,[]):
                return self.respond(421)
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
                    return self.respond(200,json.dumps({'stage':environment,'runtime_entry_sha':runtime_health[0],
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
                if fetch_queue is None or environment!='test':
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
    site_origin=os.environ.get('MEMORY_VISUAL_STANDALONE_ORIGIN')
    queue_origin=os.environ.get('MEMORY_VISUAL_BASE_ORIGIN') if site_origin else os.environ.get('MEMORY_VISUAL_'+mode.upper()+'_BASE_ORIGIN')
    standalone=(site_origin,read_small(credential.with_name('standalone-ingress-token'),66).decode('ascii').strip(),
                os.environ['MEMORY_VISUAL_GRAPH_ORIGIN'],
                read_small(credential.with_name('standalone-graph-token'),66).decode('ascii').strip()) if site_origin else None
    queue_secret = (read_small(credential.with_name('queue-base-token'), 66).decode('ascii').strip()
                    if queue_origin else None)
    health_path=credential.with_name('runtime-health-token')
    health=(digest(read_small(Path(__file__).resolve(strict=True),64_000)),
            read_small(health_path,66).decode('ascii').strip()) if health_path.is_file() else None
    enabled=os.environ.get('MEMORY_VISUAL_STANDALONE_PAUSE','0')
    if enabled not in ('0','1') or enabled=='1' and standalone is None:
        raise SystemExit('invalid standalone pause opt-in')
    pause=None
    if enabled=='1':
        if queue_origin != ('http://127.0.0.1:8893' if mode=='test' else 'http://127.0.0.1:8903'):
            raise SystemExit('standalone pause requires the exact lane Base listener')
        def load_pause(name):
            val=read_small(credential.with_name('pause-'+name),66).decode('ascii').strip()
            if not HEX.fullmatch(val):
                raise ValueError('invalid standalone pause credential')
            return val
        pause=(queue_origin,load_pause('read-token'),load_pause('write-token'),
               bytes.fromhex(load_pause('csrf-key')),load_pause('owner-ingress-token'),
               load_pause('owner-digest'),bytes.fromhex(load_pause('proof-key')))
    with make_server(ROOT / mode, secret, int(port), environment=mode,
                     queue_origin=queue_origin, queue_token=queue_secret, runtime_health=health,
                     standalone=standalone,pause=pause) as server:
        server.serve_forever()
