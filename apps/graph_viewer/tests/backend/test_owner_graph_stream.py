"""No host data: async fragmented graph-stream contract tests."""
import asyncio
from collections import deque
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from starlette.datastructures import QueryParams,Headers
from apps.graph_viewer.server.common import APIError,selector
from apps.graph_viewer.server.database_generation import epoch_name
from apps.graph_viewer.server import owner_graph_stream as w

A='1'*32
B='2'*32
GEN=(1,2,'3'*32)
SEL=selector({})

class Manager:
    def __init__(self):
        self._generation=GEN
        self.closed=False
        self.latest={self.key(SEL):A}
        self.graphs={A:self.graph(A)}
        self.cursor=0
        self.journal=deque(maxlen=256)
    def graph(self,r):
        return SimpleNamespace(revision=r,manifest={'revision':r,'selector':SEL})
    def key(self,s):return json.dumps(s,sort_keys=True)
    async def ensure(self,s):return self.graphs[self.latest[self.key(s)]]
    def sync_generation(self):return self._generation
    def append(self):
        self.cursor=1
        self.graphs[B]=self.graph(B);self.latest[self.key(SEL)]=B
        self.journal.append((1,'delta',{'base_revision':A,'revision':B,'cursor':'1',
                           'upsert_nodes':[{'id':'fictional','ordinal':0}]}))

class Request:
    method='GET'
    def __init__(self,manager,query='',headers=(),auth=True,enabled=True):
        owner=SimpleNamespace(session=lambda _: 'validated' if auth else None)
        self.app=SimpleNamespace(state=SimpleNamespace(owner=owner,owner_graph_stream=enabled,
              owner_graph_stream_key=b'synthetic fixture key only',manager=manager,
              owner_snapshot_slots=asyncio.Semaphore(1),public_origin='https://test.invalid'))
        self.cookies={w.COOKIE:'test-opaque-owner-session'}
        self.query_params=QueryParams('wire=graph-v1'+query)
        self.headers=Headers(dict(headers))
        self.scope={'raw_path':b'/api/v1/events','query_string':str(self.query_params).encode()}
    async def is_disconnected(self):return False

def frame(raw):
    event,data,_=raw.split(b'\n',2)
    return event.removeprefix(b'event: ').decode('ascii'),json.loads(data.removeprefix(b'data: '))

class UnifiedGraphStream(IsolatedAsyncioTestCase):
    async def test_cold_snapshot_atomic_hashes_and_delta_during_baseline_are_same_stream(self):
        graph=Manager()
        data={'nodes.bin':b'n'*100000,'edges.bin':b'e','topics.json':b'{"revision":"'+A.encode()+b'"}','identities.json':b'["fictional"]'}
        def chunked(_,name):
            return (data[name][i:i+w.CHUNK] for i in range(0,len(data[name]),w.CHUNK))
        with patch.object(w,'_files',side_effect=chunked), patch.object(w,'_identities',side_effect=lambda g:chunked(g,'identities.json')):
            result=await w.owner_graph_events(Request(graph))
            self.assertEqual(result.headers['x-accel-buffering'],'no')
            self.assertEqual(result.media_type,'text/event-stream')
            iterator=result.body_iterator
            try:
                kind,payload=frame(await anext(iterator))
                self.assertEqual((kind,payload),('hello',{'protocol':'graph-v1','epoch':epoch_name(GEN)}))
                kind,payload=frame(await anext(iterator))
                self.assertEqual((kind,payload['revision'],payload['cursor']),('snapshot-start',A,'0'))
                graph.append() # committed while immutable baseline bytes transfer
                offsets={p:0 for p in data}
                while True:
                    kind,payload=frame(await anext(iterator))
                    if kind=='snapshot-end':break
                    self.assertEqual(kind,'snapshot-chunk')
                    segment=payload['name']
                    raw=w.base64.b64decode(payload['base64'],validate=True)
                    self.assertLessEqual(len(raw),w.CHUNK)
                    self.assertEqual(payload['offset'],offsets[segment])
                    self.assertEqual(raw,data[segment][offsets[segment]:offsets[segment]+len(raw)])
                    offsets[segment]+=len(raw)
                self.assertEqual(payload['segments'],{k:{'bytes':len(v),'sha256':sha256(v).hexdigest()} for k,v in data.items()})
                kind,payload=frame(await anext(iterator))
                self.assertEqual((kind,payload['base_revision'],payload['revision'],payload['cursor']),('delta',A,B,'1'))
                self.assertEqual(frame(await anext(iterator))[0],'caught-up')
            finally:await iterator.aclose()

    async def test_warm_chain_has_no_baseline_and_expired_returns_same_stream_reset(self):
        graph=Manager();graph.append()
        for epoch,expected in [(epoch_name(GEN),'delta'),('f'*64,'reset')]:
            req=Request(graph,f'&epoch={epoch}&revision={A}&after=0')
            iterator=(await w.owner_graph_events(req)).body_iterator
            try:
                self.assertEqual(frame(await anext(iterator))[0],'hello')
                self.assertEqual(frame(await anext(iterator))[0],expected)
                if expected=='reset':
                    self.assertEqual(frame(await anext(iterator))[0],'snapshot-start')
            finally:await iterator.aclose()

    async def test_unauth_bridge_wrong_origin_ambiguous_checkpoint_and_scope_rotation(self):
        manager=Manager()
        for req in (Request(manager,auth=False),Request(manager,enabled=False),
                    Request(manager,headers=(('Authorization','Bearer synthetic'),)),
                    Request(manager,headers=(('Sec-Fetch-Site','cross-site'),)),
                    Request(manager,headers=(('Origin','https://foreign.invalid'),))):
            with self.assertRaises(APIError) as error:await w.owner_graph_events(req)
            self.assertEqual(error.exception.status,401)
        for q in ('&revision='+A,'&wire=graph-v1','&nonce=secret','&epoch='+'a'*64+'&revision='+A+'&after=-1'):
            with self.assertRaises(APIError) as error:await w.owner_graph_events(Request(manager,q))
            self.assertEqual(error.exception.status,400)
        req=Request(manager)
        scope=w.session_scope(req)
        self.assertEqual(w.session_scope(req),scope)
        self.assertNotEqual(w.session_scope(req,'different-session'),scope)
        self.assertNotIn(req.cookies[w.COOKIE],scope[0])

    async def test_asgi_sends_first_frame_before_slow_snapshot_and_does_not_gzip_sse(self):
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.middleware.gzip import GZipMiddleware
        from starlette.routing import Route
        manager=Manager()
        ready=asyncio.Event()
        observed=[]
        async def ensure(s):
            await ready.wait()
            return manager.graphs[A]
        manager.ensure=ensure
        app=Starlette(routes=[Route('/api/v1/events',w.owner_graph_events)],
            middleware=[Middleware(GZipMiddleware,minimum_size=24)])
        app.state.owner_graph_stream=True
        app.state.owner_snapshot_slots=asyncio.Semaphore(1)
        app.state.owner=SimpleNamespace(session=lambda req:'validated')
        app.state.manager=manager; app.state.public_origin='https://test.invalid'
        async def receive():
            await asyncio.sleep(10)
            return {'type':'http.disconnect'}
        async def send(msg):
            observed.append(msg)
            if msg['type']=='http.response.body' and msg.get('body'):
                self.assertFalse(ready.is_set())
                raise OSError('fixture client closed after live first-byte witness')
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
            'method':'GET','scheme':'https','path':'/api/v1/events','raw_path':b'/api/v1/events',
            'query_string':b'wire=graph-v1','headers':[(b'accept-encoding',b'gzip')],
            'server':('test.invalid',443),'client':('127.0.0.1',321)}
        from starlette.requests import ClientDisconnect
        with self.assertRaises(ClientDisconnect):
            await asyncio.wait_for(app(scope,receive,send),timeout=2)
        headers=dict(observed[0]['headers'])
        self.assertEqual(headers[b'x-accel-buffering'],b'no')
        self.assertNotIn(b'content-encoding',headers)
        self.assertEqual(frame(observed[1]['body'])[0],'hello')

    async def test_late_logout_and_disconnection_stop_data_without_expensive_read_ahead(self):
        for use_logout in (True,False):
            graph=Manager()
            state={'owner':True,'disconnected':False}
            request=Request(graph)
            request.app.state.owner.session=lambda _: 'validated' if state['owner'] else None
            async def disconnected():return state['disconnected']
            request.is_disconnected=disconnected
            loaded=[]
            def files(_,name):
                for index in range(4):
                    loaded.append((name,index))
                    yield b'n'*w.CHUNK
            with patch.object(w,'_files',side_effect=files):
                stream=(await w.owner_graph_events(request)).body_iterator
                try:
                    self.assertEqual(frame(await anext(stream))[0],'hello')
                    self.assertEqual(frame(await anext(stream))[0],'snapshot-start')
                    self.assertEqual(loaded,[])
                    self.assertEqual(frame(await anext(stream))[0],'snapshot-chunk')
                    self.assertEqual(loaded,[('nodes.bin',0)])
                    # Simulate revocation/disconnect while downstream is not
                    # driving the generator, before the next requested frame.
                    if use_logout:state['owner']=False
                    else:state['disconnected']=True
                    self.assertEqual(loaded,[('nodes.bin',0)])
                    with self.assertRaises(StopAsyncIteration):await anext(stream)
                    self.assertEqual(loaded,[('nodes.bin',0)])
                finally:await stream.aclose()

    async def test_aggregate_baseline_exceeds_gateway_cap_but_enforces_own_bound(self):
        chunk=b'x'*w.CHUNK
        valid_topics=b'{"revision":"'+A.encode()+b'"}'
        def files(_,name):
            if name=='nodes.bin':
                for _ in range(60):yield chunk
            elif name=='topics.json':yield valid_topics
        for limit,want in ((64*1024*1024,'snapshot-end'),(2_500_000,'unavailable')):
            graph=Manager()
            with patch.object(w,'_files',side_effect=files),patch.object(w,'_identities',side_effect=lambda _: (part for part in (b'[]',))),patch.object(w,'BASE_LIMIT',limit):
                stream=(await w.owner_graph_events(Request(graph))).body_iterator
                try:
                    while True:
                        raw=await anext(stream)
                        self.assertLessEqual(len(raw),w.FRAME_LIMIT)
                        kind,payload=frame(raw)
                        if kind in ('snapshot-end','unavailable'):
                            self.assertEqual(kind,want)
                            if want=='snapshot-end':
                                self.assertGreater(payload['segments']['nodes.bin']['bytes'],2_500_000)
                            break
                finally:await stream.aclose()

    async def test_snapshot_and_delta_project_only_graph_fields(self):
        private='PRIVATE_CARD_TEXT_SENTINEL'
        m=Manager()
        m.graphs[A].manifest['text']=private
        m.graphs[A].manifest['session_filter']={'session_id':'logical:fixture','overview_history':{'reason':private}}
        topics={'revision':A,'topics':[{'index':0,'id':'topic:fixture','label':private,'full_title':private,
                'query':private,'definition_title':private,'body':private,'naming_status':'legacy_unreviewed',
                'source_ref':{'view_id':'fixture','source_ref_text':private}}],
                'links':[{'parent':'fixture','child':'fixture','relation':'fixture','provenance':{'quote':private}}],
                'unknowns':[private], 'summary':private}
        with patch.object(w,'_files',return_value=iter([w._json(topics)])):
            cleaned=json.loads(b''.join(w._topics(m.graphs[A])))
        self.assertEqual(cleaned['topics'][0]['label'],'Тема 1')
        self.assertNotIn(private,w._json(cleaned).decode())
        self.assertNotIn(private,w._json(w.projection_manifest(m.graphs[A],epoch_name(GEN),0)).decode())
        m.append()
        p=m.journal[0][2];p['text']=private;p['upsert_nodes'][0]['text']=private
        p['topic_changes']=topics['topics']
        chain=w.retained(m,SEL,A,0,epoch_name(GEN))
        self.assertNotIn(private,w._json(chain[2]).decode())
        self.assertEqual(chain[2][0]['manifest']['live_cursor'],'1')
        m.graphs[B].manifest['counts']={'fixture':w.FRAME_LIMIT*'x'}
        self.assertIsNone(w.retained(m,SEL,A,0,epoch_name(GEN)))

    async def test_verified_stage_only_when_existing_bound_browser_authority_names_it(self):
        req=Request(Manager())
        self.assertNotIn('graph_cache_stage',w.graph_cache_fields(req))
        req.app.state.browser_owner_api=SimpleNamespace(stage='test')
        info=w.graph_cache_fields(req)
        self.assertIs(info['owner_live_graph_v1'],True)
        self.assertEqual(info['graph_cache_stage'],'test')
        self.assertRegex(info['graph_cache_scope'],r'^[a-f0-9]{64}$')

    async def test_logout_while_chunk_is_read_never_emits_chunk_and_cancellation_closes(self):
        from threading import Event
        for cancel in (False,True):
            began=asyncio.Event()
            released=Event()
            closed=asyncio.Event()
            loop=asyncio.get_running_loop()
            req=Request(Manager())
            owner=True
            req.app.state.owner.session=lambda _: 'validated' if owner else None
            def files(_,name):
                try:
                    loop.call_soon_threadsafe(began.set)
                    if not released.wait(2):raise RuntimeError('fixture read timed out')
                    yield b'secret between read and logout'
                finally:loop.call_soon_threadsafe(closed.set)
            with patch.object(w,'_files',side_effect=files):
                stream=(await w.owner_graph_events(req)).body_iterator
                self.assertEqual(frame(await anext(stream))[0],'hello')
                self.assertEqual(frame(await anext(stream))[0],'snapshot-start')
                reading=asyncio.create_task(anext(stream))
                try:
                    await asyncio.wait_for(began.wait(),1)
                    owner=False
                    if cancel:
                        reading.cancel()
                        with self.assertRaises(asyncio.CancelledError):await reading
                        self.assertFalse(closed.is_set())
                    else:
                        released.set()
                        with self.assertRaises(StopAsyncIteration):await reading
                finally:
                    released.set()
                    await stream.aclose()
                await asyncio.wait_for(closed.wait(),1)

    async def test_same_cookie_cannot_reuse_cache_after_generation_change_or_failed_read(self):
        manager=Manager()
        req=Request(manager)
        first=w.graph_cache_fields(req)
        self.assertEqual(first['graph_cache_epoch'],epoch_name(GEN))
        self.assertRegex(first['graph_cache_scope'],r'^[a-f0-9]{64}$')
        manager._generation=(1,2,'4'*32)
        actual=w.graph_cache_fields(req)
        self.assertEqual(actual['graph_cache_epoch'],epoch_name(manager._generation))
        self.assertNotEqual(actual['graph_cache_scope'],first['graph_cache_scope'])
        for failure in (None,APIError('source_unavailable','fixture',503),('invalid',),(1,2,'invalid')):
            def read():
                if isinstance(failure,Exception):raise failure
                return failure
            with patch.object(manager,'sync_generation',side_effect=read):
                self.assertEqual(w.graph_cache_fields(req),{'owner_live_graph_v1':True})
