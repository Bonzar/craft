"""Private Streamable HTTP MCP endpoint; TLS belongs to the tailnet proxy.

The listener is always loopback-only. A bearer key is still required when a
device is already on the tailnet. Database/provider settings are server-owned.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import stat
import re
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from .mcp_server import ServiceConfig, WorkerLauncher, create_server
from .storage import MemoryStore

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
    def __init__(self, app, token, neutral=None):
        self.app = app
        self.neutral = neutral
        self.expected = ('Bearer ' + token).encode('ascii')

    @asynccontextmanager
    async def lifespan(self):
        async with self.app.router.lifespan_context(self.app):
            yield

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            path=scope.get('path','')
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
                    additional_hosts=(), neutral_factory=None):
    from mcp.server.transport_security import TransportSecuritySettings
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
                and routes <= {f'/memory-mcp-{lane}-tools',f'/memory-mcp-{lane}-effects',f'/memory-api-{lane}'}
                for lane in ('test','prod')):
                raise ValueError('Invalid neutral path binding')
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
            if background:
                WorkerLauncher(config).start()
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(inner.router.lifespan_context(inner))
                if background:
                    from .processing_control import restart_on_resume
                    await stack.enter_async_context(restart_on_resume(config.db,WorkerLauncher(config).start))
                if background and config.auto_process_fresh_mac:
                    from .fresh_processing import reconcile_fresh_mac
                    await stack.enter_async_context(reconcile_fresh_mac(WorkerLauncher(config).start))
                if neutral is not None and (jobs := getattr(neutral[1], 'query_jobs', None)) is not None:
                    await stack.enter_async_context(jobs.lifespan())
                if neutral is not None and (projection := getattr(neutral[1], 'queue_dashboard_projection', None)) is not None:
                    stack.enter_context(projection())
                yield
        finally:
            store.close()

    from starlette.applications import Starlette
    from starlette.routing import Mount
    app = Starlette(routes=[Mount('/',app=inner)],lifespan=lifespan)
    app.router.redirect_slashes=False
    return AuthenticatedMemoryApp(app,token,neutral)


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
    app = create_http_app(ServiceConfig(**value['service']), token, value['public_host'], port=port,
                          additional_hosts=value.get('additional_hosts', ()), neutral_factory=neutral_factory)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
