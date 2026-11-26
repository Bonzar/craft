"""Disposable real Uvicorn+HTTP witness using an ephemeral loopback port."""
import asyncio
from contextlib import asynccontextmanager
import socket
import unittest

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from .browser_listener import browser_listener
from .browser_surface import browser_surface

class RealListenerTest(unittest.IsolatedAsyncioTestCase):
    async def test_graph_lifespan_and_separate_browser_surface_on_real_socket(self):
        calls=[]
        @asynccontextmanager
        async def graph_lifespan(app):
            calls.append('startup')
            yield
            calls.append('shutdown')
        app=Starlette(routes=[Route('/api/v1/session',lambda req: JSONResponse({'fixture':True}))],
                      lifespan=graph_lifespan)
        with socket.socket() as bound:
            bound.bind(('127.0.0.1',0))
            bound.setblocking(False)
            port=bound.getsockname()[1]
            async with browser_listener(browser_surface(app),port,_bind=lambda supplied:bound):
                self.assertEqual(calls,['startup'])
                for path, status in (('/api/v1/session',b'200'),('/memory-api-prod',b'404'),('/mcp',b'404')):
                    reader,writer=await asyncio.open_connection('127.0.0.1',port)
                    writer.write(f'GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'.encode())
                    await writer.drain()
                    self.assertEqual((await asyncio.wait_for(reader.readline(),2)).split()[1],status)
                    await asyncio.wait_for(reader.read(),2)
                    writer.close()
                    await writer.wait_closed()
            self.assertEqual(calls,['startup','shutdown'])
        with self.assertRaises(OSError):
            await asyncio.open_connection('127.0.0.1',port)

    async def test_late_browser_failure_signals_running_parent_to_exit(self):
        from .browser_listener import _SideServer
        import uvicorn

        die=asyncio.Event()
        class FailingBrowser:
            started=False
            should_exit=False
            async def serve(self, *, sockets):
                self.started=True
                await die.wait()
                raise RuntimeError('fixture child death')

        @asynccontextmanager
        async def primary_lifespan(_app):
            with socket.socket() as browser_sock:
                browser_sock.bind(('127.0.0.1',0))
                async with browser_listener(lambda: None,browser_sock.getsockname()[1],
                        _bind=lambda _:browser_sock,_server=lambda _:FailingBrowser(),
                        on_failure=lambda:setattr(parent,'should_exit',True)):
                    yield

        app=Starlette(routes=[],lifespan=primary_lifespan)
        parent=_SideServer(uvicorn.Config(app,host='127.0.0.1',lifespan='on',access_log=False,
                                         log_level='critical'))
        with socket.socket() as bound:
            bound.bind(('127.0.0.1',0))
            port=bound.getsockname()[1]
            serving=asyncio.create_task(parent.serve(sockets=[bound]))
            async with asyncio.timeout(3):
                while not parent.started:
                    await asyncio.sleep(0.01)
                die.set()
                await serving
            self.assertTrue(parent.should_exit)
        with self.assertRaises(OSError):
            await asyncio.open_connection('127.0.0.1',port)
