"""Prototype: run a separate browser-only ASGI service in the Base process."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import socket

import uvicorn

_POLL = 0.025
_START = 8.0
_STOP = 3.0

class _SideServer(uvicorn.Server):
    # Only the main Base server owns operating-system signal registration.
    @contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self):
        # Older available uvicorn entrypoint uses this method instead.
        pass

def _prepare_socket(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', port))
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise

def _prepare_server(app):
    return _SideServer(uvicorn.Config(
        app, host='127.0.0.1', lifespan='on', proxy_headers=False,
        access_log=False, log_level='warning', workers=1, timeout_graceful_shutdown=2,
    ))

@asynccontextmanager
async def browser_listener(app, port, *, _bind=_prepare_socket, _server=_prepare_server):
    """Own socket and child lifecycle; trusted config must pin the actual port."""
    if not callable(app) or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('invalid Base browser listener binding')
    with _bind(port) as listener:
        server = _server(app)
        stopping = False

        async def run():
            await server.serve(sockets=[listener])
            if not stopping:
                raise RuntimeError('Base browser listener stopped before normal shutdown')

        async with asyncio.TaskGroup() as group:
            task = group.create_task(run(), name='base-browser-listener')
            try:
                async with asyncio.timeout(_START):
                    while not server.started:
                        await asyncio.sleep(_POLL)
                yield
            finally:
                stopping = True
                server.should_exit = True
                try:
                    async with asyncio.timeout(_STOP):
                        await asyncio.shield(task)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
                    raise
