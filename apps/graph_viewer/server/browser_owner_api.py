"""Same-origin browser API on Base's Graph web listener, never the private MCP listener."""
import asyncio
from collections import OrderedDict
from contextlib import ExitStack
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import secrets
import time

from types import SimpleNamespace

from starlette.responses import JSONResponse
from starlette.routing import Match, Route

from prototypes.assertion_memory import database_clear, processing_control
from prototypes.assertion_memory.queue_item_removal import check_request
from prototypes.assertion_memory.queue_processing_policy import validate as validate_policy
from prototypes.assertion_memory.queue_session_control import normalize

from .common import APIError

PREFIX = '/api/v1/processing/'
CONTROLS = ('pause','sessions','items/source-preview','items/remove','database/clear-v2')
DIRECT = '/api/v1/events-direct'
EXACT = frozenset(PREFIX+c+s for c in CONTROLS for s in ('','/prepare'))
FORBIDDEN = {b'authorization', b'forwarded', b'x-real-ip', b'x-memory-environment', b'x-memory-lane'}

def ticket_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def require_transport(request):
    scope=request.scope
    headers=[name.lower() for name,_ in scope['headers']]
    if (headers.count(b'host')!=1 or headers.count(b'origin')>1 or headers.count(b'cookie')>1
            or any(name in FORBIDDEN or name.startswith((b'x-forwarded-',b'x-memory-'))
                   for name in headers if not (name==b'authorization' and request.url.path==DIRECT))):
        raise APIError('invalid_transport','Untrusted browser transport headers',403)
    if request.url.path in EXACT or request.url.path in {'/api/v1/session','/api/v1/events-ticket','/api/v1/capabilities',DIRECT}:
        if scope.get('raw_path')!=request.url.path.encode('ascii'):
            raise APIError('not_found','Unknown API resource',404)
    from .auth import COOKIE
    if sum(part.partition('=')[0].strip()==COOKIE
           for part in request.headers.get('cookie','').split(';'))>1:
        raise APIError('invalid_transport','Ambiguous owner cookie',403)
    if request.url.path=='/api/v1/capabilities' and 'database_clear_scope' in request.query_params:
        raise APIError('not_found','Unknown API resource',404)
    if request.url.path in EXACT and scope.get('query_string',b''):
        raise APIError('not_found','Unknown API resource',404)

def validate_query(request, route, path_params):
    pairs=request.query_params.multi_items()
    keys={key for key,_ in pairs}
    if not pairs:
        return
    if len(keys)!=len(pairs) or len(pairs)>12 or len(request.scope['query_string'])>48_000:
        raise APIError('invalid_query','Unexpected or repeated query argument',400)
    path=route.path
    allowed={
        '/api/v1/capabilities':{'queue_status','limit','offset'} if 'queue_status' in keys else set(),
        '/api/v1/graph':{'mode','at','history','undated','session_id','run_id','output_snapshot_id'},
        '/api/v1/events':{'channel'} if 'channel' in keys else {'after','revision'},
        '/api/v1/search':{'q','mode','limit','cursor','revision'},
        '/api/v1/nodes/{id:path}/edges':{'revision','visible','limit','cursor'},
        '/api/v1/nodes/{id:path}':{'revision','offset','limit'},
        '/api/v1/edges/{id:path}':{'revision'},
        '/api/v1/sources/{id:path}':{'revision','context','offset','limit','focus_start','focus_end'},
        '/api/v1/overviews/{id:path}':{'revision','offset','limit'},
    }.get(path,set())
    if path=='/api/v1/graph/{revision}/{name}' and path_params.get('name')=='sessions':
        allowed={'query','limit','cursor'}
    if path in ('/api/v1/graph/{revision}/{kind}/{ordinal:int}',
                '/api/v1/graph/{revision}/{kind}/{ordinal:int}/{action}'):
        allowed=({'visible','limit','cursor'} if path_params.get('action')=='edges' else {'offset','limit'}
                 ) if path_params.get('kind')=='nodes' else set()
    if not keys<=allowed:
        raise APIError('invalid_query','Unexpected or repeated query argument',400)


class Tickets:
    """Opaque single-use bearer; single Graph process, no shared/browser signing key."""
    def __init__(self, clock=time.time):
        self.clock=clock
        self.pending=OrderedDict()

    def issue(self, key):
        now=int(self.clock())
        for t,(expiry,_) in list(self.pending.items()):
            if expiry<=now:
                self.pending.pop(t,None)
        if len(self.pending)>=512:
            raise APIError('try_later','Too many pending confirmations',429)
        expiry=now+20
        token=f'{expiry}.{secrets.token_hex(32)}'
        self.pending[token]=(expiry,key)
        return token

    def valid(self, token, key):
        row=self.pending.get(token) if token is not None else None
        if type(token) is not str or not re.fullmatch(r'[0-9]{1,12}\.[0-9a-f]{64}',token) or row is None or row[0]<=int(self.clock()) or row[1]!=key:
            raise APIError('csrf_denied','Expired or mismatched action confirmation',403)

    def consume(self, token, key):
        self.valid(token,key)
        del self.pending[token]

class BrowserOwnerAPI:
    def __init__(self, *, stage, perform, queue_snapshot, clear_enabled=False, dashboard_live=True, clock=time.time):
        if stage not in ('test','prod') or not callable(perform) or not callable(queue_snapshot) or type(dashboard_live) is not bool:
            raise ValueError('Expected explicit Base browser API lane')
        self.stage,self.perform,self.queue_snapshot=stage,perform,queue_snapshot
        self.clear_enabled=clear_enabled is True
        self.dashboard_live=dashboard_live
        self.tickets=Tickets(clock)

    def allows(self, request):
        for route in request.app.routes:
            if isinstance(route,Route) and route.path.startswith('/api/v1/'):
                match, scope=route.matches(request.scope)
                if match==Match.FULL:
                    params=scope.get('path_params',{})
                    if ((route.path=='/api/v1/graph/{revision}/{name}'
                         and params.get('name') not in {'topics','sessions','nodes.bin','edges.bin'})
                        or (route.path.startswith('/api/v1/graph/{revision}/{kind}/')
                            and (params.get('kind') not in {'nodes','edges'}
                                 or ('action' in params and (params['kind'],params['action'])!=('nodes','edges'))))):
                        return False
                    validate_query(request,route,params)
                    return True
        return False

    def routes(self):
        return [Route(PREFIX+k+suffix, self.control,methods=[method], name=k+suffix)
                for k in CONTROLS if k!='database/clear-v2' or self.stage=='test' or self.clear_enabled for suffix,method in (('/prepare','POST'),('','PUT'))]

    def graph_ready(self, request, scope):
        manager=request.app.state.manager
        if not self.clear_enabled or not manager._poller or manager._poller.done():
            return False
        current=manager.sync_generation()
        return current is not None and database_clear.generation_scope(self.stage,current)==scope

    async def snapshot(self, request, limit, offset):
        value=await asyncio.to_thread(self.queue_snapshot,limit=limit,offset=offset)
        if value.get('stage')!=self.stage:
            raise APIError('queue_unavailable','Wrong Base queue scope',503)
        # Reuse deployed queue overlay contract: native prepared proof, not old session-token PUT.
        ready=self.stage=='test' or (value.get('processing_control') or {}).get('can_change') is True
        value={**value,'capabilities':{**value.get('capabilities',{}),'live':self.dashboard_live,'standalone_pause':ready,
                                      'queue_processing_policy':ready},'readiness':[]}
        if 'processing_control' in value:
            value['processing_control']={**value['processing_control'],'can_change':False}
        clear=value.pop('database_clear',None)
        if clear is not None and self.graph_ready(request,clear['scope']):
            value['database_clear']={k:clear[k] for k in ('can_clear','revision','capture_rearm')}
        return JSONResponse(value)

    async def control(self, request):
        # Boundary already rejects transport/header and validates current owner session.
        if request.app.state.owner.session(request) is None:
            raise APIError('owner_required','Owner session required',401)
        from .app import body
        from .auth import check_origin, COOKIE
        check_origin(request,required=True)
        if request.headers.getlist('origin')!=[request.app.state.public_origin]:
            raise APIError('origin_denied','Same-origin request required',403)
        if request.headers.getlist('content-type')!=['application/json']:
            raise APIError('invalid_request','Expected JSON',415)
        name=request.url.path.removeprefix(PREFIX)
        prepare=name.endswith('/prepare')
        name=name.removesuffix('/prepare')
        data=await body(request,limit=8192,unique=True)
        credential=request.cookies.get(COOKIE,'')
        owner=ticket_hash(credential)
        token_headers=request.headers.getlist('x-csrf-token')
        if name=='database/clear-v2' and not self.clear_enabled:
            raise APIError('not_found','Unknown API resource',404)
        kind=name
        if name=='sessions' and (data=={'format':'policy-v1'} if prepare else 'policy' in data):
            kind='policy'
        fields={'stage','expected_revision'}
        command=None; revision=data.get('expected_revision'); binding=''
        state={}
        try:
            if kind in ('pause','sessions','policy') and prepare:
                if data!=({'format':'policy-v1'} if kind=='policy' else {}):
                    raise ValueError()
                state=await asyncio.to_thread(self.perform,kind+'_prepare',{})
                revision=state['revision']
            elif kind=='database/clear-v2':
                if set(data)!=fields|({'confirmation'} if prepare else set()):
                    raise ValueError()
                if prepare and data['confirmation']!='ОЧИСТИТЬ '+self.stage.upper():
                    raise ValueError()
                command='clear_commit'
            elif kind in ('items/remove','items/source-preview'):
                if set(data)!=fields|{'queue_id','item_id'}:
                    raise ValueError()
                check_request(data['queue_id'],data['item_id'])
                if kind=='items/source-preview' and data['queue_id']!='source':
                    raise ValueError()
                binding=json.dumps([data['queue_id'],data['item_id']],separators=(',',':'))
                command='item_remove' if kind=='items/remove' else 'item_preview'
            else:
                field={'pause':'paused','sessions':'selected_session_ids','policy':'policy'}[kind]
                if set(data)!=fields|{field}:
                    raise ValueError()
                if kind=='pause' and type(data[field]) is not bool:
                    raise ValueError()
                if kind=='sessions':
                    normalize(data[field])
                if kind=='policy':
                    validate_policy(data[field])
                command=kind+'_set'
            if type(revision) is not str or not processing_control.REVISION.fullmatch(revision):
                raise ValueError()
            if data.get('stage', self.stage)!=self.stage:
                raise processing_control.Conflict('Wrong deployment stage')
            key=(owner,self.stage,kind,revision,binding)
            if not prepare:
                if len(token_headers)!=1:
                    raise APIError('csrf_denied','Action confirmation required',403)
                self.tickets.valid(token_headers[0],key)
            if kind=='database/clear-v2':
                guard=await asyncio.to_thread(self.perform,'clear_prepare',{})
                if (not guard['can_clear'] or guard['revision']!=revision
                        or not self.graph_ready(request,guard['scope'])):
                    raise processing_control.Conflict('Database generation or continuity changed')
                state={'revision':revision}
            if prepare:
                return JSONResponse({**state,'stage':self.stage,'csrf_token':self.tickets.issue(key)})
            if request.app.state.owner.session(request) is None:
                raise APIError('owner_required','Owner session required',401)
            # Same event loop atomically consumes once before handing the write to its existing Base transaction.
            self.tickets.consume(token_headers[0],key)
            args={k:v for k,v in data.items() if k!='stage'}
            result=await asyncio.to_thread(self.perform,command,args)
            return JSONResponse(result)
        except processing_control.Conflict:
            return JSONResponse({'error':'control_changed'},status_code=409)
        except ValueError:
            raise APIError('invalid_request','Invalid browser control request',400) from None

_TEST_CLEAR_PROFILE=re.compile(r'([0-9a-f]{64}):([0-9a-f]{64}|reusable)\Z')
_TEST_CLEAR_PROFILE_PATH=Path('/etc/assertion-release/test/browser-owner-clear-profile.json')

def read_test_clear_profile(path, *, owner=0):
    """Accept only a small, regular, immutable-by-service rollout record."""
    try:
        entry=path.lstat()
    except FileNotFoundError:
        return None
    for parent in (path.parent,path.parent.parent):
        st=parent.lstat()
        if (parent.resolve(strict=True)!=parent or not stat.S_ISDIR(st.st_mode)
                or st.st_uid!=owner or st.st_mode&0o022):
            raise ValueError('Untrusted TEST clear profile directory')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        st=os.fstat(handle.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=owner or st.st_nlink!=1
                or stat.S_IMODE(st.st_mode)!=0o644 or not 0<st.st_size<=512
                or not os.path.samestat(st,entry)):
            raise ValueError('Untrusted TEST clear profile')
        raw=handle.read(513)
        if len(raw)!=st.st_size or not os.path.samestat(st,path.lstat()):
            raise ValueError('Changed TEST clear profile')
    def unique(items):
        value=dict(items)
        if len(value)!=len(items):
            raise ValueError('Duplicate TEST clear profile field')
        return value
    value=json.loads(raw,object_pairs_hook=unique)
    if (type(value) is not dict or set(value)!={'version','stage','release_generation','database_scope'}
            or type(value['version']) is not int or value['version']!=1 or value['stage']!='test'
            or type(value['release_generation']) is not str
            or (value['database_scope'] is not None and type(value['database_scope']) is not str)):
        raise ValueError('Invalid TEST clear profile')
    scope=value['database_scope']
    if scope is not None and not re.fullmatch('[a-f0-9]{64}',scope):
        raise ValueError('Invalid TEST database scope')
    profile=value['release_generation']+':'+(scope if scope is not None else 'reusable')
    if not _TEST_CLEAR_PROFILE.fullmatch(profile):
        raise ValueError('Invalid TEST release generation')
    return profile

def installed_test_clear_profile():
    try:
        return read_test_clear_profile(_TEST_CLEAR_PROFILE_PATH)
    except (OSError,TypeError,ValueError):
        # Invalid/missing clear controls must never take owner login or reads down.
        logging.getLogger(__name__).warning('test_browser_clear_profile_invalid')
        return None

def configured_browser_api(dashboard, *, clear_profile=None, prod_owner_bridge=None):
    """A TEST-only profile always pins release and can bind one pre-clear epoch.

    Format: verified release generation plus exact epoch scope or reusable mode.
    The profile is an activation constraint, not proof of an owner session.
    """
    if dashboard.stage=='prod':
        if clear_profile is not None:
            raise ValueError('TEST clear profiles are forbidden on PROD')
        from .prod_owner_bridge import ProdOwnerBridge
        if type(prod_owner_bridge) is not ProdOwnerBridge:
            raise ValueError('PROD browser owner API requires the existing private Base authority')
        from prototypes.assertion_memory.dashboard_projection import release_generation

        def guarded(operation, *args, snapshot=False, **kwargs):
            # Keep the transition fence through the private request; Base's
            # separate release lease is nonblocking and acquired by Base itself.
            with ExitStack() as stack:
                try:
                    stack.enter_context(dashboard.read_fence())
                    revision, archive = dashboard.activation()
                except (OSError, ValueError, KeyError):
                    raise APIError('owner_api_unavailable','Graph reader authority unavailable',503) from None
                result = operation(*args, **kwargs)
                if snapshot and result['release_generation'] != release_generation(
                        revision, archive, result['release_version'], 'prod'):
                    raise APIError('owner_api_unavailable','Base release changed',503)
                return result

        def perform(action, args):
            return guarded(prod_owner_bridge.perform, action, args)

        def project(*, limit, offset):
            return guarded(prod_owner_bridge.queue_snapshot, limit=limit, offset=offset, snapshot=True)

        return BrowserOwnerAPI(stage='prod',perform=perform,
                               queue_snapshot=project,clear_enabled=False)
    if dashboard.stage!='test' or prod_owner_bridge is not None:
        raise ValueError('Invalid Graph owner API lane')
    if clear_profile is not None:
        match=_TEST_CLEAR_PROFILE.fullmatch(clear_profile) if type(clear_profile) is str else None
        if match is None:
            raise ValueError('Expected verified TEST clear release and database epoch profile')
        release_pin,scope_pin=match.groups()
        scope_pin=None if scope_pin=='reusable' else scope_pin
    clear_enabled=clear_profile is not None
    from prototypes.assertion_memory.queue_owner_domain import owner_operations
    from prototypes.assertion_memory.queue_rest import exact_database, snapshot as queue_snapshot
    def identity():
        config=dashboard.metadata()
        return config['release_version'], config['generation']
    def perform(action,args):
        # Graph login/static boot must not depend on the concurrent Base
        # publisher being present. Authoritative projection is pinned per request.
        config=dashboard.metadata()
        owner=owner_operations(database=dashboard.db,release_lease=dashboard.release_lease,
            identity=identity,stage=dashboard.stage,fresh_only=config['auto_process_fresh_mac'],
            baseline=config['processing_session_ids'])
        if action in ('clear_prepare','clear_commit'):
            if not clear_enabled or config['generation']!=release_pin:
                raise APIError('not_found','Unknown API resource',404)
            if action=='clear_commit':
                def active_clear(db):
                    # Recheck inside the SQLite write transaction with both reset
                    # leases held. A different epoch cannot reuse this profile.
                    epoch=(*exact_database(dashboard.db),database_clear.revision(db))
                    if ((scope_pin is not None and database_clear.generation_scope('test',epoch)!=scope_pin)
                            or identity()[1]!=release_pin):
                        raise processing_control.Conflict('TEST clear profile changed or consumed')
                return owner(action,args,authorize=active_clear)
            value=owner(action,args)
            if scope_pin is not None and value['scope']!=scope_pin:
                value={**value,'can_clear':False}
            return value
        return owner(action,args)
    def project(*, limit, offset):
        with dashboard.release_lease():
            current=dashboard.metadata()
            control=SimpleNamespace(processing_session_ids=current['processing_session_ids'],
                provider='configured' if current['model_configured'] else 'none',
                auto_process_fresh_mac=current['auto_process_fresh_mac'])
            value=queue_snapshot(control,identity=identity,database=dashboard.db,
                interactive_enabled=current['interactive_enabled'],limit=limit,offset=offset,
                lane=dashboard.stage,can_change=True,session_control=True,batch_cards=True,
                clear_ready=clear_enabled and current['generation']==release_pin)
            if 'database_clear' in value and scope_pin is not None and value['database_clear']['scope']!=scope_pin:
                value['database_clear']['can_clear']=False
            return value
    return BrowserOwnerAPI(stage=dashboard.stage,perform=perform,queue_snapshot=project,
        clear_enabled=clear_enabled)
