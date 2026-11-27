"""Compare actual local owner route contracts; no live listeners or host state."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.browser_owner_api import configured_browser_api
from apps.graph_viewer.server import queue_dashboard as qd, direct_stream
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_runtime import initialize_empty
from prototypes.assertion_memory.thought_store import ThoughtStore
import native_test_node_binding_proposal as b


async def first_stream_frame(client,path='/api/v1/events',headers=None, raw=False):
    request=client.build_request('GET',path,headers={'Accept-Encoding':'identity', **(headers or {})})
    scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
        'method':'GET','scheme':'https','path':request.url.path,'raw_path':request.url.raw_path.partition(b'?')[0],
        'query_string':request.url.query,'server':(request.url.host,443),'client':('fixture',1024),
        'headers':[(k.lower(),v) for k,v in request.headers.raw],'root_path':''}
    first=[]; received=False; done=asyncio.Event()
    async def receive():
        nonlocal received
        if not received:
            received=True
            return {'type':'http.request','body':b'','more_body':False}
        await done.wait()
        return {'type':'http.disconnect'}
    async def send(message):
        if message['type']=='http.response.body' and message.get('body') and not first:
            first.append(message['body'])
            done.set()
        elif message['type']=='http.response.start':
            if message['status']!=200:
                raise RuntimeError(f"Unexpected stream status: {message['status']}")
    await asyncio.wait_for(client.app(scope,receive,send),2)
    if raw:
        return first[0].decode()
    lines=first[0].decode().splitlines()
    event=next(l for l in lines if l.startswith('event: '))
    payload=next(l for l in lines if l.startswith('data: '))
    return event,json.loads(payload.removeprefix('data: '))


class NativeCarrierParity(TestCase):
    def test_graph_queue_and_existing_transport_parity(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            state=root/'state'; state.mkdir()
            db=state/'fictional.sqlite3'; owner_state=state/'graph-state'
            (root/'deploy.lock').touch(mode=0o600)
            uid=b.os.getuid(); profile=replace(b.TEST,root=root,db=db)
            config=SimpleNamespace(db=db,auto_process_fresh_mac=False,processing_session_ids=[],provider='none')
            stage='test'; origin=b.TEST_GRAPH_ORIGIN; current=('fixture-test','a'*64)
            physical=('b'*40,'c'*64)
            headers={'Origin':origin}
            with MemoryStore(db) as store:
                initialize_empty(store)
                t=ThoughtStore(store)
                left,right=t.publish('synthetic first'),t.publish('synthetic second')
                t.connect(left.id,right.id,'synthetic relation')
            def metadata():
                return {'release_version':current[0],'generation':current[1],
                    'processing_session_ids':[],'auto_process_fresh_mac':False,
                    'model_configured':False,'interactive_enabled':False}
            with (patch.object(b,'TEST',profile),
                  patch('pwd.getpwnam',return_value=SimpleNamespace(pw_uid=uid)),
                  patch.dict(qd.SCOPES,{'test':(profile.account,db)}),
                  patch.object(qd,'_test_activation',return_value=physical),
                  patch.object(direct_stream,'active_test_scope',return_value=physical),
                  patch.object(qd.HostedQueueDashboard,'metadata',lambda _self: metadata())):
                dashboard=b.configured_browser_dashboard('test',b.projection_path('test'),db)
                dashboard.control_lock=root/'deploy.lock'
                direct='https://8.8.8.8'  # In-memory route only; no network.
                old=TestClient(create_app(db,owner_state,password='synthetic owner fixture',
                    public_origin=origin,browser_owner_api=configured_browser_api(dashboard),owner_snapshot_events=True,
                    queue_dashboard=dashboard,synthetic_fixture=True,test_direct_events_origin=direct),base_url=origin)
                self.assertEqual(old.get('/api/v1/graph').status_code,401)
                login=old.post('/api/v1/session',headers=headers,json={'password':'synthetic owner fixture'})
                self.assertEqual(login.status_code,200,login.text)
                admitted=b.ProtectedTestBinding('test','carrier',current,uid,physical,direct)
                def build(admission=admitted):
                    return b.build_existing_test_surface(config,private_port=8893,carrier_port=8891,
                        admitted=admission,runtime_identity=lambda:current)
                native=TestClient(build(),base_url=origin,cookies=old.cookies)
                shadow=TestClient(build(replace(admitted,mode='shadow')),base_url=origin,cookies=old.cookies)
                self.assertEqual(shadow.put('/api/v1/processing/pause',headers=headers,json={}).status_code,404)
                self.assertEqual(shadow.delete('/api/v1/session',headers=headers).status_code,404)
                anon=TestClient(build(),base_url=origin)
                self.assertEqual(anon.get('/api/v1/events').status_code,401)
                self.assertEqual(anon.get('/api/v1/events?wire=graph-v1').status_code,401)
                for extra in ({'queue_dashboard':dashboard},
                              {'existing_test_dashboard':SimpleNamespace(stage='test',db=db)},
                              {'existing_test_dashboard':dashboard,'synthetic_fixture':False}):
                    with self.assertRaises(ValueError):
                        create_app(db,owner_state,public_origin=origin,
                            browser_owner_api=configured_browser_api(dashboard),existing_owner_only=True,**extra)
                with patch.object(qd,'_test_activation',return_value=('stale-revision','stale-archive')):
                    with self.assertRaisesRegex(ValueError,'queue release diverges'):
                        build()
                for client in (old,native):
                    self.assertTrue(client.get('/api/v1/session').json()['authenticated'])
                    caps=client.get('/api/v1/capabilities?queue_status=1')
                    self.assertEqual(caps.status_code,200,caps.text)
                    self.assertTrue(caps.json()['capabilities']['standalone_pause'])
                    self.assertTrue(caps.json()['capabilities']['queue_processing_policy'])
                    pause=client.post('/api/v1/processing/pause/prepare',headers=headers,json={})
                    self.assertEqual(pause.status_code,200,pause.text)
                    policy=client.post('/api/v1/processing/sessions/prepare',headers=headers,json={'format':'policy-v1'})
                    self.assertEqual(policy.status_code,200,policy.text)
                    self.assertEqual(pause.json()['stage'],stage)
                    self.assertEqual(policy.json()['stage'],stage)
                for client in (old,native):
                    self.assertTrue(client.get('/api/v1/capabilities?queue_status=1').json()['capabilities']['live'])
                manifests=[]; stream=[]
                for client in (old,native):
                    with client:
                        for attempt in range(30):
                            r=client.get('/api/v1/graph?mode=current&history=0')
                            if r.status_code!=202:
                                break
                            time.sleep(.02)
                        self.assertEqual(r.status_code,200,r.text)
                        manifest=r.json()
                        evidence=[manifest['counts']['nodes'],manifest['counts']['edges']]
                        for kind in ('nodes','edges') + (('topics',) if 'topics' in manifest else ()):
                            item=manifest[kind]
                            bit=client.get(item['url'])
                            self.assertEqual(bit.status_code,200,kind)
                            self.assertEqual(len(bit.content),item['byte_length'])
                            evidence.append(bit.content)
                        manifests.append(evidence)
                        event=asyncio.run(first_stream_frame(client))
                        stream.append(event)
                        self.assertEqual(event[1]['epoch'],manifest['owner_live_epoch'])
                        queue=asyncio.run(first_stream_frame(client,'/api/v1/events?channel=dashboard',raw=True))
                        self.assertIn('event: resync',queue)
                        stale=asyncio.run(first_stream_frame(client,'/api/v1/events?channel=dashboard',
                            {'Last-Event-ID':'test:0000000000000000:0'},raw=True))
                        self.assertIn('event: resync',stale)
                        self.assertNotEqual(queue.splitlines()[0],'id: test:0000000000000000:0')
                        wire=asyncio.run(first_stream_frame(client,'/api/v1/events?wire=graph-v1'))
                        self.assertEqual(wire[0],'event: hello')
                        self.assertEqual(wire[1]['epoch'],manifest['owner_live_epoch'])
                        self.assertEqual(client.head('/api/v1/events',headers={'Last-Event-ID':'0'}).status_code,400)
                        csrf=client.get('/api/v1/session').json()['csrf_token']
                        ticket=client.post('/api/v1/events-ticket',headers=headers|{'X-CSRF-Token':csrf})
                        self.assertEqual(ticket.status_code,200,ticket.text)
                        self.assertEqual(ticket.json()['epoch'],manifest['owner_live_epoch'])
                        self.assertEqual(ticket.json()['stream_url'],direct+direct_stream.TEST_EDGE_PATH)
                        observer=TestClient(client.app,base_url=origin)  # Existing edge pins the upstream browser Host.
                        self.assertEqual(observer.get('/api/v1/events-direct',headers=headers).status_code,401)
                        frame=asyncio.run(first_stream_frame(observer,'/api/v1/events-direct',
                            headers|{'Authorization':'Bearer '+ticket.json()['ticket']}))
                        self.assertEqual(frame[0],'event: hello')
                        replay=observer.get('/api/v1/events-direct',
                            headers=headers|{'Authorization':'Bearer '+ticket.json()['ticket']})
                        self.assertEqual(replay.status_code,401)
                self.assertEqual(manifests[0],manifests[1])
                self.assertEqual(stream[0][0],stream[1][0])
                self.assertEqual(stream[0][1]['epoch'],stream[1][1]['epoch'])
