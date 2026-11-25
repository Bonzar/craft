"""Same-origin ASGI service, with authorization on every private route."""
import asyncio
from contextlib import asynccontextmanager
import re
import json
from pathlib import Path
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse,FileResponse,StreamingResponse,Response
from starlette.routing import Route
from .auth import OwnerStore,COOKIE,check_origin,check_host,secure_transport,validate_public_origin,validate_proxy_hosts
from .common import APIError,selector
from .bridge_auth import BridgeReadService,read_service_authorized
from .manager import GraphManager,manifest_url,parse_replay_cursor
from .providers.canonical_thoughts import source_for_path
from . import cards
from .search import search


class Boundary(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            path=request.url.path
            check_host(request)
            check_origin(request)
            public=path=='/api/v1/health' and request.method=='GET' or path=='/api/v1/session' and request.method in {'GET','POST'}
            if path.startswith('/api/') and not public:
                csrf=request.app.state.owner.session(request)
                if csrf is None and not read_service_authorized(request):
                    raise APIError('unauthorized','Owner session required',401)
                if request.method not in {'GET','HEAD','OPTIONS'}:
                    if csrf is None:
                        raise APIError('unauthorized','Owner session required',401)
                    check_origin(request,required=True)
                    if request.headers.get('x-csrf-token')!=csrf:
                        raise APIError('csrf_denied','Valid CSRF token required',403)
            response=await call_next(request)
        except APIError as error:
            response=error_response(error)
        response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
                                 'Referrer-Policy':'no-referrer','Permissions-Policy':'camera=(), microphone=(), geolocation=()',
                                 'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-src 'none'; base-uri 'self'; frame-ancestors 'none'"})
        if secure_transport(request):
            response.headers['Strict-Transport-Security']='max-age=31536000'
        return response


def error_response(error):
    return JSONResponse({'error':{'code':error.code,'message':error.message}},status_code=error.status)


async def body(request):
    length=request.headers.get('content-length')
    if length is not None:
        try:
            if int(length)<0:
                raise ValueError()
            if int(length)>20000:
                raise APIError('body_too_large','Request body is too large',413)
        except ValueError:
            raise APIError('invalid_length','Invalid request body length') from None
    chunks=[]; total=0
    async for chunk in request.stream():
        total+=len(chunk)
        if total>20000:
            raise APIError('body_too_large','Request body is too large',413)
        chunks.append(chunk)
    raw=b''.join(chunks)
    if len(raw)>20000:
        raise APIError('body_too_large','Request body is too large',413)
    try:
        value=json.loads(raw)
        if not isinstance(value,dict):
            raise ValueError()
        return value
    except (ValueError,UnicodeError):
        raise APIError('invalid_json','Expected a JSON object') from None


async def session(request):
    owner=request.app.state.owner
    if request.method=='GET':
        csrf=owner.session(request)
        return JSONResponse({'authenticated':bool(csrf),**({'csrf_token':csrf} if csrf else {})})
    if request.method=='DELETE':
        owner.revoke(request); response=JSONResponse({'authenticated':False})
        response.delete_cookie(COOKIE,path='/api/v1')
        return response
    data=await body(request)
    token,csrf=await asyncio.to_thread(owner.login,data.get('password'),request.client.host if request.client else 'local')
    response=JSONResponse({'owner':True,'csrf_token':csrf})
    response.set_cookie(COOKIE,token,max_age=86400*14,httponly=True,samesite='strict',secure=secure_transport(request),path='/api/v1')
    return response


async def graph_manifest(request):
    graph=await request.app.state.manager.ensure(selector(request.query_params))
    if graph is None:
        return JSONResponse({'status':'preparing','retry_after_ms':300,'message':'Preparing complete graph and search index'},status_code=202,headers={'Retry-After':'1'})
    return JSONResponse(graph.manifest,headers={'ETag':'"'+graph.revision+'"'})


def resolve_graph(request):
    revision=request.path_params.get('revision') or request.query_params.get('revision')
    if not revision:
        raise APIError('revision_required','An immutable graph revision is required')
    return request.app.state.manager.get(revision)


async def graph_file(request):
    graph=resolve_graph(request); name=request.path_params['name']
    if name=='topics':
        return JSONResponse(graph.topics())
    if name=='sessions':
        from .sessions import session_page
        return JSONResponse(await asyncio.to_thread(session_page,graph,request.query_params))
    if name not in {'nodes.bin','edges.bin'}:
        raise APIError('not_found','Unknown graph resource',404)
    from .transport import accepts_gzip
    compressed=accepts_gzip(request.headers.get('accept-encoding',''))
    headers={'ETag':'"'+graph.revision+'-'+name+'"','Vary':'Accept-Encoding','Content-Encoding':'gzip' if compressed else 'identity'}
    return FileResponse(graph.directory/(name+'.gz' if compressed else name),media_type='application/octet-stream',headers=headers)


def card_response(request):
    graph=resolve_graph(request); params=request.path_params; action=params.get('action')
    with graph.connect() as c:
        if params['kind']=='nodes':
            row=cards.node_row(c,id=params.get('id'),ordinal=params.get('ordinal'))
            result=cards.incident(c,graph,row,request.query_params) if action=='edges' else cards.node_card(c,graph,row,request.query_params)
        elif params['kind']=='edges':
            result=cards.edge_card(c,graph,id=params.get('id'),ordinal=params.get('ordinal'))
        elif params['kind']=='overviews':
            result=cards.overview_page(c,graph,params['id'],request.query_params)
        else:
            result=cards.source_page(c,graph,params['id'],request.query_params)
    return JSONResponse(result)


async def card(request):
    return await asyncio.to_thread(card_response,request)


async def search_response(request):
    return JSONResponse(await asyncio.to_thread(search,resolve_graph(request),request.query_params))


async def bookmark(request):
    owner=request.app.state.owner
    if request.method in {'GET','HEAD'}:
        from .bookmarks import resolve_bookmarks
        manager=request.app.state.manager
        return JSONResponse(await resolve_bookmarks(manager,owner.bookmarks(manager.source.provider_id)))
    payload=await body(request) if request.method=='PUT' else None
    return JSONResponse(owner.bookmark(request.path_params['id'],payload))


async def events(request):
    manager=request.app.state.manager
    if 'channel' in request.query_params:
        if list(request.query_params.multi_items()) != [('channel','dashboard')]:
            raise APIError('invalid_dashboard','Unknown event channel')
        dashboard=request.app.state.queue_dashboard
        if dashboard is None or dashboard.streams.locked():
            raise APIError('queue_unavailable','Dashboard stream is unavailable',503)
        if request.method=='HEAD':
            return Response(media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
        from .queue_dashboard import dashboard_events
        return StreamingResponse(dashboard_events(request,manager,dashboard),
                                 media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    after=parse_replay_cursor(request)
    revision=request.query_params.get('revision')
    graph=manager.get(revision) if revision else None
    selected=graph.manifest['selector'] if graph is not None else None
    missing_family=selected is not None and manager.key(selected) not in manager.latest
    if selected is not None and selected['mode']=='known_then' and missing_family:
        manager.latest[manager.key(selected)]=graph.revision
    if after>manager.cursor or (missing_family and selected['mode']!='known_then'):
        payload={'cursor':str(manager.cursor),'revision':revision,'reason':'cursor_expired',
                 'manifest_url':manifest_url(selected) if selected is not None else '/api/v1/graph'}
        return Response(manager.encode('reset',payload),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
    if request.method=='HEAD':
        return Response(media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
    return StreamingResponse(manager.events(request),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})


async def capabilities(request):
    if 'queue_status' in request.query_params:
        q = request.query_params
        if (set(q)-{'queue_status','limit','offset'} or any(len(q.getlist(k)) != 1 for k in q)
                or q['queue_status'] != '1'):
            raise APIError('invalid_dashboard','Invalid queue status request')
        values = (q.get('limit','50'),q.get('offset','0'))
        if not all(re.fullmatch(r'0|[1-9][0-9]{0,6}',v) for v in values):
            raise APIError('invalid_dashboard','Invalid queue status page')
        limit, offset = map(int,values)
        if not 1<=limit<=100 or offset>1_000_000:
            raise APIError('invalid_dashboard','Invalid queue status page')
        dashboard = request.app.state.queue_dashboard
        if dashboard is None:
            raise APIError('queue_unavailable','Queue status is not configured',503)
        try:
            status = await asyncio.to_thread(dashboard.snapshot,limit=limit,offset=offset)
            # Published native Sites treats all capabilities responses alike:
            # these fixed fields are required before it applies transport policy.
            return JSONResponse({**status,'capabilities':{'live':True},'readiness':[]})
        except Exception:
            raise APIError('queue_unavailable','Queue status is temporarily unavailable',503) from None
    value=request.app.state.manager.source.capabilities()
    value['capabilities']['session_filter']=True
    if any(hasattr(g,'semantic_matrix') for g in request.app.state.manager.graphs.values()):
        value['capabilities']['semantic_search']=True
        value['readiness']=[r for r in value['readiness'] if r['code']!='semantic_unavailable']
    if request.app.state.synthetic_fixture:
        value['readiness'].append({'code':'synthetic_fixture','message':'Synthetic QA fixture; no personal graph connected'})
    value['live_profile']=list(request.app.state.manager.live_profiles)
    value['preparation']={'pending':len(request.app.state.manager.preparing),'ready_snapshots':len(request.app.state.manager.graphs),'semantic_pending':len(request.app.state.manager.semantic_jobs)}
    return JSONResponse(value)


async def static(request):
    root=request.app.state.static_dir
    if root is None:
        return Response('Build the viewer frontend first',status_code=503)
    target=(root/request.path_params.get('path','')).resolve()
    if not target.is_relative_to(root):
        raise APIError('not_found','File unavailable',404)
    if not target.is_file():
        target=(root/'index.html').resolve()
        if not target.is_relative_to(root):
            raise APIError('not_found','File unavailable',404)
    return FileResponse(target) if target.is_file() else Response('Frontend build unavailable',status_code=503)


def validate_static_directory(static_dir, *private_paths):
    if static_dir is None:
        return None
    root=Path(static_dir).resolve()
    for value in private_paths:
        if value is None:
            continue
        private=Path(value).resolve()
        if root.is_relative_to(private) or private.is_relative_to(root):
            raise ValueError('Frontend directory overlaps a private path')
    return root


def create_app(source_path, state_dir, *, password=None, provider_id='assertion-memory', static_dir=None, source=None, semantic_model=None, synthetic_fixture=False, public_origin=None, bridge_read_token_file=None, allowed_proxy_hosts=(), queue_dashboard=None):
    if queue_dashboard is None:
        from .queue_dashboard import configured_for_legacy_reader
        queue_dashboard = configured_for_legacy_reader(source_path)
    static_dir=validate_static_directory(
        static_dir,state_dir,source_path,bridge_read_token_file,getattr(queue_dashboard,'config',None),
        getattr(source,'source_path',None),getattr(source,'path',None))
    bridge_read_service=BridgeReadService(bridge_read_token_file)
    owner=OwnerStore(state_dir,password)
    manager=GraphManager(source or source_for_path(source_path,state_dir,provider_id),Path(state_dir)/'cache',semantic_model)
    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        dashboard_task = (asyncio.create_task(queue_dashboard.watch(manager.emit))
                          if queue_dashboard is not None else None)
        try:
            yield
        finally:
            if dashboard_task is not None:
                dashboard_task.cancel()
                await asyncio.gather(dashboard_task,return_exceptions=True)
            await manager.stop()
    async def handle_error(request,error):
        return error_response(error)
    async def unavailable(request,error):
        import logging
        logging.getLogger(__name__).warning('request_failed route=%s error_type=%s',request.scope.get('route').path if request.scope.get('route') else 'unmatched',type(error).__name__)
        return error_response(APIError('service_unavailable','Requested data is temporarily unavailable',503))
    routes=make_routes()
    app=Starlette(routes=routes,lifespan=lifespan,exception_handlers={APIError:handle_error,Exception:unavailable},
                  middleware=[Middleware(Boundary),Middleware(GZipMiddleware,minimum_size=1500)])
    app.state.public_origin=validate_public_origin(public_origin)
    app.state.allowed_proxy_hosts=validate_proxy_hosts(allowed_proxy_hosts)
    app.state.bridge_read_service=bridge_read_service
    app.state.synthetic_fixture=synthetic_fixture
    app.state.owner=owner; app.state.manager=manager
    app.state.queue_dashboard=queue_dashboard
    app.state.static_dir=static_dir
    return app


def make_routes():
    prefix='/api/v1'
    routes=[Route(prefix+'/health',lambda request:JSONResponse({'ok':True})),
            Route(prefix+'/session',session,methods=['GET','POST','DELETE']),
            Route(prefix+'/capabilities',capabilities),Route(prefix+'/graph',graph_manifest),
            Route(prefix+'/graph/{revision}/{kind}/{ordinal:int}/{action}',card),
            Route(prefix+'/graph/{revision}/{kind}/{ordinal:int}',card),
            Route(prefix+'/graph/{revision}/{name}',graph_file),
            Route(prefix+'/nodes/{id:path}/edges',stable_card('nodes','edges'))]
    routes.extend(Route(prefix+'/'+kind+'/{id:path}',stable_card(kind)) for kind in ['nodes','edges','sources','overviews'])
    routes.extend([Route(prefix+'/search',search_response),Route(prefix+'/events',events),
                   Route(prefix+'/bookmarks',bookmark),Route(prefix+'/bookmarks/{id}',bookmark,methods=['PUT','DELETE']),
                   Route('/api/{path:path}',unknown_api,methods=['GET','POST','PUT','DELETE','PATCH']),Route('/{path:path}',static)])
    return routes


def stable_card(kind,action=None):
    async def endpoint(request):
        request.path_params['kind']=kind
        selected=action
        # ASGI decodes %2F before routing. A percent-encoded trailing /edges is
        # part of the ID; only a literal trailing /edges requests adjacency.
        if selected=='edges' and not request.scope.get('raw_path',b'').endswith(b'/edges'):
            request.path_params['id']+='/edges'; selected=None
        if selected:
            request.path_params['action']=selected
        return await card(request)
    return endpoint


async def unknown_api(request):
    raise APIError('not_found','Unknown API resource',404)
