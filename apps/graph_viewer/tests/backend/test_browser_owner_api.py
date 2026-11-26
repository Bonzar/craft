"""Real Base Graph owner HTTP boundary and real disposable domain transactions."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI, PREFIX
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
    def make(self, clear=False, **options):
        def project(**page):
            config=SimpleNamespace(processing_session_ids=None,provider='none',auto_process_fresh_mac=False)
            return snapshot(config,identity=lambda:IDENTITY,database=self.db,interactive_enabled=False,
                            clear_ready=clear,can_change=True,**page)
        api=BrowserOwnerAPI(stage='test',perform=self.control,queue_snapshot=project,
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
