"""Disposable synthetic owner, never any deployment or private graph."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.direct_stream import Binding,DIRECT_PATH,TICKET_PATH,Tickets
from test_service import fixture

PUBLIC='https://visual-test.example'
DIRECT='https://8.8.8.8'

class DirectStream(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'source.sqlite3';fixture(self.source)
    def create(self,enabled=True,synthetic=True):
        return create_app(self.source,self.root/'viewer',password='owner-fixture-long',
                          public_origin=PUBLIC,synthetic_fixture=synthetic,
                          test_direct_events_origin=DIRECT if enabled else None)
    def test_rejects_non_test_or_untrusted_direct_origin_without_enabling_any_route(self):
        with self.assertRaises(ValueError):
            self.create(synthetic=False)
        for value in ('http://8.8.8.8','https://localhost','https://127.0.0.1','https://8.8.8.8/'):
            with self.assertRaises(ValueError):Binding.test(PUBLIC,value,True)
        with TestClient(self.create(enabled=False),base_url='https://testserver') as client:
            self.assertEqual(client.get(DIRECT_PATH,headers={'Origin':PUBLIC}).status_code,404)

    def test_real_owner_csrf_and_origin_then_one_use_direct_without_cookie(self):
        app=self.create()
        with TestClient(app,base_url=PUBLIC) as ui:
            proven=ui.post('/api/v1/session',json={'password':'owner-fixture-long'},headers={'Origin':PUBLIC})
            self.assertEqual(proven.status_code,200)
            csrf=proven.json()['csrf_token']
            for settings in ({'Origin':'https://prod.example','X-CSRF-Token':csrf},
                             {'Origin':PUBLIC,'X-CSRF-Token':'bad'},
                             {'X-CSRF-Token':csrf}):
                self.assertEqual(ui.post(TICKET_PATH,headers=settings).status_code,403)
            valid={'Origin':PUBLIC,'X-CSRF-Token':csrf}
            granted=ui.post(TICKET_PATH,headers=valid)
            self.assertEqual(granted.status_code,200)
            payload=granted.json(); token=payload['ticket']
            self.assertEqual(payload['stream_url'],DIRECT+'/__mvt/api/v1/events-direct')
            self.assertEqual(payload['expires_in'],15)
            request={'Origin':PUBLIC,'Authorization':'Bearer '+token}
            self.assertEqual(ui.get(DIRECT_PATH,headers=request).status_code,403)
            with TestClient(app,base_url='https://testserver') as stream:
                preflight={'Origin':PUBLIC,'Access-Control-Request-Method':'GET',
                           'Access-Control-Request-Headers':'authorization'}
                pre=stream.options(DIRECT_PATH,headers=preflight)
                self.assertEqual(pre.status_code,204)
                self.assertNotIn('access-control-allow-credentials',pre.headers)
                self.assertEqual(stream.get(DIRECT_PATH,headers={'Origin':PUBLIC}).status_code,401)
                self.assertEqual(stream.get(DIRECT_PATH,headers={**request,'Origin':'https://prod.example'}).status_code,403)
                self.assertEqual(stream.get('/api/v1/capabilities',headers=request).status_code,401)
                with patch('apps.graph_viewer.server.direct_stream.LIVE_SECONDS',0):
                    response=stream.get(DIRECT_PATH,headers=request)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['access-control-allow-origin'],PUBLIC)
                self.assertEqual(response.text,'event: hello\ndata: {}\n\nevent: reset\ndata: {}\n\n')
                self.assertEqual(stream.get(DIRECT_PATH,headers=request).status_code,401)

    def test_ticket_expiry_replacement_single_consumption_and_revocation(self):
        now=[100]; tickets=Tickets(Binding.test(PUBLIC,DIRECT,True),clock=lambda:now[0])
        old=tickets.issue('owner1')
        token=tickets.issue('owner1')
        self.assertIsNone(tickets.consume(old))
        key,_=tickets.consume(token)
        self.assertIsNone(tickets.consume(token))
        tickets.close(key)
        short=tickets.issue('owner2')
        now[0]+=15
        self.assertIsNone(tickets.consume(short))

    def test_socket_delivers_distinct_events_while_open_and_closes_on_logout(self):
        import json
        import socket
        import threading
        import time
        import httpx
        import uvicorn
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            listener.listen()
            addr='http://127.0.0.1:'+str(listener.getsockname()[1])
            server=uvicorn.Server(uvicorn.Config(self.create(),host='127.0.0.1',
                    access_log=False,log_level='critical'))
            thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
            thread.start()
            try:
                end=time.monotonic()+4
                while not server.started and time.monotonic()<end:
                    time.sleep(.01)
                self.assertTrue(server.started)
                with httpx.Client(base_url=addr,timeout=3,trust_env=False) as client:
                    login=client.post('/api/v1/session',headers={'Origin':PUBLIC},
                                      json={'password':'owner-fixture-long'})
                    self.assertEqual(login.status_code,200)
                    browser={'Origin':PUBLIC,'Cookie':login.headers['set-cookie'].split(';',1)[0],
                             'X-CSRF-Token':login.json()['csrf_token']}
                    response=client.post(TICKET_PATH,headers=browser)
                    token=response.json()['ticket']
                    stream_headers={'Origin':PUBLIC,'Authorization':'Bearer '+token}
                    with patch('apps.graph_viewer.server.direct_stream.HEARTBEAT_SECONDS',.25), \
                         client.stream('GET',DIRECT_PATH,headers=stream_headers) as stream:
                        self.assertEqual(stream.status_code,200)
                        frames={}
                        lines=stream.iter_lines()
                        for line in lines:
                            if line.startswith('event: '):
                                frames[line[7:]]=time.monotonic()
                            elif line.startswith('data: '):
                                self.assertEqual(json.loads(line[6:]),{})
                            if 'heartbeat' in frames:
                                self.assertLess(frames['hello']+.15,frames['heartbeat'])
                                self.assertEqual(client.delete('/api/v1/session',headers=browser).status_code,200)
                                revoked=time.monotonic()
                                self.assertFalse(any(line.startswith('event: ') for line in lines))
                                self.assertLess(time.monotonic()-revoked,1.25)
                                break
                        self.assertIn('heartbeat',frames)
            finally:
                server.should_exit=True
                thread.join(timeout=5)
