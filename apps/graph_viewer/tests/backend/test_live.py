"""Incremental source publishing without reloading the universe."""
import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_service import fixture,EARLY
from apps.graph_viewer.server.manager import GraphManager
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource


class LiveFixture:
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.source_path=self.root/'source.sqlite3'; fixture(self.source_path)
        self.manager=GraphManager(AssertionMemorySource(self.source_path),self.root/'cache')
        await self.manager.start()
    async def asyncTearDown(self):
        await self.manager.stop(); self.tmp.cleanup()
    async def graph(self, selection=None):
        selection=selection or {'mode':'current','at':None,'history':False,'undated':'separate'}
        for _ in range(150):
            graph=await self.manager.ensure(selection)
            if graph: return graph
            await asyncio.sleep(.02)
        self.fail('Graph failed to prepare')


class LiveContract(LiveFixture,unittest.IsolatedAsyncioTestCase):
    async def test_single_append_emits_delta_preserves_old_snapshot_and_ordinals(self):
        old=await self.graph()
        with patch('apps.graph_viewer.server.manager.prepare',side_effect=AssertionError('Full reload is forbidden for append')):
            with sqlite3.connect(self.source_path) as c:
                c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',('new','new','actor','s','session',EARLY,EARLY,'s','{}'))
                c.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',('new-edge','new','unicode','supports',EARLY,'{}'))
                c.execute('UPDATE memory_state SET version=version+2')
            for _ in range(100):
                if self.manager.journal: break
                await asyncio.sleep(.03)
        self.assertTrue(self.manager.journal,'Source append did not publish')
        _,event,data=self.manager.journal[-1]
        self.assertEqual(event,'delta')
        self.assertEqual(data['base_revision'],old.revision)
        self.assertEqual(data['upsert_nodes'][0]['ordinal'],6)
        self.assertEqual(data['upsert_edges'][0]['ordinal'],3)
        new=self.manager.get(data['revision'])
        self.assertEqual(new.manifest['counts']['nodes'],7)
        with old.connect() as c:self.assertEqual(c.execute('select count(*) from nodes').fetchone()[0],6)
    async def test_live_unknown_date_append_updates_explicit_bucket_counts(self):
        old=await self.graph()
        with sqlite3.connect(self.source_path) as c:
            c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',('new-undated','unknown date','a','s','session',None,EARLY,'s','{}'))
            c.execute('UPDATE memory_state SET version=version+1')
        for _ in range(100):
            latest=self.manager.graphs[self.manager.latest[self.manager.key(old.manifest['selector'])]]
            if latest.revision!=old.revision:break
            await asyncio.sleep(.03)
        self.assertEqual(latest.manifest['counts']['undated_known_count'],2)

    async def test_same_size_view_update_changes_source_revision(self):
        first=self.manager.source.revision()
        with sqlite3.connect(self.source_path) as c:
            c.execute('UPDATE memory_views SET definition=? WHERE id=?',(json.dumps({'title':'Changed','query':'Changed','scopes':['s']}),'parent'))
        self.assertNotEqual(self.manager.source.revision(),first)

if __name__=='__main__': unittest.main()

class StreamIsolation(LiveFixture,unittest.IsolatedAsyncioTestCase):
    async def test_known_then_stream_never_receives_late_semantic_events(self):
        from types import SimpleNamespace
        historical=await self.graph({'mode':'known_then','at':'2025-02-01T00:00:00.000000+00:00','history':True,'undated':'separate'})
        current=await self.graph()
        self.manager.emit('delta',{'base_revision':current.revision,'revision':current.revision})
        class Request:
            headers={}
            query_params={'revision':historical.revision}
            calls=0
            async def is_disconnected(self):
                self.calls+=1
                return self.calls>1
        received=[part async for part in self.manager.events(Request())]
        self.assertFalse(any('event: delta' in part for part in received))
    async def test_effective_replacement_transitions_without_a_source_write(self):
        from datetime import datetime,timezone,timedelta
        valid=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.source_path) as c:
            c.execute('UPDATE memory_assertions SET valid_at=? WHERE assertion_id=?',(valid,'future'))
        old=await self.graph()
        with old.connect() as c:self.assertNotEqual(c.execute("SELECT disposition FROM nodes WHERE id='old'").fetchone()[0],'superseded')
        for _ in range(100):
            latest=self.manager.graphs[self.manager.latest[self.manager.key(old.manifest['selector'])]]
            with latest.connect() as c:
                if c.execute("SELECT disposition FROM nodes WHERE id='old'").fetchone()[0]=='superseded':break
            await asyncio.sleep(.03)
        else:self.fail('Future effective replacement never became effective')
        self.assertNotEqual(latest.revision,old.revision)

class SourceAvailability(LiveFixture,unittest.IsolatedAsyncioTestCase):
    async def test_missing_source_reports_unavailable_and_fences_last_graph(self):
        old=await self.graph()
        self.source_path.unlink()
        for _ in range(40):
            if not self.manager.available:break
            await asyncio.sleep(.03)
        self.assertFalse(self.manager.available)
        from apps.graph_viewer.server.common import APIError
        with self.assertRaises(APIError) as denied:
            self.manager.get(old.revision)
        self.assertEqual(denied.exception.status,503)
        self.assertEqual(self.manager.journal[-1][1],'unavailable')
    async def test_atomic_source_replacement_wakes_publisher_even_with_identical_version(self):
        first=self.manager.source.revision()
        replacement=self.root/'replacement.sqlite3';fixture(replacement)
        replacement.replace(self.source_path)
        self.assertNotEqual(self.manager.source.revision(),first)

class StreamRevocation(LiveFixture,unittest.IsolatedAsyncioTestCase):
    async def test_revoked_owner_session_stops_existing_stream(self):
        from types import SimpleNamespace
        graph=await self.graph()
        self.manager.emit('delta',{'base_revision':graph.revision,'revision':graph.revision})
        class Request:
            headers={};query_params={'revision':graph.revision};calls=0
            app=SimpleNamespace(state=SimpleNamespace(owner=SimpleNamespace(session=lambda request:None)))
            async def is_disconnected(self):
                self.calls+=1
                return self.calls>1
        received=[part async for part in self.manager.events(Request())]
        self.assertFalse(any('event: delta' in part for part in received))

class ConcurrentSnapshot(LiveFixture,unittest.IsolatedAsyncioTestCase):
    async def test_write_after_snapshot_is_not_mislabeled_as_already_published(self):
        from apps.graph_viewer.server.incremental import incremental,copy_sources
        with sqlite3.connect(self.source_path) as c:c.execute('PRAGMA journal_mode=WAL')
        old=await self.graph()
        def append(id):
            with sqlite3.connect(self.source_path) as c:
                c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',(id,id,'a','s','session',EARLY,EARLY,'s','{}'))
                c.execute('UPDATE memory_state SET version=version+1')
        append('first-new')
        def concurrent_write(*args,**kwargs):
            copy_sources(*args,**kwargs)
            append('arrived-after-snapshot')
        with patch('apps.graph_viewer.server.incremental.copy_sources',side_effect=concurrent_write):
            new,_=incremental(self.manager.source,old,self.root/'cache',old.manifest['selector'])
        with new.connect() as c:
            self.assertIsNone(c.execute("SELECT id FROM nodes WHERE id='arrived-after-snapshot'").fetchone())
        self.assertNotEqual(new.manifest['source_revision'],self.manager.source.revision(),'Publisher must observe a later write rather than falsely acknowledge it')

class ResetReplayBoundary(LiveFixture,unittest.IsolatedAsyncioTestCase):
    async def test_hard_delete_reset_manifest_cursor_includes_its_reset_event(self):
        old=await self.graph()
        with sqlite3.connect(self.source_path) as c:
            c.execute("DELETE FROM memory_edges WHERE edge_id='proposal-edge'")
            c.execute('UPDATE memory_state SET version=version+1')
        for _ in range(100):
            if self.manager.journal:break
            await asyncio.sleep(.03)
        self.assertTrue(self.manager.journal)
        cursor,event,payload=self.manager.journal[-1]
        self.assertEqual(event,'reset')
        new=await self.graph()
        self.assertNotEqual(new.revision,old.revision)
        self.assertEqual(new.manifest['counts']['edges'],2)
        self.assertEqual(new.manifest['live_cursor'],str(cursor),'Fresh manifest must acknowledge the reset that published this snapshot')

    async def test_fresh_stream_after_reset_does_not_reload_the_same_snapshot_forever(self):
        old=await self.graph()
        with sqlite3.connect(self.source_path) as c:
            c.execute("DELETE FROM memory_edges WHERE edge_id='proposal-edge'")
            c.execute('UPDATE memory_state SET version=version+1')
        for _ in range(100):
            if self.manager.journal:break
            await asyncio.sleep(.03)
        new=await self.graph()
        class Request:
            headers={};calls=0
            query_params={'revision':new.revision,'after':new.manifest['live_cursor']}
            async def is_disconnected(self):
                self.calls+=1
                return self.calls>1
        parts=[part async for part in self.manager.events(Request())]
        self.assertFalse(any('event: reset' in part for part in parts),'Fresh manifest cursor must not replay its own already-applied reset')
