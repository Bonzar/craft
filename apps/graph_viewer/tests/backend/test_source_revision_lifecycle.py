"""Immutable viewer revisions, live invalidation and ledger snapshot coherence."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from apps.graph_viewer.server.projection import prepare, copy_nodes
from apps.graph_viewer.server.incremental import incremental, append_compatible
from apps.graph_viewer.server.manager import GraphManager
from apps.graph_viewer.server.cache_restore import restore_graph
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from revision_fixture import fixture, publish, add_node, ids, CURRENT, BEFORE, AFTER, SUMMARY, logical_source


def saved_bytes(graph):
    return {name:(graph.directory/name).read_bytes() for name in
            ['projection.sqlite3','nodes.bin','edges.bin','topics.json','manifest.json','watermarks.json']}


def original_fixture(path):
    fixture(path, revised=False)
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM memory_assertions WHERE assertion_id='green'")
        c.execute("DELETE FROM memory_edges WHERE edge_id='green-edge'")


class RevisionLive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'source.sqlite3'; original_fixture(self.path)
        self.manager=GraphManager(AssertionMemorySource(self.path),self.root/'cache')
        await self.manager.start()
    async def asyncTearDown(self):
        await self.manager.stop(); self.tmp.cleanup()
    async def graph(self, selection=CURRENT):
        for _ in range(100):
            graph=await self.manager.ensure(selection)
            if graph: return graph
            await asyncio.sleep(.02)
        self.fail('Graph failed to prepare')
    async def changed(self, old):
        for _ in range(100):
            graph=await self.graph(old.manifest['selector'])
            if graph.revision!=old.revision: return graph
            await asyncio.sleep(.02)
        self.fail('Ledger publication did not refresh')
    async def test_withdraw_only_no_state_bump_wakes_and_resets_all_current_perspectives(self):
        old=await self.graph(); binary=saved_bytes(old)
        known=await self.graph(dict(CURRENT,mode='known_now',at=BEFORE,undated='include'))
        pinned=await self.graph(dict(CURRENT,mode='known_then',at=BEFORE))
        before=self.manager.source.revision()
        with sqlite3.connect(self.path) as c:
            publish(c,['round'],{'blue':'withdrawn','round':'retained'})
        self.assertNotEqual(self.manager.source.revision(),before)
        current=await self.changed(old); past_now=await self.changed(known)
        self.assertEqual(ids(current),{'round','direct'})
        self.assertEqual(ids(past_now),{'direct'})
        self.assertEqual(saved_bytes(old),binary)
        self.assertEqual(ids(pinned),{'blue','round','direct'})
        self.assertEqual((await self.graph(pinned.manifest['selector'])).revision,pinned.revision)
        self.assertTrue(self.manager.journal)
        self.assertTrue(all(event=='reset' for _,event,_ in self.manager.journal))
        with current.connect() as c:
            self.assertEqual({r[0] for r in c.execute('SELECT kind FROM edges')},{'supports'})
            self.assertEqual(c.execute('SELECT count(*) FROM status_links').fetchone()[0],0)
    async def test_ledger_append_and_row_delta_matching_state_still_resets(self):
        old=await self.graph()
        with sqlite3.connect(self.path) as c:
            add_node(c,'ordinary')
            publish(c,['round'],{'blue':'withdrawn','round':'retained'},bump=1)
        new=await self.changed(old)
        self.assertEqual(ids(new),{'round','direct','ordinary'})
        self.assertEqual(self.manager.journal[-1][1],'reset')
        self.assertFalse(new.manifest['preparation'].get('incremental',False))
    async def test_conforming_withdraw_only_state_bump_resets(self):
        old=await self.graph()
        with sqlite3.connect(self.path) as c:
            publish(c,['round'],{'blue':'withdrawn','round':'retained'},bump=1)
        self.assertEqual(ids(await self.changed(old)),{'round','direct'})
        self.assertEqual(self.manager.journal[-1][1],'reset')
    async def test_legacy_current_cache_is_rebuilt_even_if_source_hint_matches(self):
        old=await self.graph()
        old.manifest['preparation'].pop('source_membership_policy')
        new=await self.changed(old)
        self.assertEqual(ids(new),ids(old))
        self.assertEqual(self.manager.journal[-1][1],'reset')


class RevisionSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'source.sqlite3'; original_fixture(self.path)
        self.source=AssertionMemorySource(self.path)
    def tearDown(self):
        self.source.close(); self.tmp.cleanup()
    def graph(self, selection=CURRENT):
        return prepare(self.source,self.root/'cache',selection)
    def test_wal_publication_during_prepare_keeps_one_membership_counter_topic_snapshot(self):
        with sqlite3.connect(self.path) as c: c.execute('PRAGMA journal_mode=WAL')
        def concurrent(*args, **kwargs):
            with sqlite3.connect(self.path) as c:
                publish(c,['round'],{'blue':'withdrawn','round':'retained'},bump=1)
            return copy_nodes(*args,**kwargs)
        with patch('apps.graph_viewer.server.projection.copy_nodes',side_effect=concurrent):
            old=self.graph()
        self.assertEqual(ids(old),{'blue','round','direct'})
        self.assertEqual(old.watermarks['state'],1)
        self.assertEqual(old.watermarks['assertion_membership'][1],1)
        self.assertEqual(old.manifest['counts']['undated_known_count'],2)
        self.assertEqual(old.topics()['topics'][0]['member_count'],2)
        self.assertNotEqual(old.manifest['source_revision'],self.source.revision())
        before=logical_source(self.path); new=self.graph()
        self.assertEqual(ids(new),{'round','direct'})
        self.assertEqual(new.watermarks['assertion_membership'][1],2)
        self.assertEqual(new.manifest['counts']['undated_known_count'],1)
        self.assertEqual(new.topics()['topics'][0]['member_count'],1)
        self.assertEqual(before,logical_source(self.path))
        with self.source.connect() as c:
            with self.assertRaises(sqlite3.OperationalError): c.execute('DELETE FROM memory_extraction_runs')
    def test_future_ledger_ownership_watermark_rejects_append_even_when_evidence_fingerprint_same(self):
        selection=dict(CURRENT,mode='known_then',at=BEFORE)
        old=self.graph(selection)
        with sqlite3.connect(self.path) as c:
            add_node(c,'green')
            publish(c,['round','green'],{'blue':'withdrawn','round':'retained','green':'introduced'},bump=1)
        new=self.graph(selection)
        self.assertEqual(old.watermarks['assertion_membership'][2],new.watermarks['assertion_membership'][2])
        self.assertNotEqual(old.watermarks['assertion_membership'][1],new.watermarks['assertion_membership'][1])
        self.assertFalse(append_compatible(old.watermarks,new.watermarks))
        self.assertIsNone(incremental(self.source,old,self.root/'cache',selection))
        self.assertEqual(ids(new),{'blue','round','direct'})
    def test_malformed_cache_preparation_or_readiness_is_rejected_without_crashing(self):
        graph=self.graph(); descriptor=graph.directory/'manifest.json'
        original=json.loads(descriptor.read_text())
        for key in ['preparation','readiness']:
            with self.subTest(key=key):
                manifest=dict(original); manifest[key]=None
                manifest['preparation']=None if key=='preparation' else {}
                descriptor.write_text(json.dumps(manifest))
                self.assertIsNone(restore_graph(self.root/'cache',graph.revision,self.source.provider_id))

    def test_exact_cached_revision_survives_new_publication_and_legacy_is_explicitly_unchecked(self):
        old=self.graph(); frozen=saved_bytes(old)
        with sqlite3.connect(self.path) as c:
            publish(c,['round'],{'blue':'withdrawn','round':'retained'})
        current=self.graph()
        manager=GraphManager(self.source,self.root/'cache')
        restored=manager.get(old.revision)
        self.assertEqual(ids(restored),{'blue','round','direct'})
        self.assertEqual(saved_bytes(restored),frozen)
        self.assertEqual(ids(current),{'round','direct'})
        manifest_path=old.directory/'manifest.json'; manifest=json.loads(manifest_path.read_text())
        manifest['preparation'].pop('source_membership_policy')
        manifest_path.write_text(json.dumps(manifest))
        marks=json.loads((old.directory/'watermarks.json').read_text()); marks.pop('assertion_membership')
        (old.directory/'watermarks.json').write_text(json.dumps(marks))
        legacy_bytes=saved_bytes(old)
        legacy=restore_graph(self.root/'cache',old.revision,self.source.provider_id)
        self.assertIsNotNone(legacy)
        self.assertIn('legacy_source_membership_unchecked',legacy.manifest['readiness'])
        self.assertEqual(saved_bytes(legacy),legacy_bytes)
        self.assertEqual(ids(legacy),{'blue','round','direct'})
        self.assertIsNone(incremental(self.source,legacy,self.root/'cache',CURRENT))

if __name__=='__main__': unittest.main()
