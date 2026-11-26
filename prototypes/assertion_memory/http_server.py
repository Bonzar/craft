"""Private Streamable HTTP MCP endpoint; TLS belongs to the tailnet proxy.

The listener is always loopback-only. A bearer key is still required when a
device is already on the tailnet. Database/provider settings are server-owned.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import stat
import re
import uuid
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from pathlib import Path

from .mcp_server import ServiceConfig, WorkerLauncher, create_server
from .storage import MemoryStore
from .queue_rest import PATH as QUEUE_REST_PATH
from .queue_pause_rest import PATH as QUEUE_PAUSE_PATH
from .queue_session_control import PATH as QUEUE_SESSIONS_PATH
from .queue_processing_policy import PATH as QUEUE_POLICY_PATH
from .queue_item_removal import PATH as QUEUE_REMOVE_PATH, PREVIEW_PATH as QUEUE_PREVIEW_PATH

from .database_clear import PATH as QUEUE_CLEAR_PATH, database_lease
from .graph_node_removal import PATH as GRAPH_REMOVE, PREVIEW_PATH as GRAPH_REMOVE_PREVIEW

MIN_TOKEN_LENGTH = 32
TOKEN_ENV_VAR = 'ASSERTION_MEMORY_TOKEN'
PUBLIC_PREFIX = '/memory'
NO_STORE = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}


def load_token(path: Path | None = None, *, env_var: str | None = None) -> str:
    if (path is None) == (env_var is None):
        raise ValueError('configure exactly one host token source')
    if env_var is not None:
        if not isinstance(env_var,str) or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',env_var) is None:
            raise ValueError('invalid host token environment variable name')
        value=os.environ.get(env_var,'')
        try:is_uuid=str(uuid.UUID(value))==value
        except ValueError:is_uuid=False
        if not is_uuid:raise ValueError('host token environment must contain a canonical UUID')
        return value
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, encoding='utf-8') as stream:
        mode = os.fstat(stream.fileno())
        if not stat.S_ISREG(mode.st_mode) or mode.st_mode & 0o077:
            raise ValueError('bearer key must be a private regular file (mode 0600)')
        if mode.st_uid != os.getuid():
            raise ValueError('bearer key must belong to the service user')
        value = stream.read(4097).strip()
    if not MIN_TOKEN_LENGTH <= len(value) <= 4096 or not value.isascii() or any(x.isspace() for x in value):
        raise ValueError('bearer key must contain 32..4096 non-whitespace ASCII characters')
    return value


class AuthenticatedMemoryApp:
    def __init__(self, app, token, neutral=None, queue_rest=None, queue_pause=None):
        self.app = app
        self.neutral = neutral
        self.queue_rest = queue_rest
        self.queue_pause = queue_pause
        self.expected = ('Bearer ' + token).encode('ascii')

    @asynccontextmanager
    async def lifespan(self):
        async with self.app.router.lifespan_context(self.app):
            yield

    async def __call__(self, scope, receive, send):
        if scope['type']=='http':
            path=scope.get('path','')
            if path==QUEUE_CLEAR_PATH:
                handler=getattr(self.queue_pause,'queue_clear_route',None)
                if handler is None:
                    return await _error_response(404,'not found',scope,receive,send)
                return await handler(scope,receive,send)
            database=getattr(self.queue_pause,'queue_clear_database',None)
            # MCP GET may be a stream without a bound lifetime. Provider and
            # mutations start only via POST/PUT; GET owner queue reads are finite.
            if database is not None and (scope.get('method')!='GET' or path.startswith('/api/')):
                # Keep the lease until the actual dispatcher exits, even if
                # an HTTP caller disconnects while a model thread is active.
                task=asyncio.create_task(self._leased_dispatch(database,scope,receive,send))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    task.add_done_callback(lambda done:None if done.cancelled() else done.exception())
                    raise
        return await self._dispatch(scope,receive,send)

    async def _leased_dispatch(self,database,scope,receive,send):
        with ExitStack() as stack:
            try:
                stack.enter_context(database_lease(database))
            except OSError:
                return await _error_response(503,'database reset in progress',scope,receive,send)
            return await self._dispatch(scope,receive,send)

    async def _dispatch(self, scope, receive, send):
        if scope['type'] == 'http':
            path=scope.get('path','')
            if path in (QUEUE_REMOVE_PATH,QUEUE_PREVIEW_PATH,GRAPH_REMOVE,GRAPH_REMOVE_PREVIEW):
                handler=getattr(self.queue_pause,'queue_item_routes',{}).get(path)
                if handler is None:
                    return await _error_response(404,'not found',scope,receive,send)
                return await handler(scope,receive,send)
            if path==QUEUE_POLICY_PATH:
                handler=getattr(self.queue_pause,'queue_policy_route',None)
                if handler is None:
                    return await _error_response(404,'not found',scope,receive,send)
                return await handler(scope,receive,send)
            if path==QUEUE_SESSIONS_PATH:
                handler=getattr(self.queue_pause,'queue_session_route',None)
                if handler is None:
                    return await _error_response(404,'not found',scope,receive,send)
                return await handler(scope,receive,send)
            if path == QUEUE_PAUSE_PATH:
                if self.queue_pause is None:
                    return await _error_response(404,'not found',scope,receive,send)
                return await self.queue_pause(scope,receive,send)
            if path==QUEUE_REST_PATH:
                if self.queue_rest is None:
                    return await _error_response(404,'not found',scope,receive,send)
                return await self.queue_rest(scope,receive,send)
            if self.neutral is not None and path in self.neutral[0]:
                return await self.neutral[1](scope, receive, send)
            if path==PUBLIC_PREFIX or path.startswith(PUBLIC_PREFIX+'/'):
                scope={**scope,'path':path[len(PUBLIC_PREFIX):] or '/'}
                raw=scope.get('raw_path',b'')
                if raw.startswith(PUBLIC_PREFIX.encode()):scope['raw_path']=raw[len(PUBLIC_PREFIX):] or b'/'
            supplied = [value for key, value in scope.get('headers', []) if key.lower() == b'authorization']
            if len(supplied)!=1 or not hmac.compare_digest(supplied[0],self.expected):
                return await _error_response(401,'authentication required',scope,receive,send)

        await self.app(scope, receive, send)


async def _error_response(status,code,scope,receive,send):
    from starlette.responses import JSONResponse
    headers={**NO_STORE}
    if status==401:headers['WWW-Authenticate']='Bearer realm="assertion-memory"'
    await JSONResponse({'error':code},status_code=status,headers=headers)(scope,receive,send)


def create_http_app(config: ServiceConfig, token: str, public_host: str, *, port=8793, background=True,
                    additional_hosts=(), neutral_factory=None, queue_rest_factory=None, queue_pause_factory=None,
                    browser_lifespan=None):
    from mcp.server.transport_security import TransportSecuritySettings
    if browser_lifespan is not None and not callable(browser_lifespan):
        raise ValueError('Invalid browser-only lifespan')
    if (not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH
            or not token.isascii() or any(x.isspace() for x in token)):
        raise ValueError('a private bearer key of at least 32 ASCII characters is required')
    if (not isinstance(public_host, str) or not public_host
            or any(x in public_host for x in ('/', '@', '*', ' ', '\n', '\r'))):
        raise ValueError('public_host must be an exact hostname with optional port')
    if not isinstance(additional_hosts, (list, tuple)) or len(additional_hosts) > 8:
        raise ValueError('additional_hosts must contain at most eight exact hosts')
    if any(not isinstance(host, str) or not host or len(host) > 512
           or any(x in host for x in ('/', '@', '*', ' ', '\n', '\r')) for host in additional_hosts):
        raise ValueError('additional_hosts requires exact hostnames with optional ports')
    public_hosts = list(dict.fromkeys([public_host, *additional_hosts]))
    allowed_hosts=[*public_hosts,f'127.0.0.1:{port}',f'localhost:{port}']
    # Stateless MCP opens a protocol lifespan per request. The HTTP process,
    # rather than each request, owns the shared connection and worker launch.
    store = MemoryStore(config.db)
    try:
        service = config.service(store) if neutral_factory is not None else None
        server = create_server(config, background=background, store=store, start_on_session=False,
                               service=service)
        neutral = (neutral_factory(config, store, service, background)
                   if neutral_factory is not None else None)
        if config.auto_process_fresh_mac and (neutral is None or '/memory-api-prod' not in neutral[0]):
            raise ValueError('fresh Mac automatic processing requires the production machine route')
        if neutral is not None:
            routes, handler = neutral
            if not isinstance(routes, frozenset) or not callable(handler) or not any(
                f'/memory-mcp-{lane}-tools' in routes
                and routes <= ({f'/memory-mcp-{lane}-tools',f'/memory-mcp-{lane}-effects',f'/memory-api-{lane}'}
                               | ({'/memory-visual-test-pause'} if lane=='test' else set()))
                for lane in ('test','prod')):
                raise ValueError('Invalid neutral path binding')
        queue_pause=queue_pause_factory(config) if queue_pause_factory is not None else None
        controller=queue_pause or (neutral[1] if neutral is not None else None)
        interactive=neutral is not None and getattr(neutral[1],'query_jobs',None) is not None
        if queue_rest_factory is None:
            queue_rest=None
        elif getattr(controller,'queue_pause_ready',None) is None:
            queue_rest=queue_rest_factory(config,interactive)
        else:
            queue_rest=queue_rest_factory(config,interactive,can_change=getattr(controller,'queue_pause_ready',False),
                **({'session_control':True} if getattr(controller,'queue_session_route',None) is not None else {}))
    except BaseException:
        store.close()
        raise
    server.settings.stateless_http = True
    server.settings.json_response = True
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=['https://' + host for host in public_hosts])
    inner = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            async with AsyncExitStack() as stack:
                if background:
                    WorkerLauncher(config).start()
                await stack.enter_async_context(inner.router.lifespan_context(inner))
                if background:
                    from .processing_control import restart_on_resume
                    await stack.enter_async_context(restart_on_resume(config.db,WorkerLauncher(config).start))
                    if (session_watch:=getattr(controller,'queue_session_lifespan',None)) is not None:
                        await stack.enter_async_context(session_watch())
                if background and neutral is not None and any(
                        route.startswith('/memory-api-') for route in neutral[0]):
                    from .fresh_processing import reconcile_processing
                    from .mcp_server import _eligible_processing_work
                    launcher=WorkerLauncher(config)
                    def reconcile():
                        # A lost batch wake must not periodically wake unselected legacy work.
                        if config.auto_process_fresh_mac or (config.provider != 'none'
                                and _eligible_processing_work(config,batches_only=True)):
                            launcher.start()
                    await stack.enter_async_context(reconcile_processing(reconcile))
                if neutral is not None and (jobs := getattr(neutral[1], 'query_jobs', None)) is not None:
                    await stack.enter_async_context(jobs.lifespan())
                if neutral is not None and (projection := getattr(neutral[1], 'queue_dashboard_projection', None)) is not None:
                    stack.enter_context(projection())
                # Browser depends on the writer; enter after dependencies
                # become ready and leave before they begin unwinding.
                if browser_lifespan is not None:
                    await stack.enter_async_context(browser_lifespan(interactive_active=interactive))
                yield
        finally:
            store.close()

    from starlette.applications import Starlette
    from starlette.routing import Mount
    app = Starlette(routes=[Mount('/',app=inner)],lifespan=lifespan)
    app.router.redirect_slashes=False
    return AuthenticatedMemoryApp(app,token,neutral,queue_rest,queue_pause)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    value = json.loads(args.config.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) - {'service', 'token_file', 'token_env_var', 'public_host', 'port', 'additional_hosts'}:
        parser.error('config requires service, public_host and one host token source')
    port = value.get('port', 8793)
    if type(port) is not int or not 1024 <= port <= 65535:
        parser.error('port must be between 1024 and 65535')
    token_file=value.get('token_file')
    token=load_token(Path(token_file) if token_file else None,
        env_var=value.get('token_env_var',TOKEN_ENV_VAR if not token_file else None))
    from .private_http_binding import configured_neutral_factory
    neutral_factory = configured_neutral_factory(args.config, token)
    from .queue_rest import configured_queue_rest
    queue_rest_factory=configured_queue_rest(args.config,port,token)
    from .queue_pause_binding import configured_queue_pause
    queue_pause_factory=configured_queue_pause(args.config,port,token)
    config=ServiceConfig(**value['service'])
    from .browser_prod_binding import installed_prod_browser_lifespan
    parent=None
    side_failed=False
    def failed():
        nonlocal side_failed
        side_failed=True
        if parent is not None:
            parent.should_exit=True
    browser=installed_prod_browser_lifespan(args.config,config,private_port=port,on_failure=failed)
    app = create_http_app(config, token, value['public_host'], port=port,
                          additional_hosts=value.get('additional_hosts', ()), neutral_factory=neutral_factory,
                          queue_rest_factory=queue_rest_factory, queue_pause_factory=queue_pause_factory,
                          browser_lifespan=browser)
    import uvicorn
    if browser is None:
        uvicorn.run(app, host='127.0.0.1', port=port, access_log=False, log_level='warning')
    else:
        parent=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,
                                  access_log=False,log_level='warning',lifespan='on',workers=1))
        parent.run()
        if side_failed or not parent.started:
            raise RuntimeError('Base browser listener failed')


if __name__ == '__main__':
    main()


