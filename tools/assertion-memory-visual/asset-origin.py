"""Loopback Visual origin for immutable assets and optional TEST Base queue reads."""
from hashlib import sha256
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
from ipaddress import IPv4Address
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

ENTRY_ASSET = re.compile(rb'((?:src|href)=")\./(assets/index-[A-Za-z0-9_-]+\.(?:js|css))(")')


def standalone_entry_html(html, release, files):
    """Resolve built asset references without moving the browser off /visual."""
    prefix = b'/visual/' + release.encode('ascii') + b'/'
    seen = set()

    def qualify(match):
        asset = match[2].decode('ascii')
        if asset not in files or asset in seen:
            raise ValueError('Invalid standalone entry asset')
        seen.add(asset)
        return match[1] + prefix + match[2] + match[3]

    result = ENTRY_ASSET.sub(qualify, html)
    expected = {name for name in files if name.startswith('assets/index-')}
    if seen != expected or {Path(name).suffix for name in seen} != {'.js', '.css'}:
        raise ValueError('Incomplete standalone entry assets')
    return result


def current_release(root):
    """Read the shared journal receiver's atomic pointer, without following it."""
    target = Path(os.readlink(root / 'current'))
    if target.parent not in (Path('releases'), root.absolute() / 'releases'):
        raise ValueError('current escaped this visual environment')
    key = target.name
    if not HEX.fullmatch(key):
        raise ValueError('invalid visual release identity')
    return key


QUEUE_LIMIT = 2_400_000 # up to 50 cards, each with 128 bounded member identifiers
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



def snapshot_queues(queues, limit, offset, *, batch_remove=False):
    if not isinstance(queues, list) or len(queues) > 16:
        return False
    ids = set()
    for q in queues:
        if (not isinstance(q, dict) or set(q) - {'source_count'} != {'id','status','ordering','total','next_offset','items'}
                or ('source_count' in q and (q['id']!='source' or type(q['source_count']) is not int or q['source_count']<q['total']))
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
            if (not isinstance(row, dict) or set(row) - {'hold_reason','session_id','remove_revision','preview_revision','members','member_count'} != {'id','status','phase','position','available_at'}
                    or (row.get('remove_revision') is not None and (q['id'] not in ('source','maintenance') or type(row['remove_revision']) is not str or not REVISION.fullmatch(row['remove_revision'])))
                    or (row.get('preview_revision') is not None and (q['id']!='source' or type(row['preview_revision']) is not str or not REVISION.fullmatch(row['preview_revision'])))
                    or row.get('hold_reason') not in (None,'global_pause','session_excluded','incomplete_source')
                    or (row.get('session_id') is not None and not session_identifier(row['session_id']))
                    or not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 512
                    or any(not isinstance(row[k], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',row[k])
                           for k in ('status','phase'))
                    or (row['position'] is not None and (type(row['position']) is not int or row['position'] < 1))
                    or (row['available_at'] is not None and
                        (not isinstance(row['available_at'], str) or len(row['available_at']) > 64))):
                return False
            if 'members' in row or 'member_count' in row:
                members=row.get('members')
                if (q['id']!='source' or 'source_count' not in q
                        or not re.fullmatch(r'batch:[a-f0-9]{64}',row['id'])
                        or 'preview_revision' in row
                        or ('remove_revision' in row and (not batch_remove or row['status'] not in ('ready','paused')
                            or row['phase']!='extract' or type(row['remove_revision']) is not str
                            or not REVISION.fullmatch(row['remove_revision'])))
                        or type(row.get('member_count')) is not int or type(members) is not list
                        or not 1<=len(members)==row['member_count']<=128):
                    return False
                member_ids=set()
                for member in members:
                    if (type(member) is not dict or set(member)-{'preview_revision'}!={'id'}
                            or not removal_identity({'queue_id':'source','item_id':member['id']})
                            or member['id'] in member_ids
                            or ('preview_revision' in member and (type(member['preview_revision']) is not str
                                or not REVISION.fullmatch(member['preview_revision'])))):
                        return False
                    member_ids.add(member['id'])
    return True


def queue_client(origin, key, *, stage='test', disclose_pause=False, graph_ready=None):
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
                                  'Connection': 'close',
                                  **({'X-Memory-Queue-Format':'batch-cards-v1','X-Memory-Queue-Batch-Remove':'atomic-v1','X-Memory-Queue-Reset-V2':'binding-v2'} if disclose_pause else {})})
            response = http.getresponse()
            if (response.status != 200 or response.headers.get_content_type() != 'application/json'
                    or response.headers.get('Content-Encoding') is not None):
                raise ValueError('Base queue unavailable')
            raw = response.read(QUEUE_LIMIT+1)
            if len(raw) > QUEUE_LIMIT:
                raise ValueError('Base queue page too large')
            value = unique_json(raw)
            ctl = value.get('processing_control') if isinstance(value, dict) else None
            if (not isinstance(value, dict) or set(value)-{'database_clear'} != SNAPSHOT_KEYS or value['stage'] != stage
                    or not isinstance(value['release_version'], str)
                    or not re.fullmatch(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', value['release_version'])
                    or not isinstance(value['release_generation'], str) or not HEX.fullmatch(value['release_generation'])
                    or not isinstance(value['snapshot_at'], str) or len(value['snapshot_at']) > 64
                    or value['global_order'] is not False or not snapshot_queues(value['queues'],limit,offset,batch_remove=disclose_pause)
                    or not isinstance(ctl, dict) or set(ctl) != {'paused', 'revision', 'can_change'}
                    or type(ctl['paused']) is not bool or type(ctl['can_change']) is not bool
                    or not isinstance(ctl['revision'], str) or not re.fullmatch(r'[0-9a-f]{32}',ctl['revision'])):
                raise ValueError('invalid TEST Base queue page')
            item = value.pop('database_clear',None)
            if disclose_pause and isinstance(item,dict) and callable(graph_ready):
                try:
                    clear_state({'stage':stage,**item},stage)
                except ValueError:
                    pass
                else:
                    if item['can_clear'] and graph_ready(item['scope']):
                        value['database_clear']={k:item[k] for k in ('revision','can_clear','capture_rearm')}
            capabilities={'live':False}
            if disclose_pause and ctl['can_change']:
                capabilities['standalone_pause']=True
            if disclose_pause and ctl['can_change']:
                capabilities['standalone_queue_sessions']=True
            if not disclose_pause:
                for q in value['queues']:
                    for row in q['items']:
                        row.pop('session_id',None)
                        # TEST legacy dashboard carries opaque optimistic-revision hints only.
                        # Exact owner controls separately gate any read of text or state change.
                        if stage!='test':
                            for k in ('remove_revision','preview_revision'): row.pop(k,None)
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


BROWSER_SESSIONS = '/api/v1/processing/sessions'
BASE_SESSIONS = '/api/v1/queue-sessions'
BASE_POLICY = '/api/v1/queue-processing-policy'
BROWSER_REMOVE = '/api/v1/processing/items/remove'
BASE_REMOVE = '/api/v1/queue-items/remove'
BROWSER_CLEAR = '/api/v1/processing/database/clear-v2'
BASE_CLEAR = '/api/v1/queue-database/clear'
BROWSER_PREVIEW = '/api/v1/processing/items/source-preview'
BASE_PREVIEW = '/api/v1/queue-items/source-preview'


REARM_STATES=frozenset(('not_cleared','unknown','not_required','waiting','confirmed'))

def clear_state(value, stage):
    if (type(value) is not dict or set(value)!={'stage','revision','can_clear','scope','capture_rearm'}
            or value['stage']!=stage or type(value['capture_rearm']) is not str or value['capture_rearm'] not in REARM_STATES or type(value['can_clear']) is not bool or type(value['scope']) is not str or not HEX.fullmatch(value['scope'])
            or type(value['revision']) is not str or not REVISION.fullmatch(value['revision'])):
        raise ValueError('invalid Base database clear state')
    return value


def clear_receipt(value, stage):
    if (type(value) is not dict or set(value)!={'stage','revision','cleared','capture_rearm'}
            or value['stage']!=stage or value['cleared'] is not True or value['capture_rearm'] not in ('waiting','not_required')
            or type(value['revision']) is not str or not REVISION.fullmatch(value['revision'])):
        raise ValueError('invalid Base database clear receipt')
    return value


def preview_receipt(value, stage):
    if (type(value) is not dict or set(value)!={'stage','queue_id','item_id','source_preview','source_role'}
            or value['stage']!=stage or value['queue_id']!='source' or not removal_identity(value)
            or type(value['source_preview']) is not str or not 0<len(value['source_preview'])<=240
            or value['source_role'] not in (None,'user','assistant','system','developer','tool')):
        raise ValueError('invalid Base source preview')
    return value



def removal_identity(value):
    return (value.get('queue_id') in ('source','maintenance') and type(value.get('item_id')) is str
            and 0<len(value['item_id'])<=512 and not any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in value['item_id']))


def removal_receipt(value, stage):
    if (type(value) is not dict or set(value)!={'stage','queue_id','item_id','removed','effect'} or
            value['stage']!=stage or value['removed'] is not True or not removal_identity(value) or
            value['effect']!=('source_and_queue' if value['queue_id']=='source' else 'queue_only')):
        raise ValueError('invalid Base removal receipt')
    return value



def session_identifier(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}',value) is not None


def selected_ids(value):
    return type(value) is list and len(value)<=50 and all(session_identifier(v) for v in value) and len(set(value))==len(value)


def sessions_state(value, stage):
    if (type(value) is not dict or set(value)!={'stage','revision','selected_session_ids','sessions'}
            or value['stage']!=stage or not isinstance(value['revision'],str) or not REVISION.fullmatch(value['revision'])
            or not selected_ids(value['selected_session_ids']) or type(value['sessions']) is not list or len(value['sessions'])>150):
        raise ValueError('invalid Base session policy')
    seen=set()
    for row in value['sessions']:
        if (type(row) is not dict or set(row)!={'id','queued','eligible'} or not session_identifier(row['id'])
                or row['id'] in seen or type(row['queued']) is not int or row['queued']<0 or type(row['eligible']) is not bool):
            raise ValueError('invalid Base session choice')
        seen.add(row['id'])
    return value


def policy_date(value):
    if value is None:
        return None
    from datetime import datetime
    if type(value) is not str or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)',value):
        raise ValueError('invalid policy timestamp')
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def canonical_policy(value):
    if (type(value) is not dict or set(value)!={'mode','included_session_ids','excluded_session_ids','added_from','added_through'}
            or value['mode'] not in ('all','include')
            or not selected_ids(value['included_session_ids']) or not selected_ids(value['excluded_session_ids'])
            or set(value['included_session_ids']) & set(value['excluded_session_ids'])
            or (value['mode']=='all' and value['included_session_ids'])):
        raise ValueError('invalid processing policy')
    low, high=map(policy_date,(value['added_from'],value['added_through']))
    if low is not None and high is not None and low>high:
        raise ValueError('invalid policy time range')
    return (value['mode'],frozenset(value['included_session_ids']),frozenset(value['excluded_session_ids']),low,high)


def policy_state(value,stage):
    if type(value) is not dict or set(value)!={'stage','revision','policy','sessions'}:
        raise ValueError('invalid Base processing policy response')
    canonical_policy(value['policy'])
    sessions_state({k:value[k] for k in ('stage','revision','sessions')}|{'selected_session_ids':[]},stage)
    return value


class PolicyUnavailable(ValueError):
    pass


def pause_state(value, stage):
    if (type(value) is not dict or set(value) != {'stage','paused','revision'}
            or value['stage'] != stage or type(value['paused']) is not bool
            or type(value['revision']) is not str or not REVISION.fullmatch(value['revision'])):
        raise ValueError('invalid Base pause state')
    return value


def pause_client(origin, read_key, write_key, signer, *, stage, route=BASE_PAUSE, validate=pause_state, max_bytes=1024):
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
        if route==BASE_CLEAR:
            headers['X-Memory-Queue-Reset-V2']='binding-v2'
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
        conn = HTTPConnection('127.0.0.1', int(address[1]), timeout=18 if route==BASE_CLEAR else 4)
        try:
            conn.request(method, route, body=body, headers=headers)
            rsp = conn.getresponse()
            if route==BASE_POLICY and (rsp.status==404 or method=='GET' and rsp.status==401):
                raise PolicyUnavailable()
            if method == 'PUT' and rsp.status == 409:
                return None
            if (rsp.status != 200 or rsp.headers.get_content_type() != 'application/json'
                    or rsp.headers.get('Content-Encoding') is not None):
                raise ValueError('Base pause unavailable')
            raw = rsp.read(max_bytes+1)
            if len(raw) > max_bytes:
                raise ValueError('invalid Base pause response')
            parsed=unique_json(raw)
            if route==BASE_CLEAR:
                return (clear_state if method=='GET' else clear_receipt)(parsed,stage)
            return validate(parsed, stage)
        finally:
            conn.close()
    return call


def pause_signer(stage, owner, secret, *, clock=time.time, route=BASE_PAUSE):
    """Service-only signer: supplied to handlers guarded by the dedicated verified owner ingress."""
    if (stage not in ('test','prod') or type(owner) is not str or not HEX.fullmatch(owner)
            or type(secret) is not bytes or len(secret) != 32):
        raise ValueError('invalid pause signer')
    def sign(body, revision):
        iat = int(clock())
        expires = iat+15
        nonce = secrets.token_hex(16)
        message = ('\n'.join(('memory-queue-pause-v1','PUT',route,stage,
                   digest(body),owner,revision,str(iat),str(expires),nonce))+'\n').encode('ascii')
        return dict(zip(('X-Memory-Pause-Owner','X-Memory-Pause-Issued-At','X-Memory-Pause-Expires-At',
                         'X-Memory-Pause-Nonce','X-Memory-Pause-Signature'),
                        (owner,str(iat),str(expires),nonce,hmac.digest(secret,message,'sha256').hex())))
    return sign


def owner_live_csp_origin(environment, standalone, origin):
    if origin is None:
        return ''
    # Production has no approved direct endpoint or lane-specific issuer yet.
    if environment != 'test' or standalone is None or not isinstance(origin, str):
        raise ValueError('owner live origin requires explicit TEST standalone')
    try:
        ip = IPv4Address(origin.removeprefix('https://'))
    except ValueError:
        raise ValueError('invalid owner live HTTPS origin') from None
    if not ip.is_global or ip.is_multicast or origin != 'https://' + str(ip):
        raise ValueError('invalid owner live HTTPS origin')
    return ' ' + origin


def make_server(root, token, port=8829, *, environment='test', queue_origin=None, queue_token=None, runtime_health=None, standalone=None, pause=None, clock=time.time, owner_live_origin=None):
    if environment not in ('test', 'prod') or not HEX.fullmatch(token):
        raise ValueError('invalid dedicated TEST token')
    live_csp=owner_live_csp_origin(environment,standalone,owner_live_origin)

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
        def graph_clear_ready(scope):
            # Prove the reader is reset-aware and bound to this exact DB epoch
            # using its existing authenticated local read channel. Never forward
            # or expose the opaque scope to any browser/client.
            conn=HTTPConnection('127.0.0.1',graph_port,timeout=2)
            try:
                conn.request('GET','/api/v1/capabilities?database_clear_scope=1',
                    headers={'Authorization':'Bearer '+graph_key,'Accept-Encoding':'identity','Connection':'close'})
                response=conn.getresponse()
                if response.status!=200 or response.headers.get_content_type()!='application/json':
                    return False
                raw=response.read(1025)
                if len(raw)>1024:
                    return False
                return unique_json(raw)=={'stage':environment,'database_clear_scope':scope}
            except (OSError,ValueError):
                return False
            finally:
                conn.close()

        fetch_owner_queue = queue_client(queue_origin,queue_token,stage=environment,
                                        disclose_pause=True,graph_ready=graph_clear_ready)
        pause_call = pause_client(origin,read_key,write_key,
                                  pause_signer(environment,owner,proof_key,clock=clock),stage=environment)
        sessions_call = pause_client(origin,read_key,write_key,
            pause_signer(environment,owner,proof_key,clock=clock,route=BASE_SESSIONS),
            stage=environment,route=BASE_SESSIONS,validate=sessions_state,max_bytes=524288)
        policy_call = pause_client(origin,read_key,write_key,
            pause_signer(environment,owner,proof_key,clock=clock,route=BASE_POLICY),
            stage=environment,route=BASE_POLICY,validate=policy_state,max_bytes=524288)
        remove_call = pause_client(origin,read_key,write_key,
            pause_signer(environment,owner,proof_key,clock=clock,route=BASE_REMOVE),
            stage=environment,route=BASE_REMOVE,validate=removal_receipt,max_bytes=4096)
        preview_call = pause_client(origin,read_key,write_key,
            pause_signer(environment,owner,proof_key,clock=clock,route=BASE_PREVIEW),
            stage=environment,route=BASE_PREVIEW,validate=preview_receipt,max_bytes=4096)
        clear_call = pause_client(origin,read_key,write_key,
            pause_signer(environment,owner,proof_key,clock=clock,route=BASE_CLEAR),
            stage=environment,route=BASE_CLEAR,max_bytes=1024)
        def csrf_token(revision, expiry, kind='pause'):
            msg = f'visual-{kind}-v1\n{environment}\n{public_origin}\n{revision}\n{expiry}'.encode('ascii')
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
            safe={'Content-Security-Policy':f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'{live_csp}; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                  'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Strict-Transport-Security':'max-age=31536000'}
            try:
                path,query=path_and_query(raw)
            except (ValueError,UnicodeError):
                return self.respond(400)
            try:
                if self.headers.get('Sec-Fetch-Site')=='cross-site' and path.startswith('/api/'):
                    return self.respond(403)
                if path in (BROWSER_CLEAR,BROWSER_CLEAR+'/prepare'):
                    if pause is None or raw != path or query:
                        return self.respond(404)
                    return self.clear_request(path,safe)
                if path in (BROWSER_REMOVE,BROWSER_REMOVE+'/prepare',BROWSER_PREVIEW,BROWSER_PREVIEW+'/prepare'):
                    if pause is None or raw != path or query:
                        return self.respond(404)
                    return self.remove_request(path,safe,preview=path in (BROWSER_PREVIEW,BROWSER_PREVIEW+'/prepare'))
                if path in (BROWSER_SESSIONS,BROWSER_SESSIONS+'/prepare'):
                    if pause is None or raw != path or query:
                        return self.respond(404)
                    return self.pause_request(path,safe, sessions=True)
                if pause is not None and path in (BROWSER_PAUSE,BROWSER_PAUSE+'/prepare'):
                    if raw != path or query:
                        return self.respond(404)
                    return self.pause_request(path,safe)
                if self.command not in ('GET','HEAD') or self.headers.get_all('Transfer-Encoding') or any(
                        n!='0' for n in self.headers.get_all('Content-Length',[])):
                    return self.respond(405)
                if path in ('/','/visual','/visual/'):
                    release=current_release(root)
                    directory,lookup=manifest(root,release)
                    page=read_small(directory/'index.html',LIMIT)
                    if digest(page)!=lookup['index.html']:
                        return self.respond(404)
                    page=standalone_entry_html(page,release,lookup)
                    if release!=current_release(root):
                        return self.respond(404)
                    return self.respond(200,page,MIME['.html'],headers=safe)
                if path=='/api/v1/session':
                    if query:
                        return self.respond(404)
                    return self.respond(200,b'{"authenticated":true}','application/json',headers=safe)
                if path=='/api/v1/capabilities':
                    q=parse_qs(query,keep_blank_values=True,max_num_fields=12)
                    if 'database_clear_scope' in q:
                        return self.respond(404)
                    if 'queue_status' in q:
                        if set(q)-{'queue_status','limit','offset'} or q['queue_status']!=['1'] or any(len(v)!=1 for v in q.values()):
                            return self.respond(400)
                        l,o=q.get('limit',['50'])[0],q.get('offset',['0'])[0]
                        if any(not re.fullmatch(r'(?:0|[1-9][0-9]{0,6})',v) for v in (l,o)) or not 1<=int(l)<=50 or int(o)>1_000_000:
                            return self.respond(400)
                        fetch = fetch_owner_queue if self.has_pause_owner() else fetch_queue
                        page = fetch(int(l),int(o))
                        if environment=='prod' and self.has_pause_owner():
                            value = unique_json(page)
                            value['capabilities']['owner_database_clear_post']=True
                            value['owner_database_clear_csrf']=csrf_token('ordinary-post',int(clock())+30,
                                                                         'database-clear-post')
                            page=json.dumps(value,separators=(',',':'),allow_nan=False).encode('utf-8')
                        return self.respond(200,page,'application/json',headers=safe)
                if GRAPH_RE.fullmatch(path):
                    return self.graph(raw,safe)
                match=re.fullmatch(r'/visual/([a-f0-9]{64})/(.*)',path)
                if match:
                    release,name=match[1],match[2] or 'index.html'
                    if not ITEM.fullmatch(name):
                        return self.respond(404)
                    if release!=current_release(root):
                        if name=='index.html':
                            return self.respond(307,headers={**safe,'Location':'/visual'})
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

        def pause_request(self,path,safe,sessions=False):
            if not self.has_pause_owner():
                return self.respond(403)
            prepare = path.endswith('/prepare')
            call = sessions_call if sessions else pause_call
            kind = 'sessions' if sessions else 'pause'
            if self.command != ('POST' if prepare else 'PUT'):
                return self.respond(405)
            if self.headers.get_all('Origin',[]) != [public_origin]:
                return self.respond(403)
            lengths = self.headers.get_all('Content-Length',[])
            if (self.headers.get_all('Transfer-Encoding') or len(lengths)!=1
                    or not re.fullmatch(r'[1-9][0-9]{0,4}' if sessions else r'[1-9][0-9]{0,2}',lengths[0])):
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
            processing_policy=sessions and type(request) is dict and (
                request=={'format':'policy-v1'} if prepare else 'policy' in request)
            if processing_policy:
                call,kind=policy_call,'processing-policy'
            if prepare:
                if type(request) is not dict or (request and not processing_policy):
                    return self.respond(400)
                try:
                    state = call('GET')
                except PolicyUnavailable:
                    return self.respond(404)
                return reply(200,{**state,'csrf_token':csrf_token(state['revision'],int(clock())+30,kind)})
            operation='policy' if processing_policy else ('selected_session_ids' if sessions else 'paused')
            fields={'stage','expected_revision',operation}
            if (type(request) is not dict or set(request)!=fields
                    or type(request['expected_revision']) is not str or not REVISION.fullmatch(request['expected_revision'])):
                return self.respond(400)
            if processing_policy:
                try:
                    wanted_policy=canonical_policy(request['policy'])
                except ValueError:
                    return self.respond(400)
            elif not (selected_ids(request[operation]) if sessions else type(request[operation]) is bool):
                return self.respond(400)
            if request['stage']!=environment:
                return reply(409,{'error':'queue_control_changed'})
            revision = request['expected_revision']
            tokens = self.headers.get_all('X-CSRF-Token',[])
            proof = re.fullmatch(r'([0-9]{1,12})\.[0-9a-f]{64}',tokens[0]) if len(tokens)==1 else None
            if not proof:
                return self.respond(403)
            expiry, now = int(proof[1]), int(clock())
            if not now < expiry <= now+30 or not hmac.compare_digest(tokens[0],csrf_token(revision,expiry,kind)):
                return self.respond(403)
            try:
                result = call('PUT',{operation:request[operation],'expected_revision':revision})
            except PolicyUnavailable:
                return self.respond(404)
            if result is None:
                return reply(409,{'error':'queue_control_changed'})
            matches=(canonical_policy(result['policy'])==wanted_policy if processing_policy else
                     set(result[operation])==set(request[operation]) if sessions else
                     result[operation] is request[operation])
            if not matches or result['revision']==revision:
                return self.respond(503)
            return reply(200,result)

        def clear_request(self,path,safe):
            if not self.has_pause_owner() or self.headers.get_all('Origin',[]) != [public_origin]:
                return self.respond(403)
            prepare=path.endswith('/prepare')
            if self.command != ('POST' if prepare else 'PUT'):
                return self.respond(405)
            lengths=self.headers.get_all('Content-Length',[])
            if (self.headers.get_all('Transfer-Encoding') or len(lengths)!=1
                    or not re.fullmatch(r'[1-9][0-9]{0,2}',lengths[0])):
                return self.respond(400)
            if self.headers.get_all('Content-Type',[])!=['application/json']:
                return self.respond(415)
            try:
                raw=self.rfile.read(int(lengths[0]))
                if len(raw)!=int(lengths[0]):
                    return self.respond(400)
                request=unique_json(raw)
            except (ValueError,OSError,RecursionError):
                return self.respond(400)
            if environment=='prod' and not prepare and type(request) is dict and set(request)=={'confirmation'}:
                if request != {'confirmation':'ОЧИСТИТЬ PROD'}:
                    return self.respond(400)
                tokens=self.headers.get_all('X-CSRF-Token',[])
                token=re.fullmatch(r'([0-9]{1,12})\.[0-9a-f]{64}',tokens[0]) if len(tokens)==1 else None
                now=int(clock())
                if (not token or not now < int(token[1]) <= now+30
                        or not hmac.compare_digest(tokens[0],csrf_token('ordinary-post',int(token[1]),'database-clear-post'))):
                    return self.respond(403)
                current=clear_call('GET')
                if not current['can_clear']:
                    return self.respond(409,b'{"error":"database_clear_state_changed"}','application/json',headers=safe)
                result=clear_call('PUT',{'expected_revision':current['revision']})
                if result is None or result['revision']==current['revision']:
                    return self.respond(409,b'{"error":"database_clear_state_changed"}','application/json',headers=safe)
                return self.respond(200,json.dumps(result,separators=(',',':')).encode(),'application/json',headers=safe)
            fields={'stage','expected_revision','confirmation'} if prepare else {'stage','expected_revision'}
            if (type(request) is not dict or set(request)!=fields
                    or request['stage'] not in ('test','prod')
                    or type(request['expected_revision']) is not str
                    or not REVISION.fullmatch(request['expected_revision'])):
                return self.respond(400)
            def reply(status,value):
                return self.respond(status,json.dumps(value,separators=(',',':')).encode(),'application/json',
                                    headers={**safe,'Cache-Control':'no-store'})
            if request['stage']!=environment:
                return reply(409,{'error':'database_clear_state_changed'})
            revision=request['expected_revision']
            if prepare:
                if request['confirmation']!='ОЧИСТИТЬ '+environment.upper():
                    return self.respond(400)
                state=clear_call('GET')
                if not state['can_clear'] or state['revision']!=revision or not graph_clear_ready(state['scope']):
                    return reply(409,{'error':'database_clear_state_changed'})
                return reply(200,{'stage':environment,'revision':revision,
                                  'csrf_token':csrf_token(revision,int(clock())+30,'database-clear')})
            tokens=self.headers.get_all('X-CSRF-Token',[])
            token=re.fullmatch(r'([0-9]{1,12})\.[0-9a-f]{64}',tokens[0]) if len(tokens)==1 else None
            if not token:
                return self.respond(403)
            expiry,now=int(token[1]),int(clock())
            if not now < expiry <= now+30 or not hmac.compare_digest(tokens[0],csrf_token(revision,expiry,'database-clear')):
                return self.respond(403)
            state=clear_call('GET')
            if not state['can_clear'] or state['revision']!=revision or not graph_clear_ready(state['scope']):
                return reply(409,{'error':'database_clear_state_changed'})
            result=clear_call('PUT',{'expected_revision':revision})
            if result is None:
                return reply(409,{'error':'database_clear_state_changed'})
            if result['revision']==revision:
                return self.respond(503)
            return reply(200,result)

        def remove_request(self,path,safe,preview=False):
            if not self.has_pause_owner() or self.headers.get_all('Origin',[]) != [public_origin]:
                return self.respond(403)
            prepare=path.endswith('/prepare')
            if self.command != ('POST' if prepare else 'PUT'):
                return self.respond(405)
            lengths=self.headers.get_all('Content-Length',[])
            if self.headers.get_all('Transfer-Encoding') or len(lengths)!=1 or not re.fullmatch(r'[1-9][0-9]{0,3}',lengths[0]) or int(lengths[0])>4096:
                return self.respond(400)
            if self.headers.get_all('Content-Type',[])!=['application/json']:
                return self.respond(415)
            try:
                request=unique_json(self.rfile.read(int(lengths[0])))
            except (ValueError,OSError,RecursionError):
                return self.respond(400)
            if (type(request) is not dict or set(request)!={'stage','queue_id','item_id','expected_revision'} or
                    not removal_identity(request) or type(request['expected_revision']) is not str or
                    not REVISION.fullmatch(request['expected_revision'])):
                return self.respond(400)
            def reply(status,value):
                return self.respond(status,json.dumps(value,separators=(',',':')).encode(),'application/json',headers=safe)
            if request['stage']!=environment:
                return reply(409,{'error':'queue_item_changed'})
            if preview and request['queue_id']!='source':return self.respond(400)
            kind='preview' if preview else 'remove'
            call=preview_call if preview else remove_call
            command={k:request[k] for k in ('queue_id','item_id','expected_revision')}
            bound=digest(json.dumps(command,separators=(',',':')).encode('ascii'))
            if prepare:
                return reply(200,{'stage':environment,'csrf_token':csrf_token(bound,int(clock())+30,kind)})
            tokens=self.headers.get_all('X-CSRF-Token',[])
            token=re.fullmatch(r'([0-9]{1,12})\.[0-9a-f]{64}',tokens[0]) if len(tokens)==1 else None
            if not token:
                return self.respond(403)
            expiry,now=int(token[1]),int(clock())
            if not now < expiry <= now+30 or not hmac.compare_digest(tokens[0],csrf_token(bound,expiry,kind)):
                return self.respond(403)
            result=call('PUT',command)
            if result is None:return reply(409,{'error':'queue_item_changed'})
            if any(result[k]!=command[k] for k in ('queue_id','item_id')):return self.respond(503)
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
            if runtime_health is not None and len(raw)==3 and raw[1]=='/__visual-retention-health':
                if self.command!='GET' or self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Length'):
                    return self.respond(404)
                header=self.headers.get_all('Authorization',[])
                if len(header)!=1 or not hmac.compare_digest(header[0], 'Bearer '+runtime_health[1]):
                    return self.respond(401)
                try:
                    current=current_release(root)
                    manifest(root,current)
                    return self.respond(200,json.dumps({'stage':environment,'runtime_entry_sha':runtime_health[0],
                        'static_digest':current,'current_only':True},separators=(',',':')).encode(),'application/json')
                except (ValueError,OSError):
                    return self.respond(503)
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
                # Even the authenticated legacy asset relay is current-only.
                # It returns a typed miss; only owner HTML navigation redirects.
                if release != current_release(root):
                    return self.respond(404)
                directory, files = manifest(root, release)
                if name not in files:
                    return self.respond(404)
                asset = read_small(directory / name, LIMIT)
                if digest(asset) != files[name] or release != current_release(root):
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
                     standalone=standalone,pause=pause,
                     owner_live_origin=os.environ.get('MEMORY_VISUAL_TEST_DIRECT_EVENTS_ORIGIN')) as server:
        server.serve_forever()
