"""Only disposable SQLite fixtures: stale built and persisted projections cannot return."""
import asyncio
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
    def clear(self,generation):
        with sqlite3.connect(self.source_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS memory_database_clear_state(singleton INTEGER PRIMARY KEY,revision TEXT,salt BLOB)')
            db.execute('DELETE FROM memory_edges')
            db.execute('DELETE FROM memory_assertions')
            db.execute('DELETE FROM memory_views')
            db.execute('DELETE FROM memory_view_versions')
            db.execute('UPDATE memory_state SET version=version+1')
            db.execute('INSERT OR REPLACE INTO memory_database_clear_state VALUES(1,?,?)',(generation,b'fixture'))

    async def test_sse_historical_reader_crosses_neither_reset_nor_restart(self):
        selection={'mode':'known_then','at':'2025-02-01T00:00:00.000000+00:00','history':True,'undated':'separate'}
        old=await self.graph(selection)
        async def still_open(): return False
        stream=self.manager.events(SimpleNamespace(query_params={'revision':old.revision},headers={},is_disconnected=still_open))
        self.assertIn('event: hello',await anext(stream))
        self.clear('1'*32)
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
                self.clear('2'*32)
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
                ResetSafety.clear(self,'3'*32)
                for suffix in ('nodes.bin','nodes/0'):
                    self.assertEqual(client.get('/api/v1/graph/'+rev+'/'+suffix).status_code,409)
                self.assertIn('database_cleared',client.get('/api/v1/events',params={'revision':rev}).text)
                self.assertEqual(manifest()['counts']['nodes'],0)
