"""Disposable synthetic owner, never any deployment or private graph."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.direct_stream import (Binding,DIRECT_PATH,TICKET_PATH,Tickets,
                                                  active_test_scope)
from test_service import fixture

PUBLIC='https://visual-test.example'
DIRECT='https://8.8.8.8'

class DirectStream(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'source.sqlite3';fixture(self.source)
        self.active=('revision','archive')
        stack=patch('apps.graph_viewer.server.direct_stream.active_test_scope',side_effect=lambda _:self.active)
        stack.start();self.addCleanup(stack.stop)
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

    def test_transport_advertisement_is_dark_and_revokes_on_runtime_drift(self):
        for enabled in (False, True):
            with TestClient(self.create(enabled=enabled),base_url=PUBLIC) as ui:
                self.assertEqual(ui.get('/api/v1/capabilities').status_code,401)
                self.assertEqual(ui.post('/api/v1/session',json={'password':'owner-fixture-long'},
                                         headers={'Origin':PUBLIC}).status_code,200)
                value=ui.get('/api/v1/capabilities').json()
                if enabled:
                    self.assertEqual(value['owner_live_transport'],{'version':1,'lane':'test','origin':DIRECT})
                    self.active=('revision','changed-archive')
                    self.assertNotIn('owner_live_transport',ui.get('/api/v1/capabilities').json())
                else:
                    self.assertNotIn('owner_live_transport',value)

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
            self.assertIn('no-store',granted.headers['cache-control'])
            payload=granted.json(); token=payload['ticket']
            self.assertEqual(payload['stream_url'],DIRECT+'/__mvt/api/v1/events-direct')
            self.assertEqual(payload['expires_in'],15)
            self.assertRegex(payload['epoch'],r'^[a-f0-9]{64}$')
            import time
            for _ in range(40):
                graph=ui.get('/api/v1/graph',headers={'Origin':PUBLIC})
                if graph.status_code==200:break
                time.sleep(.02)
            self.assertEqual(graph.status_code,200)
            self.assertEqual(graph.json()['owner_live_epoch'],payload['epoch'])
            with TestClient(app,base_url=PUBLIC) as anonymous:
                self.assertNotIn('owner_live_epoch',anonymous.get('/api/v1/graph').json())
            request={'Origin':PUBLIC,'Authorization':'Bearer '+token}
            self.assertEqual(ui.get(DIRECT_PATH,headers=request).status_code,403)
            with TestClient(app,base_url='https://testserver') as stream:
                preflight={'Origin':PUBLIC,'Access-Control-Request-Method':'GET',
                           'Access-Control-Request-Headers':'authorization'}
                pre=stream.options(DIRECT_PATH,headers=preflight)
                self.assertEqual(pre.status_code,204)
                self.assertNotIn('access-control-allow-credentials',pre.headers)
                self.assertEqual(stream.get(DIRECT_PATH,headers={'Origin':PUBLIC}).status_code,401)
                self.assertEqual(stream.options(DIRECT_PATH,headers={**preflight,'Cookie':''}).status_code,403)
                self.assertEqual(stream.get(DIRECT_PATH,headers={**request,'Cookie':'malformed'}).status_code,403)
                self.assertEqual(stream.get(DIRECT_PATH,headers={**request,'Origin':'https://prod.example'}).status_code,403)
                self.assertEqual(stream.get('/api/v1/capabilities',headers=request).status_code,401)
                with patch('apps.graph_viewer.server.direct_stream.LIVE_SECONDS',0):
                    response=stream.get(DIRECT_PATH,headers=request)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['access-control-allow-origin'],PUBLIC)
                self.assertEqual(response.text,'event: hello\ndata: {}\n\nevent: reset\ndata: {}\n\n')
                self.assertEqual(stream.get(DIRECT_PATH,headers=request).status_code,401)

    def test_manifest_lookup_crossing_physical_generation_never_labels_old_graph_as_new(self):
        import asyncio
        import sqlite3
        from types import SimpleNamespace
        from starlette.datastructures import QueryParams
        from apps.graph_viewer.server.app import APIError,graph_manifest
        app=self.create()
        manager=app.state.manager
        prior=manager._generation
        old=SimpleNamespace(revision='old-fixture',manifest={'revision':'old-fixture'})

        async def crosses_reset(_):
            with sqlite3.connect(self.source) as db:
                db.execute('CREATE TABLE memory_database_clear_state(singleton INTEGER PRIMARY KEY,revision TEXT,salt BLOB)')
                db.execute('INSERT INTO memory_database_clear_state VALUES(1,?,?)',('a'*32,b'fixture'))
            # Simulate another request/poll acquiring the new fence before the
            # suspended old request resumes. Exercise the route itself, without
            # the outer Graph security middleware's independent body fence.
            self.assertNotEqual(manager.sync_generation(),prior)
            return old

        request=SimpleNamespace(app=app,query_params=QueryParams())
        with (patch.object(manager,'ensure',side_effect=crosses_reset),
              patch.object(app.state.owner,'session',return_value='fictional-owner'),
              self.assertRaises(APIError) as denied):
            asyncio.run(graph_manifest(request))
        self.assertEqual(denied.exception.status,409)
        self.assertEqual(denied.exception.code,'revision_expired')

    def test_graph_marker_stays_dark_outside_enabled_test(self):
        import time
        with TestClient(self.create(enabled=False),base_url=PUBLIC) as ui:
            self.assertEqual(ui.post('/api/v1/session',json={'password':'owner-fixture-long'},
                                     headers={'Origin':PUBLIC}).status_code,200)
            for _ in range(50):
                graph=ui.get('/api/v1/graph',headers={'Origin':PUBLIC})
                if graph.status_code==200:break
                time.sleep(.02)
            self.assertEqual(graph.status_code,200)
            self.assertNotIn('owner_live_epoch',graph.json())

    def test_machine_read_authority_does_not_receive_owner_marker(self):
        import time
        from types import SimpleNamespace
        app=self.create()
        app.state.bridge_read_service=SimpleNamespace(
            authorizes=lambda request: request.method=='GET' and request.url.path=='/api/v1/graph')
        with TestClient(app,base_url=PUBLIC) as machine:
            for _ in range(50):
                graph=machine.get('/api/v1/graph',headers={'Origin':PUBLIC})
                if graph.status_code==200:break
                time.sleep(.02)
            self.assertEqual(graph.status_code,200)
            self.assertNotIn('owner_live_epoch',graph.json())

    def test_ticket_expiry_replacement_single_consumption_and_revocation(self):
        now=[100]; tickets=Tickets(Binding.test(PUBLIC,DIRECT,True),clock=lambda:now[0])
        old=tickets.issue('owner1')
        token=tickets.issue('owner1')
        self.assertIsNone(tickets.consume(old))
        key,_,_=tickets.consume(token)
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

    def test_lane_drift_and_generation_change_invalidates_the_ticket(self):
        import sqlite3
        app=self.create()
        with TestClient(app,base_url=PUBLIC) as ui, TestClient(app,base_url='https://testserver') as stream:
            csrf=ui.post('/api/v1/session',json={'password':'owner-fixture-long'},
                        headers={'Origin':PUBLIC}).json()['csrf_token']
            browser={'Origin':PUBLIC,'X-CSRF-Token':csrf}
            self.assertEqual(ui.post(TICKET_PATH,headers=browser,content=b'not empty').status_code,400)
            issue=lambda:ui.post(TICKET_PATH,headers=browser)
            previous=issue().json();old=previous['ticket']
            with sqlite3.connect(self.source) as db:
                db.execute('CREATE TABLE memory_database_clear_state(singleton INTEGER PRIMARY KEY,revision TEXT,salt BLOB)')
                db.execute('INSERT INTO memory_database_clear_state VALUES(1,?,?)',('a'*32,b'fixture'))
            wire=lambda token:{'Origin':PUBLIC,'Authorization':'Bearer '+token}
            self.assertEqual(stream.get(DIRECT_PATH,headers=wire(old)).status_code,409)
            self.assertEqual(stream.get(DIRECT_PATH,headers=wire(old)).status_code,401)
            next_epoch=issue().json();fresh=next_epoch['ticket']
            self.assertNotEqual(previous['epoch'],next_epoch['epoch'])
            self.active=('later','archive')
            self.assertEqual(stream.get(DIRECT_PATH,headers=wire(fresh)).status_code,404)
            self.assertEqual(issue().status_code,404)

    def test_active_stream_resets_without_emitting_retired_journal(self):
        import asyncio
        import sqlite3
        from starlette.requests import Request
        from apps.graph_viewer.server.direct_stream import direct_events
        app=self.create()
        # This tests the actual async response, on a pristine disposable graph.
        manager=app.state.manager
        cookie,_=app.state.owner.login('owner-fixture-long','synthetic')
        from apps.graph_viewer.server.direct_stream import digest
        token=app.state.direct_events.issue(digest(cookie),manager.sync_generation())
        async def disconnected():
            return {'type':'http.request','body':b'','more_body':False}
        req=Request({'type':'http','app':app,'method':'GET','path':DIRECT_PATH,
                     'headers':[(b'origin',PUBLIC.encode()),(b'authorization',('Bearer '+token).encode())]},receive=disconnected)
        async def witness():
            response=await direct_events(req)
            self.assertEqual(response.status_code,200)
            body=response.body_iterator
            self.assertEqual(await anext(body),'event: hello\ndata: {}\n\n')
            self.assertEqual(await anext(body),'event: reset\ndata: {}\n\n')
            manager.emit('delta',{'test':'must not leave the old generation'})
            with sqlite3.connect(self.source) as db:
                db.execute('CREATE TABLE memory_database_clear_state(singleton INTEGER PRIMARY KEY,revision TEXT,salt BLOB)')
                db.execute('INSERT INTO memory_database_clear_state VALUES(1,?,?)',('b'*32,b'fixture'))
            self.assertEqual(await anext(body),'event: reset\ndata: {}\n\n')
            with self.assertRaises(StopAsyncIteration):
                await anext(body)
            await manager.stop()
        asyncio.run(witness())

    def test_ordinary_fixture_cannot_claim_deployed_test_lane(self):
        with patch('apps.graph_viewer.server.direct_stream.active_test_scope',new=active_test_scope):
            with self.assertRaisesRegex(ValueError,'exact active TEST runtime'):
                self.create(synthetic=True)

    def test_nonempty_body_without_length_is_rejected_before_minting(self):
        import asyncio
        from starlette.requests import Request
        from apps.graph_viewer.server.direct_stream import issue_ticket
        from apps.graph_viewer.server.common import APIError
        app=self.create()
        cookie,csrf=app.state.owner.login('owner-fixture-long','synthetic')
        async def receive():
            return {'type':'http.request','body':b'x','more_body':True}
        request=Request({'type':'http','app':app,'method':'POST','path':TICKET_PATH,
                         'headers':[(b'origin',PUBLIC.encode()),(b'x-csrf-token',csrf.encode()),
                                    (b'cookie',('gv_session='+cookie).encode())]},receive)
        try:
            with self.assertRaises(APIError) as failure:
                asyncio.run(issue_ticket(request))
            self.assertEqual(failure.exception.status,400)
            self.assertFalse(app.state.direct_events.unused)
        finally:
            asyncio.run(app.state.manager.stop())
