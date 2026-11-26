"""Only disposable SQLite fixtures: stale built and persisted projections cannot return."""
import asyncio
import json
import sqlite3
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from apps.graph_viewer.server.common import APIError
from apps.graph_viewer.server.manager import GraphManager
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from apps.graph_viewer.server.projection import prepare
from test_live import LiveFixture

class ResetSafety(LiveFixture,unittest.IsolatedAsyncioTestCase):
    def clear(self):
        from contextlib import nullcontext
        from prototypes.assertion_memory.queue_owner_domain import owner_operations
        reset=owner_operations(database=self.source_path.resolve(),stage='test',
                               release_lease=nullcontext,identity=lambda:('fixture','exact-build'))
        status=reset('clear_prepare',{})
        self.assertTrue(status['can_clear'])
        self.assertTrue(reset('clear_commit',{'expected_revision':status['revision']})['cleared'])

    async def test_sse_historical_reader_crosses_neither_reset_nor_restart(self):
        selection={'mode':'known_then','at':'2025-02-01T00:00:00.000000+00:00','history':True,'undated':'separate'}
        old=await self.graph(selection)
        async def still_open(): return False
        stream=self.manager.events(SimpleNamespace(query_params={'revision':old.revision},headers={},is_disconnected=still_open))
        self.assertIn('event: hello',await anext(stream))
        self.clear()
        reset=await asyncio.wait_for(anext(stream),2)
        self.assertIn('event: reset',reset)
        self.assertIn('database_cleared',reset)
        await stream.aclose()
        with self.assertRaises(APIError):
            self.manager.get(old.revision)
        fresh=await self.graph()
        self.assertEqual(fresh.manifest['counts']['nodes'],0)
        self.assertNotEqual(fresh.directory.parent,old.directory.parent)
        restarted=GraphManager(AssertionMemorySource(self.source_path),self.root/'cache')
        try:
            with self.assertRaises(APIError):
                restarted.get(old.revision)
            self.assertEqual(restarted.get(fresh.revision).manifest['counts']['nodes'],0)
        finally:
            await restarted.stop()

    async def test_purge_removes_private_file_caches_after_reset_without_touching_other_scopes(self):
        from hashlib import sha256
        from apps.graph_viewer.server.database_generation import epoch_name
        old=await self.graph()
        provider=sha256(self.manager.source.provider_id.encode()).hexdigest()
        ir=self.root/'source-ir'/provider
        ir.parent.mkdir(mode=0o700)
        ir.mkdir(mode=0o700)
        retired=ir/('a'*64)
        retired.mkdir(mode=0o700)
        interrupted=ir/'.building-a1b2c3d4'
        interrupted.mkdir(mode=0o700)
        (interrupted/'source.sqlite3').write_bytes(b'incomplete-old-private-source')
        stale=retired/'source.sqlite3'
        stale.write_bytes(b'only-synthetic-old-source')
        stale.chmod(0o600)
        unrelated=self.root/'embedding-models'
        unrelated.mkdir()
        control=unrelated/'weights'
        control.write_bytes(b'never-delete')
        self.clear()
        fresh=await self.graph()
        generation=self.manager.read_generation()
        current=ir/'database-epochs'/epoch_name(generation)/('b'*64)
        for path in (current.parent.parent,current.parent,current):
            path.mkdir(mode=0o700)
        active=current/'source.sqlite3'
        active.write_bytes(b'only-synthetic-current-source')
        active.chmod(0o600)
        for _ in range(25):
            if not old.directory.exists() and not retired.exists():
                break
            self.manager.cleanup_reset(generation)
            await asyncio.sleep(.1)
        self.assertFalse(old.directory.exists())
        self.assertFalse(retired.exists())
        self.assertFalse(interrupted.exists())
        self.assertTrue(fresh.directory.exists())
        self.assertEqual(active.read_bytes(),b'only-synthetic-current-source')
        self.assertEqual(control.read_bytes(),b'never-delete')

    async def test_old_worker_can_finish_io_but_cannot_republish(self):
        arrived=threading.Event(); resume=threading.Event(); olds=[]
        def blocked(*args):
            graph=prepare(*args)
            if not olds:
                olds.append(graph)
                arrived.set()
                if not resume.wait(4): raise AssertionError('old worker would leak')
            return graph
        selection={'mode':'current','at':None,'history':False,'undated':'separate'}
        with patch('apps.graph_viewer.server.manager.prepare',side_effect=blocked):
            try:
                self.assertIsNone(await self.manager.ensure(selection))
                self.assertTrue(await asyncio.to_thread(arrived.wait,3))
                self.clear()
                fresh=await self.graph(selection)
                self.assertEqual(fresh.manifest['counts']['nodes'],0)
            finally:
                resume.set()
            await asyncio.gather(*list(self.manager._epoch_tasks))
        self.assertNotIn(olds[0].revision,self.manager.graphs)
        self.assertIs(self.manager.get(fresh.revision),fresh)
        with self.assertRaises(APIError):
            self.manager.get(olds[0].revision)

class HttpReset(unittest.TestCase):
    def test_owner_old_manifest_binary_and_card_fail_closed_after_clear(self):
        from pathlib import Path
        import tempfile
        import time
        from starlette.testclient import TestClient
        from apps.graph_viewer.server.app import create_app
        from test_service import fixture
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.source_path=root/'source.sqlite3'
            fixture(self.source_path)
            with TestClient(create_app(self.source_path,root/'state',password='owner-test-strong')) as client:
                self.assertEqual(client.post('/api/v1/session',json={'password':'owner-test-strong'},
                                            headers={'origin':'http://testserver'}).status_code,200)
                def manifest():
                    for _ in range(100):
                        response=client.get('/api/v1/graph')
                        self.assertIn(response.status_code,(200,202))
                        if response.status_code==200:
                            return response.json()
                        time.sleep(.02)
                    self.fail('No manifest')
                first=manifest()
                rev=first['revision']
                self.assertEqual(client.get('/api/v1/graph/'+rev+'/nodes.bin').status_code,200)
                ResetSafety.clear(self)
                for suffix in ('nodes.bin','nodes/0'):
                    self.assertEqual(client.get('/api/v1/graph/'+rev+'/'+suffix).status_code,409)
                self.assertIn('database_cleared',client.get('/api/v1/events',params={'revision':rev}).text)
                self.assertEqual(manifest()['counts']['nodes'],0)

    def test_canonical_private_search_source_ir_and_old_projection_are_reclaimed(self):
        from pathlib import Path
        import tempfile
        import time
        from starlette.testclient import TestClient
        from prototypes.assertion_memory.storage import MemoryStore
        from prototypes.assertion_memory.service import MemoryService
        from prototypes.assertion_memory.thought_store import ThoughtStore
        from prototypes.assertion_memory.thought_runtime import initialize_empty
        from apps.graph_viewer.server.app import create_app
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.source_path=root/'synthetic.sqlite3'
            with MemoryStore(self.source_path) as store:
                MemoryService(store)
                self.assertTrue(initialize_empty(store))
                ThoughtStore(store).publish('synthetic-reset-marker')
            state=root/'state'
            token=root/'graph-loopback-only.key'
            token.write_text('g'*64)
            token.chmod(0o600)
            async def watch(_emit):
                await asyncio.Event().wait()
            dashboard=SimpleNamespace(stage='test',db=self.source_path,watch=watch)
            with TestClient(create_app(self.source_path,state,password='owner-test-strong',
                                        bridge_read_token_file=token,queue_dashboard=dashboard)) as client:
                self.assertEqual(client.post('/api/v1/session',json={'password':'owner-test-strong'},
                                            headers={'origin':'http://testserver'}).status_code,200)
                def manifest():
                    for _ in range(100):
                        response=client.get('/api/v1/graph')
                        if response.status_code==200:
                            return response.json()
                        time.sleep(.02)
                    self.fail('No manifest')
                old=manifest()
                self.assertGreater(old['counts']['nodes'],0)
                scope_path='/api/v1/capabilities?database_clear_scope=1'
                self.assertEqual(client.get(scope_path).status_code,404)
                trusted_graph_headers={'Authorization':'Bearer '+'g'*64}
                scope=client.get(scope_path,headers=trusted_graph_headers)
                self.assertEqual(scope.status_code,200)
                self.assertEqual(scope.json()['stage'],'test')
                self.assertRegex(scope.json()['database_clear_scope'],r'^[a-f0-9]{64}$')
                csrf=client.get('/api/v1/session').json()['csrf_token']
                headers={'origin':'http://testserver','x-csrf-token':csrf}
                bookmark={'provider_id':'assertion-memory','object_id':'synthetic-node','object_kind':'node',
                          'label':'synthetic-deleted-bookmark'}
                self.assertEqual(client.put('/api/v1/bookmarks/wipe',json=bookmark,headers=headers).status_code,200)
                self.assertEqual(client.put('/api/v1/bookmarks/keep',json=bookmark|{'provider_id':'unrelated','label':'keep-me'},headers=headers).status_code,200)
                previous=list((state/'source-ir').glob('*/*/source.sqlite3'))
                self.assertTrue(previous)
                old_graph=state/'cache'/old['revision']
                self.assertTrue(old_graph.exists())
                ResetSafety.clear(self)
                self.assertEqual(client.get('/api/v1/graph/'+old['revision']+'/nodes.bin').status_code,409)
                fresh=manifest()
                self.assertEqual(fresh['counts']['nodes'],0)
                self.assertNotEqual(client.get(scope_path,headers=trusted_graph_headers).json(),scope.json())
                self.assertEqual(client.get('/api/v1/session').json()['csrf_token'],csrf)
                shown=client.get('/api/v1/bookmarks').json()['items']
                self.assertEqual([(item['id'],item['label']) for item in shown],[('keep','keep-me')])
                search=client.get('/api/v1/search',params={'q':'synthetic-reset-marker','revision':fresh['revision']})
                self.assertEqual(search.status_code,200)
                self.assertNotIn('synthetic-reset-marker',json.dumps(search.json().get('items',[])))
                for _ in range(40):
                    if not any(x.exists() for x in [old_graph,*previous]):
                        break
                    time.sleep(.1)
                self.assertFalse(any(x.exists() for x in [old_graph,*previous]))
                self.assertTrue(list((state/'source-ir').glob('*/database-epochs/*/*/source.sqlite3')))
