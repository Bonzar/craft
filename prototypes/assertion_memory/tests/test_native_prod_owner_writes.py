"""Owner-facing native PROD effects on synthetic DBs, never provision a host."""
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from starlette.testclient import TestClient

from apps.graph_viewer.server.auth import COOKIE, OwnerStore
from prototypes.assertion_memory import base_browser_surface as surface
from prototypes.assertion_memory import graph_node_removal as nodes, queue_item_removal as items
from prototypes.assertion_memory.capture_batch import authorized_machine_batch, capture
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_graph import export_graph
from prototypes.assertion_memory.thought_runtime import initialize_empty
from prototypes.assertion_memory.thought_store import ThoughtStore

CAP='/api/v1/capabilities'
NODE='/api/v1/processing/graph-nodes/remove'
ITEM='/api/v1/processing/items/remove'
CLEAR='/api/v1/processing/database/clear-v2'
BATCH='b'*64
OWNER_PWD='synthetic only'


class NativeProdOwner(TestCase):
    def fixture(self, *, authority='exact', batch=False):
        root=Path(self.enterContext(TemporaryDirectory()))
        path=root/'state'/'fictional.sqlite3'
        path.parent.mkdir()
        config=ServiceConfig(db=str(path),provider='none',processing_session_ids=[])
        packet=None
        with MemoryStore(path) as store:
            initialize_empty(store)
            MemoryService(store).capture_message('fictional','m','qa','user','Independent original',1,'fixture')
            thoughts=ThoughtStore(store)
            target=thoughts.publish('Fictional selected thought')
            thoughts.publish('Fictional survivor')
            if batch:
                service=config.service(store)
                session='qa-native-prod-batch'
                stream=service.ingest_register('isolated-producer',session)
                packet=[dict(session_id=session,message_id=f'b{i}',actor_id=role,role=role,
                    text=f'Fictional {role}',source=f'fixture://qa/{i}',
                    delivery={k:stream[k] for k in ('stream_id','epoch')}|{'seq':i})
                    for i,role in enumerate(('user','assistant'),1)]
                with authorized_machine_batch():
                    capture(service,BATCH,packet)
        other=root/'other.sqlite3'
        with MemoryStore(other) as store:
            initialize_empty(store)
            ThoughtStore(store).publish('Separate fixture DB')
        owner=OwnerStore(path.parent/'graph-state',OWNER_PWD)
        cookie,_=owner.login(OWNER_PWD,'qa-lane')
        profile=surface.LANES['prod']
        lane=replace(profile,base=replace(profile.base,root=root,db=path))
        readiness={'ok':True}
        def private(*_):
            self.fail('Native owner route must not call private REST/HMAC endpoint')
        private_queue=SimpleNamespace(queue_pause_ready=lambda:readiness['ok'],
            queue_clear_database=other if authority=='foreign' else path,
            queue_clear_route=private,
            queue_item_routes={items.PATH:private,nodes.PATH:private,nodes.PREVIEW_PATH:private})
        if authority=='missing':
            private_queue=None
        with patch.object(surface,'LANES',dict(surface.LANES)|{'prod':lane}):
            app=surface.base_browser_app('prod',config=config,private_port=lane.private_port,
                verified_identity=lambda:('fixture-release','a'*64),release_lease=nullcontext,
                interactive=False,private_owner_queue=private_queue)
        client=self.enterContext(TestClient(app,base_url=lane.origin))
        self.assertNotIn('owner_graph_node_removal',client.get(CAP).json())
        client.cookies.set(COOKIE,cookie)
        return client,{'Origin':lane.origin},readiness,config,target,packet,other

    def queue(self, client):
        r=client.get(CAP+'?queue_status=1')
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def batch_card(self, client):
        q=self.queue(client)
        return next((row for queue in q['queues'] if queue['id']=='source'
                     for row in queue['items'] if row['id']=='batch:'+BATCH),None)

    def clear_post(self, client, headers, *, stage='PROD'):
        csrf=client.get('/api/v1/session').json()['csrf_token']
        return client.post(CLEAR,headers=headers|{'X-CSRF-Token':csrf},
                           json={'confirmation':'ОЧИСТИТЬ '+stage})

    def test_bound_clear_executes_only_after_native_owner_confirmation(self):
        c,h,ready,config,_,_,other=self.fixture()
        self.assertTrue(self.queue(c)['capabilities']['owner_database_clear_post'])
        self.assertEqual(c.post(CLEAR,headers=h,json={'confirmation':'ОЧИСТИТЬ PROD'}).status_code,403)
        self.assertEqual(self.clear_post(c,h,stage='TEST').status_code,400)
        ready['ok']=False
        self.assertNotIn('owner_database_clear_post',self.queue(c)['capabilities'])
        self.assertNotIn('owner_graph_node_removal',c.get(CAP).json())
        self.assertEqual(self.clear_post(c,h).status_code,409)
        with MemoryStore(config.db) as store:
            self.assertEqual(len(export_graph(ThoughtStore(store))['nodes']),2)
        ready['ok']=True
        completed=self.clear_post(c,h)
        self.assertEqual(completed.status_code,200,completed.text)
        self.assertTrue(completed.json()['cleared'])
        with MemoryStore(config.db) as store:
            self.assertEqual(export_graph(ThoughtStore(store))['nodes'],[])
        with MemoryStore(other) as store:
            self.assertEqual(len(export_graph(ThoughtStore(store))['nodes']),1)

    def test_signed_batch_and_graph_deletion_use_only_current_bound_database(self):
        c,h,ready,config,target,packet,other=self.fixture(batch=True)
        body=dict(stage='prod',thought_id=target.id,thought_version=target.version)
        self.assertEqual(c.get(CAP).json()['owner_graph_node_removal'],{'version':1,'stage':'prod'})
        prepared=c.post(NODE+'/prepare',headers=h,json=body)
        self.assertEqual(prepared.status_code,200,prepared.text)
        proof=prepared.json(); action=body|{'expected_revision':proof['revision']}
        self.assertEqual(c.put(NODE,headers=h,json=action).status_code,403)
        ready['ok']=False
        self.assertNotIn('owner_graph_node_removal',c.get(CAP).json())
        self.assertEqual(c.put(NODE,headers=h|{'X-CSRF-Token':proof['csrf_token']},json=action).status_code,409)
        ready['ok']=True
        proof=c.post(NODE+'/prepare',headers=h,json=body).json()
        receipt=c.put(NODE,headers=h|{'X-CSRF-Token':proof['csrf_token']},
                      json=body|{'expected_revision':proof['revision']})
        self.assertEqual(receipt.status_code,200,receipt.text)
        self.assertTrue(receipt.json()['removed'])
        row=self.batch_card(c)
        request=dict(stage='prod',queue_id='source',item_id=row['id'],expected_revision=row['remove_revision'])
        t=c.post(ITEM+'/prepare',headers=h,json=request).json()['csrf_token']
        self.assertEqual(self.batch_card(c)['member_count'],2)
        ready['ok']=False
        self.assertNotIn('remove_revision',self.batch_card(c))
        self.assertEqual(c.put(ITEM,headers=h|{'X-CSRF-Token':t},json=request).status_code,409)
        ready['ok']=True
        t=c.post(ITEM+'/prepare',headers=h,json=request).json()['csrf_token']
        receipt=c.put(ITEM,headers=h|{'X-CSRF-Token':t},json=request)
        self.assertEqual(receipt.status_code,200,receipt.text)
        self.assertIsNone(self.batch_card(c))
        with MemoryStore(config.db) as store:
            self.assertEqual(len(export_graph(ThoughtStore(store))['nodes']),1)
            with authorized_machine_batch(),self.assertRaisesRegex(ValueError,'cannot replay'):
                capture(config.service(store),BATCH,packet)
            with store.transaction() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
        with MemoryStore(other) as store:
            self.assertEqual(len(export_graph(ThoughtStore(store))['nodes']),1)

    def test_missing_or_foreign_authority_does_not_offer_or_perform_new_actions(self):
        for authority in ('missing','foreign'):
            with self.subTest(authority=authority):
                c,h,_,_,target,_,_=self.fixture(authority=authority,batch=True)
                self.assertNotIn('owner_database_clear_post',self.queue(c)['capabilities'])
                self.assertNotIn('owner_graph_node_removal',c.get(CAP).json())
                self.assertEqual(self.clear_post(c,h).status_code,404)
                self.assertEqual(c.post(NODE+'/prepare',headers=h,json=dict(stage='prod',
                    thought_id=target.id,thought_version=target.version)).status_code,404)
                self.assertNotIn('remove_revision',self.batch_card(c))
