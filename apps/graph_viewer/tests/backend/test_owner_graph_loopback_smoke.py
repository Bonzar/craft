"""Exercise actual TLS/HTTP headers and SSE hello on a disposable Graph."""
import asyncio
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

import httpx
import uvicorn

from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.tests.backend.test_service import fixture

DEADLINE = 3
PASSWORD = 'synthetic-only-owner'


class LoopbackGraphWire(unittest.TestCase):
    def test_tls_hello_is_flushed_while_snapshot_remains_blocked(self):
        with tempfile.TemporaryDirectory() as work, socket.socket() as listener:
            directory = Path(work)
            cert, key, database = (directory / n for n in ('cert','key','fixture.sqlite3'))
            fixture(database)
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                            '-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1',
                            '-keyout',str(key),'-out',str(cert)],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            listener.bind(('127.0.0.1',0))
            listener.listen(16)
            port = listener.getsockname()[1]
            origin = f'https://127.0.0.1:{port}'

            def no_queue(*args, **kwargs):
                self.fail('wire smoke must not run queue or effect operations')

            browser = BrowserOwnerAPI(stage='test',perform=no_queue,queue_snapshot=no_queue,dashboard_live=False)
            graph = create_app(database,directory/'state',password=PASSWORD,synthetic_fixture=True,
                               public_origin=origin,browser_owner_api=browser)
            entered, release, returned = (threading.Event() for _ in range(3))
            original_ensure = graph.state.manager.ensure

            async def blocked_ensure(*args, **kwargs):
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(.01)
                returned.set()
                return await original_ensure(*args, **kwargs)

            graph.state.manager.ensure = blocked_ensure
            server = uvicorn.Server(uvicorn.Config(graph,host='127.0.0.1',port=port,ssl_keyfile=str(key),
                ssl_certfile=str(cert),proxy_headers=False,access_log=False,log_level='critical',
                timeout_graceful_shutdown=1))
            thread = threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
            thread.start()
            try:
                start = time.monotonic()
                while not server.started and thread.is_alive() and time.monotonic()-start < 5:
                    time.sleep(.01)
                self.assertTrue(server.started,'fixture server did not start')
                verify=ssl.create_default_context(cafile=str(cert))
                with httpx.Client(base_url=origin,verify=verify,timeout=DEADLINE,trust_env=False,
                                  follow_redirects=False) as client:
                    response = client.post('/api/v1/session',headers={'Origin':origin},json={'password':PASSWORD})
                    self.assertEqual(response.status_code,200)
                    started = time.monotonic()
                    with client.stream('GET','/api/v1/events?wire=graph-v1',headers={
                            'Accept':'text/event-stream','Accept-Encoding':'gzip','Sec-Fetch-Site':'same-origin'
                        }) as stream:
                        header_ms=(time.monotonic()-started)*1000
                        self.assertEqual(stream.status_code,200)
                        self.assertTrue(stream.headers.get('content-type','').startswith('text/event-stream'))
                        self.assertNotIn('content-encoding',stream.headers)
                        # Nginx may consume acceleration headers; we are
                        # intentionally observing the app socket, not ingress.
                        self.assertEqual(stream.headers.get('x-accel-buffering'),'no')
                        lines=iter(stream.iter_lines())
                        self.assertEqual(next(lines),'event: hello')
                        data=next(lines)
                        self.assertTrue(data.startswith('data: '))
                        self.assertEqual(json.loads(data[6:])['protocol'],'graph-v1')
                        self.assertEqual(next(lines),'')
                        hello_ms=(time.monotonic()-started)*1000
                        self.assertLessEqual(header_ms,hello_ms)
                        self.assertLess(hello_ms,DEADLINE*1000)
                        self.assertTrue(entered.wait(1),'fixture never attempted snapshot')
                        self.assertFalse(release.is_set())
                        self.assertFalse(returned.is_set())
                        print(f'upstream_loopback_tls only: headers_ms={header_ms:.1f} hello_ms={hello_ms:.1f} snapshot_blocked=true')
            finally:
                release.set()
                server.should_exit=True
                thread.join(5)
                self.assertFalse(thread.is_alive(),'fixture did not shut down')
