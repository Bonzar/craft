"""Real Base Graph owner HTTP boundary and real disposable domain transactions."""
from contextlib import nullcontext
from pathlib import Path
import json
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI, PREFIX, configured_browser_api, read_test_clear_profile
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.queue_rest import snapshot
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_runtime import initialize_empty

ORIGIN='https://owner-test.example'
BROWSER={'origin':ORIGIN}
IDENTITY=('assertion-memory-v0.0.42','a'*64)
CLEAR=PREFIX+'database/clear-v2'


class BrowserOwnerBoundaryTest(unittest.TestCase):
    def setUp(self):
        root=Path(self.enterContext(TemporaryDirectory())).resolve()
        self.db=root/'database.sqlite3'
        self.viewer=root/'private-viewer'
        self.store=self.enterContext(MemoryStore(self.db))
        self.assertTrue(initialize_empty(self.store))
        self.service=MemoryService(self.store)
        self.service.capture_message('fixture','one','fixture-user','user','Fictional memory',1,'fixture')
        self.clock=1800000000
        self.control=owner_operations(database=self.db,release_lease=nullcontext,
                                      identity=lambda:IDENTITY,stage='test')
    def make(self, clear=False, *, browser=None, **options):
        def project(**page):
            config=SimpleNamespace(processing_session_ids=None,provider='none',auto_process_fresh_mac=False)
            return snapshot(config,identity=lambda:IDENTITY,database=self.db,interactive_enabled=False,
                            clear_ready=clear,can_change=True,**page)
        api=browser or BrowserOwnerAPI(stage='test',perform=self.control,queue_snapshot=project,
                            clear_enabled=clear,clock=lambda:self.clock)
        app=create_app(self.db,self.viewer,password='synthetic-owner-password',
                       public_origin=ORIGIN,browser_owner_api=api,**options)
        return self.enterContext(TestClient(app,base_url=ORIGIN))
    def login(self, client):
        result=client.post('/api/v1/session',headers=BROWSER,json={'password':'synthetic-owner-password'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertIn('Secure',result.headers['set-cookie'])
        self.assertIn('HttpOnly',result.headers['set-cookie'])
        return result.json()['csrf_token']
    def prepare(self, client, path, data):
        response=client.post(path+'/prepare',headers=BROWSER,json=data)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    def test_exact_boundary_no_loopback_alias_or_browser_forwarded_auth(self):
        c=self.make()
        self.assertEqual(c.post('/api/v1/session',json={'password':'synthetic-owner-password'}).status_code,403)
        self.assertEqual(c.get('/api/v1/graph').status_code,401)
        self.login(c)
        for alias in ['/api/v1/queue-status','/api/v1/queue-pause','/api/v1/queue-database/clear',
                      PREFIX+'database/clear','/api/v1/capabilities?database_clear_scope=1','/api/v1/%73ession']:
            self.assertEqual(c.get(alias).status_code,404,alias)
        for suffix in ('queue_status=1&queue_status=1','queue_status=1&extra=1','limit=1','foreign=1'):
            self.assertEqual(c.get('/api/v1/capabilities?'+suffix).status_code,400,suffix)
        self.assertEqual(c.get('/api/v1/graph?mode=current&unexpected=1').status_code,400)
        self.assertEqual(c.get('/api/v1/search?q=x&q=y').status_code,400)
        self.assertIn(c.get('/api/v1/graph?mode=current&history=0').status_code,(200,202))
        for name in ('Authorization','Forwarded','X-Forwarded-Host','X-Memory-Visual-Pause-Owner-Ingress',
                     'X-Memory-Pause-Signature'):
            self.assertEqual(c.get('/api/v1/graph',headers={name:'forged'}).status_code,403,name)
        self.assertEqual(c.get('/api/v1/search',headers={'Origin':'https://other.test'}).status_code,403)
        self.assertEqual(c.post(PREFIX+'pause/prepare?alias=1',headers=BROWSER,json={}).status_code,404)
        self.assertEqual(c.put(PREFIX+'pause/prepare',headers=BROWSER,json={}).status_code,404)
        for path in ['/memory-api-test','/memory-mcp-test','/api/v1/private']:
            self.assertEqual(c.post(path,headers=BROWSER,json={}).status_code,404,path)
    def test_pause_ticket_bound_to_owner_session_action_revision_and_single_use(self):
        path=PREFIX+'pause'
        c=self.make()
        self.login(c)
        draft=self.prepare(c,path,{})
        body={k:draft[k] for k in ('stage',)}
        body.update(paused=True,expected_revision=draft['revision'])
        key={'x-csrf-token':draft['csrf_token']}
        self.assertEqual(c.put(path,headers=key,json=body).status_code,403)
        self.assertEqual(c.put(path,headers=BROWSER,json=body).status_code,403)
        self.login(c) # different owner session, old prepared ticket unavailable
        self.assertEqual(c.put(path,headers=BROWSER|key,json=body).status_code,403)
        draft=self.prepare(c,path,{})
        key={'x-csrf-token':draft['csrf_token']}
        self.assertEqual(c.put(path,headers=BROWSER|key,json=body|{'stage':'prod'}).status_code,409)
        result=c.put(path,headers=BROWSER|key,json=body)
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['paused'])
        self.assertEqual(c.put(path,headers=BROWSER|key,json=body).status_code,403)
    def test_queue_projection_and_session_control_keep_existing_wire(self):
        c=self.make()
        self.login(c)
        result=c.get('/api/v1/capabilities?queue_status=1&limit=50&offset=0')
        self.assertEqual(result.status_code,200,result.text)
        body=result.json()
        self.assertTrue(body['capabilities']['standalone_pause'])
        self.assertFalse(body['processing_control']['can_change'])
        self.assertNotIn('database_clear',body)
        path=PREFIX+'sessions'
        prep=self.prepare(c,path,{'format':'policy-v1'})
        self.assertIn('policy',prep)
        self.assertEqual(prep['stage'],'test')
        queue=next(q for q in body['queues'] if q['id']=='source')
        item=queue['items'][0]
        pending={'stage':'test','queue_id':'source','item_id':item['id'],
                 'expected_revision':item['preview_revision']}
        route=PREFIX+'items/source-preview'
        proof=self.prepare(c,route,pending)
        out=c.put(route,headers=BROWSER|{'x-csrf-token':proof['csrf_token']},json=pending)
        self.assertEqual(out.status_code,200,out.text)
        self.assertEqual(out.json()['source_preview'],'Fictional memory')
    def test_clear_disabled_and_live_exact_same_graph_generation_when_enabled(self):
        c=self.make(clear=True)
        self.login(c)
        status=c.get('/api/v1/capabilities?queue_status=1').json()
        state=status['database_clear']
        self.assertTrue(state['can_clear'])
        self.assertNotIn('scope',state)
        req={'stage':'test','expected_revision':state['revision']}
        self.assertEqual(c.post(CLEAR+'/prepare',headers=BROWSER,json=req|{'confirmation':'ОЧИСТИТЬ PROD'}).status_code,400)
        prep=self.prepare(c,CLEAR,req|{'confirmation':'ОЧИСТИТЬ TEST'})
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
        updated=c.put(CLEAR,headers=BROWSER|{'x-csrf-token':prep['csrf_token']},json=req)
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertTrue(updated.json()['cleared'])
        self.assertNotEqual(updated.json()['revision'],req['expected_revision'])
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],0)
        self.assertTrue(c.get('/api/v1/session').json()['authenticated'])
    def test_direct_stream_remains_separate_one_use_ticket(self):
        self.enterContext(patch('apps.graph_viewer.server.direct_stream.active_test_scope',
                                return_value=('exact-release','exact-archive')))
        c=self.make(synthetic_fixture=True,test_direct_events_origin='https://8.8.8.8')
        csrf=self.login(c)
        granted=c.post('/api/v1/events-ticket',headers=BROWSER|{'x-csrf-token':csrf})
        self.assertEqual(granted.status_code,200,granted.text)
        direct={'origin':ORIGIN,'authorization':'Bearer '+granted.json()['ticket']}
        self.assertEqual(c.get('/api/v1/capabilities',headers=direct).status_code,403)
        self.assertEqual(c.get('/api/v1/events-direct',headers=direct).status_code,403)
        with TestClient(c.app,base_url='https://testserver') as stream:
            proof=BROWSER|{'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization'}
            self.assertEqual(stream.options('/api/v1/events-direct',headers=proof).status_code,204)
            with patch('apps.graph_viewer.server.direct_stream.LIVE_SECONDS',0):
                response=stream.get('/api/v1/events-direct',headers=direct)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(stream.get('/api/v1/events-direct',headers=direct).status_code,401)

    def test_late_recorder_and_expired_proof_never_clear(self):
        c=self.make(clear=True)
        self.login(c)
        status=c.get('/api/v1/capabilities?queue_status=1').json()
        req={'stage':'test','expected_revision':status['database_clear']['revision']}
        prep=self.prepare(c,CLEAR,req|{'confirmation':'ОЧИСТИТЬ TEST'})
        self.clock+=21
        self.assertEqual(c.put(CLEAR,headers=BROWSER|{'x-csrf-token':prep['csrf_token']},json=req).status_code,403)
        prep=self.prepare(c,CLEAR,req|{'confirmation':'ОЧИСТИТЬ TEST'})
        self.service.ingest_register('fictional','ordinary')
        self.assertEqual(c.put(CLEAR,headers=BROWSER|{'x-csrf-token':prep['csrf_token']},json=req).status_code,409)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)


    def test_ordinary_clear_is_one_post_with_owner_session_csrf_and_no_marker(self):
        current=dict(release_version=IDENTITY[0],generation=IDENTITY[1],
            auto_process_fresh_mac=False,processing_session_ids=[],model_configured=False,interactive_enabled=False)
        dashboard=SimpleNamespace(stage='test',db=self.db,metadata=lambda:current,release_lease=nullcontext)
        api=configured_browser_api(dashboard,ordinary_test_clear=True)
        c=self.make(browser=api)
        body={'confirmation':'ОЧИСТИТЬ TEST'}
        self.assertEqual(c.post(CLEAR,headers=BROWSER,json=body).status_code,401)
        csrf=self.login(c)
        proof=BROWSER|{'X-CSRF-Token':csrf}
        self.assertTrue(c.get('/api/v1/capabilities?queue_status=1').json()['capabilities']['owner_database_clear_post'])
        self.assertEqual(c.post(CLEAR,headers=BROWSER,json=body).status_code,403)
        self.assertEqual(c.post(CLEAR,headers=proof|{'Origin':'https://foreign.test'},json=body).status_code,403)
        self.assertEqual(c.post(CLEAR,headers=proof,json={'confirmation':'ОЧИСТИТЬ PROD'}).status_code,400)
        self.assertEqual(c.post(CLEAR+'/prepare',headers=proof,json=body).status_code,404)
        self.assertEqual(c.put(CLEAR,headers=proof,json=body).status_code,404)
        recorded=[]
        perform=api.perform
        def no_effect(action,args):
            if action=='clear_prepare':
                return {'revision':perform(action,args)['revision']}
            if action=='clear_commit':
                recorded.append(args)
                return {'stage':'test','cleared':True,'revision':'f'*32,'capture_rearm':'not_required'}
            return perform(action,args)
        api.perform=no_effect
        with patch.object(api,'graph_ready',side_effect=AssertionError('ordinary action or queue must not need Graph ready')):
            self.assertTrue(c.get('/api/v1/capabilities?queue_status=1').json()['capabilities']['owner_database_clear_post'])
            self.assertEqual(c.post(CLEAR,headers=proof,json=body).status_code,200)
        self.assertEqual(len(recorded),1)
        self.assertEqual(set(recorded[0]),{'expected_revision'})
        old=recorded[0]['expected_revision']
        current['generation']='d'*64
        c2=self.make(browser=configured_browser_api(dashboard,ordinary_test_clear=True))
        self.login(c2)
        capability=c2.get('/api/v1/capabilities?queue_status=1').json()['capabilities']
        self.assertTrue(capability['owner_database_clear_post'])
        self.assertNotEqual(configured_browser_api(dashboard,ordinary_test_clear=True).perform('clear_prepare',{})['revision'],old)

    def test_canonical_test_clear_profile_is_explicit_and_one_epoch_only(self):
        witness=self.control('clear_prepare',{})
        current=dict(release_version=IDENTITY[0],generation=IDENTITY[1],
            auto_process_fresh_mac=False,processing_session_ids=[],model_configured=False,interactive_enabled=False)
        dashboard=SimpleNamespace(stage='test',db=self.db,metadata=lambda:current,release_lease=nullcontext)
        wrong=witness['scope'][::-1]
        assert wrong!=witness['scope']
        for invalid in ('',True,IDENTITY[1],IDENTITY[1].upper()+':'+witness['scope'],None):
            if invalid is None:
                self.assertFalse(configured_browser_api(dashboard).clear_enabled)
            else:
                with self.assertRaises(ValueError):
                    configured_browser_api(dashboard,clear_profile=invalid)
        with self.assertRaises(ValueError):
            configured_browser_api(SimpleNamespace(stage='prod'),
                                   clear_profile=IDENTITY[1]+':'+witness['scope'])
        invalid=self.make(browser=configured_browser_api(dashboard,clear_profile=IDENTITY[1]+':'+wrong))
        self.login(invalid)
        status=invalid.get('/api/v1/capabilities?queue_status=1').json()['database_clear']
        self.assertFalse(status['can_clear'])
        prep={'stage':'test','expected_revision':status['revision'],'confirmation':'ОЧИСТИТЬ TEST'}
        self.assertEqual(invalid.post(CLEAR+'/prepare',headers=BROWSER,json=prep).status_code,409)
        invalid.close()
        c=self.make(browser=configured_browser_api(dashboard,clear_profile=IDENTITY[1]+':'+witness['scope']))
        self.login(c)
        self.assertTrue(c.get('/api/v1/capabilities?queue_status=1').json()['database_clear']['can_clear'])
        self.assertNotIn('database_clear',c.get('/api/v1/capabilities?queue_status=1',
            headers={'origin':'https://other.example'}).json())
        current['generation']='b'*64
        self.assertNotIn('database_clear',c.get('/api/v1/capabilities?queue_status=1').json())
        self.assertEqual(c.post(CLEAR+'/prepare',headers=BROWSER,json=prep).status_code,404)
        current['generation']=IDENTITY[1]
        self.assertEqual(c.post(CLEAR+'/prepare',headers=BROWSER,json=prep|{'confirmation':'ОЧИСТИТЬ PROD'}).status_code,400)
        ticket=self.prepare(c,CLEAR,prep)
        other=self.prepare(c,CLEAR,prep)
        req={k:v for k,v in prep.items() if k!='confirmation'}
        reply=c.put(CLEAR,headers=BROWSER|{'x-csrf-token':ticket['csrf_token']},json=req)
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertTrue(reply.json()['cleared'])
        self.assertTrue(c.get('/api/v1/session').json()['authenticated'])
        status=c.get('/api/v1/capabilities?queue_status=1').json()['database_clear']
        self.assertFalse(status['can_clear'])
        self.assertNotEqual(status['revision'],req['expected_revision'])
        self.service.capture_message('fixture','two','fixture-user','user','New fictional memory',2,'fixture')
        self.assertEqual(c.put(CLEAR,headers=BROWSER|{'x-csrf-token':other['csrf_token']},json=req).status_code,409)
        self.assertEqual(c.post(CLEAR+'/prepare',headers=BROWSER,
            json=prep|{'expected_revision':status['revision']}).status_code,409)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)


    def test_reusable_profile_still_needs_new_confirmation_for_each_epoch(self):
        current=dict(release_version=IDENTITY[0],generation=IDENTITY[1],
            auto_process_fresh_mac=False,processing_session_ids=[],model_configured=False,interactive_enabled=False)
        d=SimpleNamespace(stage='test',db=self.db,metadata=lambda:current,release_lease=nullcontext)
        c=self.make(browser=configured_browser_api(d,clear_profile=IDENTITY[1]+':reusable'))
        self.login(c)
        first=self.control('clear_prepare',{})['revision']
        old={'stage':'test','expected_revision':first}
        first_proof=self.prepare(c,CLEAR,old|{'confirmation':'ОЧИСТИТЬ TEST'})
        old_proof=BROWSER|{'x-csrf-token':first_proof['csrf_token']}
        self.assertEqual(c.put(CLEAR,headers=old_proof,json=old).status_code,200)
        new_status=c.get('/api/v1/capabilities?queue_status=1').json()['database_clear']
        self.assertTrue(new_status['can_clear'])
        new={'stage':'test','expected_revision':new_status['revision']}
        self.assertNotEqual(first,new['expected_revision'])
        self.service.capture_message('fixture','two','fixture-user','user','New fictional memory',2,'fixture')
        self.assertEqual(c.put(CLEAR,headers=old_proof,json=new).status_code,403)
        self.assertEqual(c.post(CLEAR+'/prepare',headers=BROWSER,json=new).status_code,400)
        proof=self.prepare(c,CLEAR,new|{'confirmation':'ОЧИСТИТЬ TEST'})
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
        self.assertEqual(c.put(CLEAR,headers=BROWSER|{'x-csrf-token':proof['csrf_token']},json=new).status_code,200)

    def test_test_clear_profile_file_has_fixed_authority_and_strict_contents(self):
        parent=self.db.parent/'config'/'test'
        parent.mkdir(parents=True)
        marker=parent/'profile.json'
        self.assertIsNone(read_test_clear_profile(marker,owner=os.getuid()))
        valid={'version':1,'stage':'test','release_generation':'a'*64,'database_scope':None}
        def store(payload):
            marker.write_text(json.dumps(payload)); marker.chmod(0o644)
        store(valid)
        self.assertEqual(read_test_clear_profile(marker,owner=os.getuid()),'a'*64+':reusable')
        store(valid|{'database_scope':'b'*64})
        self.assertEqual(read_test_clear_profile(marker,owner=os.getuid()),'a'*64+':'+'b'*64)
        for bad in (valid|{'stage':'prod'},valid|{'version':True},valid|{'release_generation':'a'},
                    valid|{'database_scope':'reusable'},valid|{'extra':1}):
            store(bad)
            with self.assertRaises(ValueError):read_test_clear_profile(marker,owner=os.getuid())
        store(valid)
        os.link(marker,parent/'hard')
        with self.assertRaises(ValueError):read_test_clear_profile(marker,owner=os.getuid())
        (parent/'hard').unlink()
        marker.chmod(0o664)
        with self.assertRaises(ValueError):read_test_clear_profile(marker,owner=os.getuid())
        marker.rename(parent/'real')
        marker.symlink_to(parent/'real')
        with self.assertRaises(OSError):read_test_clear_profile(marker,owner=os.getuid())


class OwnerGraphResponseStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_headers_do_not_wait_for_first_graph_generation(self):
        import asyncio
        from threading import Event, Timer
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.routing import Route
        from apps.graph_viewer.server.app import Boundary, events

        generation_released=Event()
        class Manager:
            closed=False
            def sync_generation(self):
                if not generation_released.wait(timeout=2):
                    raise AssertionError('fixture generation was not released')
                return 1,2,'1'*32

        host='fixture.invalid'
        app=Starlette(routes=[Route('/api/v1/events',events)],middleware=[Middleware(Boundary)])
        app.state.public_origin='https://'+host
        app.state.manager=Manager()
        app.state.owner_graph_stream=True
        app.state.owner=SimpleNamespace(session=lambda r:'fixture-csrf'
                                        if r.cookies.get('fixture')=='owner' else None)
        app.state.browser_owner_api=SimpleNamespace(allows=lambda r:r.url.path=='/api/v1/events')
        app.state.owner_snapshot_slots=asyncio.Semaphore(2)
        scope=dict(type='http',asgi=dict(version='3.0',spec_version='2.4'),
                   http_version='1.1',scheme='https',path='/api/v1/events',raw_path=b'/api/v1/events',
                   query_string=b'wire=graph-v1',root_path='',method='GET',
                   headers=[(b'host',host.encode()),(b'cookie',b'fixture=owner'),
                            (b'sec-fetch-site',b'same-origin')],
                   client=('127.0.0.1',9),server=(host,443))
        started=False
        received=asyncio.Event()
        witness={}
        async def receive():
            nonlocal started
            if not started:
                started=True
                return dict(type='http.request',body=b'',more_body=False)
            await received.wait()
            return dict(type='http.disconnect')

        async def send(message):
            if message['type']=='http.response.start':
                witness['status']=message['status']
                witness['sse']=dict(message['headers'])[b'content-type'].startswith(b'text/event-stream')
                witness['headers_before_generation']=not generation_released.is_set()
                generation_released.set()
            elif message.get('body',b'').startswith(b'event: hello\n'):
                witness['hello']=True
                received.set()
                raise OSError('fixture disconnected after hello')

        # Independent watchdog lets the old, blocking implementation finish so
        # failure is an assertion about order, not a hung ASGI request.
        timer=Timer(0.5,generation_released.set)
        timer.start()
        try:
            try:
                await asyncio.wait_for(app(scope,receive,send),timeout=3)
            except OSError:
                if not witness.get('hello'):
                    raise
        finally:
            timer.cancel()
        self.assertEqual(witness,dict(status=200,sse=True,headers_before_generation=True,hello=True))
