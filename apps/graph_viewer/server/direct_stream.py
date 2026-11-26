"""Off-by-default TEST owner–authorized metadata-only direct SSE."""
from dataclasses import dataclass
import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
from starlette.responses import JSONResponse, Response, StreamingResponse
from .auth import COOKIE
from .common import APIError

TICKET_PATH='/api/v1/events-ticket'
DIRECT_PATH='/api/v1/events-direct'
TEST_EDGE_PATH='/__mvt/api/v1/events-direct'
TOKEN=re.compile('[A-Za-z0-9_-]{43}\\Z')
MAX_TICKETS=64
MAX_ACTIVE=8
TICKET_SECONDS=15
LIVE_SECONDS=90
HEARTBEAT_SECONDS=5

def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()

@dataclass(frozen=True)
class Binding:
    public_origin:str
    direct_origin:str
    @classmethod
    def test(cls, public_origin, direct_origin, synthetic_fixture):
        if synthetic_fixture is not True or not public_origin:
            raise ValueError('direct events require explicit synthetic TEST and owner ingress')
        target=urlsplit(direct_origin)
        if (target.scheme!='https' or target.username or target.password or target.port is not None
            or target.path or target.query or target.fragment):
            raise ValueError('direct stream must use the fixed HTTPS IP origin')
        try:
            ip=ipaddress.ip_address(target.hostname)
        except (TypeError,ValueError):
            raise ValueError('direct stream target must be the existing public IP origin') from None
        if not ip.is_global or ip.version!=4 or direct_origin!='https://'+str(ip):
            raise ValueError('direct stream target must be a canonical public IPv4 HTTPS origin')
        return cls(public_origin,direct_origin)

    def permits(self, request):
        return request.headers.getlist('origin')==[self.public_origin]
    def cors(self):
        return {'Access-Control-Allow-Origin':self.public_origin,'Vary':'Origin','Cache-Control':'private, no-store'}

def active_test_scope(source):
    """Independently pin the physical configured TEST account, DB and code."""
    from .queue_dashboard import SCOPES,_active_reader_release
    user,expected=SCOPES['test']
    source=Path(source)
    if source!=expected or source.resolve(strict=True)!=expected:
        raise ValueError('Direct events require the exact active TEST runtime')
    uid=pwd.getpwnam(user).pw_uid
    if os.getuid()!=uid or os.geteuid()!=uid:
        raise ValueError('Direct events require the exact active TEST runtime')
    return _active_reader_release('test')


def configure_test(public_origin,direct_origin,synthetic_fixture,source):
    binding=Binding.test(public_origin,direct_origin,synthetic_fixture)
    release=active_test_scope(source)
    def current():
        try:
            return active_test_scope(source)==release
        except (OSError,ValueError,KeyError):
            return False
    return Tickets(binding,current=current)


class Tickets:
    """Only digests in memory; a valid ticket can start exactly one TEST stream."""
    def __init__(self,binding,clock=time.monotonic,*,current=lambda:False):
        self.binding=binding
        self.clock=clock
        self.current=current
        self.lock=threading.Lock()
        self.unused={}
        self.active={}
    def issue(self,owner_session,generation=None):
        now=self.clock()
        with self.lock:
            self.unused={key:item for key,item in self.unused.items() if item[1]>now}
            # Keep only the most recent unused ticket for one owner browser session.
            self.unused={key:item for key,item in self.unused.items() if item[0]!=owner_session}
            if len(self.unused)>=MAX_TICKETS:
                raise APIError('rate_limited','Try opening the stream later',429)
            raw=secrets.token_urlsafe(32)
            self.unused[digest(raw)]=(owner_session,now+TICKET_SECONDS,generation)
        return raw

    def consume(self,value):
        if not isinstance(value,str) or not TOKEN.fullmatch(value):
            return None
        key=digest(value)
        with self.lock:
            now=self.clock()
            self.active={k:v for k,v in self.active.items() if v[1]>now}
            item=self.unused.pop(key,None)
            if item is None or item[1]<=now:
                return None
            if len(self.active)>=MAX_ACTIVE or any(v[0]==item[0] for v in self.active.values()):
                raise APIError('rate_limited','An active stream for this session is already open',429)
            self.active[key]=(item[0],now+LIVE_SECONDS+5)
            return key,item[0],item[2]
    def close(self,key):
        with self.lock:
            self.active.pop(key,None)

async def issue_ticket(request):
    scope=request.app.state.direct_events
    if scope is None or not scope.current():
        raise APIError('not_found','Direct events are not enabled',404)
    tokens=request.headers.getlist('x-csrf-token')
    if not scope.binding.permits(request) or len(tokens)!=1 or not TOKEN.fullmatch(tokens[0]):
        raise APIError('origin_denied','Exact owner origin and CSRF required',403)
    if (request.url.query or request.headers.getlist('content-length') not in ([],['0'])
        or request.headers.getlist('transfer-encoding')):
        raise APIError('invalid_request','No selection or request body is accepted',400)
    try:
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if chunk:
                    raise APIError('invalid_request','No request body is accepted',400)
    except TimeoutError:
        raise APIError('invalid_request','No request body is accepted',400) from None
    try:
        session=digest(request.cookies.get(COOKIE,''))
    except (TypeError,UnicodeError):
        raise APIError('unauthorized','Owner session required',401) from None
    owner=request.app.state.owner
    csrf=await asyncio.to_thread(owner.session_digest,session)
    if csrf is None or not hmac.compare_digest(csrf,tokens[0]):
        raise APIError('unauthorized','Owner session required',401)
    generation=request.app.state.manager.sync_generation()
    if generation is None or not scope.current():
        raise APIError('not_found','Direct events are not enabled',404)
    return JSONResponse({'ticket':scope.issue(session,generation),'expires_in':TICKET_SECONDS,
                         'stream_url':scope.binding.direct_origin+TEST_EDGE_PATH},
                        headers={'Cache-Control':'private, no-store','Pragma':'no-cache'})

async def direct_events(request):
    scope=request.app.state.direct_events
    if scope is None or not scope.current():
        raise APIError('not_found','Direct events are not enabled',404)
    binding=scope.binding
    if not binding.permits(request) or request.headers.getlist('cookie') or request.url.query:
        raise APIError('origin_denied','Exact TEST stream origin required',403)
    cors=binding.cors()
    if request.method=='OPTIONS':
        if (request.headers.getlist('access-control-request-method')!=['GET']
            or [value.lower() for value in request.headers.getlist('access-control-request-headers')]!=['authorization']):
            raise APIError('origin_denied','Exact stream preflight required',403)
        return Response(status_code=204,headers={**cors,'Access-Control-Allow-Methods':'GET',
                        'Access-Control-Allow-Headers':'Authorization','Access-Control-Max-Age':'0'})
    auth=request.headers.getlist('authorization')
    if request.method!='GET' or len(auth)!=1 or not auth[0].startswith('Bearer '):
        return Response(status_code=401,headers=cors)
    try:
        lease=scope.consume(auth[0][7:])
    except APIError as error:
        return Response(status_code=error.status,headers=cors)
    if lease is None:
        return Response(status_code=401,headers=cors)
    key,session,generation=lease
    owner=request.app.state.owner
    manager=request.app.state.manager
    try:
        if not await asyncio.to_thread(owner.session_digest,session) or not scope.current():
            scope.close(key)
            return Response(status_code=401,headers=cors)
        if generation is None or manager.sync_generation()!=generation:
            scope.close(key)
            return Response(status_code=409,headers=cors)
    except BaseException:
        scope.close(key)
        raise
    # No Graph payload, revision or user references ever cross this path.
    def frame(kind):
        return f'event: {kind}\ndata: {{}}\n\n'
    async def content():
        start=ping=time.monotonic()
        cursor=manager.cursor
        try:
            if not scope.current() or manager.sync_generation()!=generation:
                return
            yield frame('hello')
            yield frame('reset') # After reconnect, reload the same current selector separately.
            while not manager.closed and time.monotonic()-start<LIVE_SECONDS:
                if (await request.is_disconnected() or not await asyncio.to_thread(owner.session_digest,session)
                    or not scope.current()):
                    return
                if manager.sync_generation()!=generation:
                    yield frame('reset')
                    return
                unseen=[(number,kind) for number,kind,_ in list(manager.journal) if number>cursor]
                if unseen:
                    if unseen[0][0]>cursor+1 or any(kind=='reset' for _,kind in unseen):
                        yield frame('reset')
                    elif any(kind=='delta' for _,kind in unseen):
                        yield frame('changed')
                    cursor=unseen[-1][0]
                if time.monotonic()-ping>=HEARTBEAT_SECONDS:
                    yield frame('heartbeat')
                    ping=time.monotonic()
                await asyncio.sleep(.4)
        except APIError:
            # A missing or unverified generation must end the current ticket.
            return
        finally:
            scope.close(key)
    return StreamingResponse(content(),media_type='text/event-stream',
                             headers={**cors,'X-Accel-Buffering':'no'})
