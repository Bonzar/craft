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
