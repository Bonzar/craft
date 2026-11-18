"""Explicit post-migration purge removes junk without deleting useful history."""
import json
import sqlite3
import unittest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import Thought, ThoughtStore
from prototypes.assertion_memory.thought_store_migration import migrate_batch
from prototypes.assertion_memory.thought_purge import purge_batch


class ThoughtPurgeTest(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryStore(':memory:')
        self.addCleanup(self.memory.close)
        self.queue = CaptureQueue(self.memory)
        self.store = ThoughtStore(self.memory)

    def migrate(self):
        while not migrate_batch(self.store, limit=2)['complete']:
            pass

    def purge(self):
        result = purge_batch(self.store, limit=1)
        for _ in range(100):
            self.assertLessEqual(result['processed'], 1)
            if result['complete']:
                return result
            result = purge_batch(self.store, limit=1)
        self.fail('purge did not complete')

    def test_tool_junk_and_mixed_parent_disappear_but_chat_history_and_grounds_survive(self):
        chat = self.queue.capture_event('s', 'chat', 'Useful user choice.', source='codex://session/s/user')
        tool = self.queue.capture_event('s', 'tool', 'EXEC_JUNK_BODY', actor='tool:exec_command',
            source='codex://session/s/tool/exec', metadata={'role': 'tool'})
        for key, event in [('chat', chat), ('tool', tool)]:
            self.memory.append_assertion(assertion_id=key, text=event.prompt, actor=event.actor,
                source=event.source, episode='s', recorded_at=event.recorded_at, metadata={'event_id': event.id, **event.metadata})
        self.migrate()
        self.store.publish('Useful user choice updated.', thought_id='chat', expected_version=1,
            bases=[{'event_id': chat.id}])
        mixed = Thought('mixed', 1, 'EXEC_JUNK_BODY mixed with choice.', 'Old question?', {},
            [{'thought_id': 'tool', 'version': 1}, {'thought_id': 'chat', 'version': 1}], {}, '2026-01-01')
        with self.memory.transaction(write=True) as c:
            self.store._insert(c, mixed)
        result = self.purge()
        self.assertEqual(result['blockers'], [])
        self.assertIsNone(self.store.get('tool', 1))
        self.assertIsNone(self.store.get('mixed', 1))
        self.assertIsNone(self.queue.get_event(tool.id))
        self.assertIsNone(self.memory.get_assertion('tool'))
        self.assertEqual(len(self.store.versions('chat')), 2)
        self.assertEqual(self.queue.get_event(chat.id), chat)
        for version in self.store.versions('chat'):
            for basis in version.bases:
                if 'event_id' in basis:
                    self.assertIsNotNone(self.queue.get_event(basis['event_id']))
        with self.memory.transaction() as c:
            receipt = json.loads(c.execute("SELECT payload FROM memory_thought_legacy WHERE thought_id='chat'").fetchone()[0])
            self.assertTrue(receipt['compacted'])
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_explicit_tool_ground_in_useful_legacy_provenance_blocks_raw_deletion(self):
        chat = self.queue.capture_event('s', 'chat', 'Useful choice.', source='codex://session/s/user')
        tool = self.queue.capture_event('s', 'tool', 'Old supporting output.', actor='tool:exec_command',
            source='codex://session/s/tool/exec', metadata={'role': 'tool'})
        self.migrate()
        useful = self.store.publish('Useful choice with historical grounds.', bases=[{'event_id': chat.id}],
            metadata={'legacy': {'original': {'metadata': json.dumps({'evidence_refs': [{'event_id': tool.id, 'start': 0, 'end': 3}]})}}})
        result = self.purge()
        self.assertTrue(any(row['kind'] == 'raw_ground_retained' and row['owner'] == tool.id for row in result['protected']))
        self.assertIsNotNone(self.queue.get_event(tool.id))
        self.assertEqual(self.store.get(useful.id), useful)
        retry = purge_batch(self.store, limit=1, retry_blockers=True)
        self.assertLessEqual(retry['processed'], 1)

    def test_failed_legacy_view_owners_are_deleted_with_exact_append_only_trigger_restored(self):
        from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
        engine = MemoryEngine(self.memory)
        self.memory.append_assertion(assertion_id='fact', text='Useful choice.', actor='user',
            source='fixture', episode='s', recorded_at='2026-01-01')
        engine.define_view(ViewSpec('failed-view', 'Failed query', 'Old query?'))
        failed = engine.build_view('failed-view')
        engine.define_view(ViewSpec('useful-view', 'Useful', 'Useful question?', root_ids=('fact',)))
        useful = engine.build_view('useful-view')
        with self.memory.transaction(write=True) as c:
            snapshot = json.loads(c.execute('SELECT snapshot FROM memory_view_versions WHERE id=?', (failed.id,)).fetchone()[0])
            snapshot['overview_review'] = {'status': 'failed'}
            c.execute('UPDATE memory_view_versions SET snapshot=? WHERE id=?', (json.dumps(snapshot), failed.id))
            c.execute("INSERT INTO memory_run_provenance VALUES('run','query','owner',NULL,'2026-01-01','known','{}','{}')")
            for version in (failed, useful):
                check = c.execute('SELECT id FROM memory_view_checks WHERE version_id=?', (version.id,)).fetchone()[0]
                c.execute("INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream) VALUES('run','view','done','2026-01-01',?,?,?,'{}')",
                    (version.view_id, version.id, check))
            trigger = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='memory_run_events_no_delete'").fetchone()[0]
        self.migrate()
        result = self.purge()
        self.assertEqual(result['blockers'], [])
        with self.memory.transaction() as c:
            self.assertIsNone(c.execute('SELECT id FROM memory_view_versions WHERE id=?', (failed.id,)).fetchone())
            self.assertEqual(c.execute('SELECT version_id FROM memory_run_events').fetchone()[0], useful.id)
            self.assertEqual(c.execute("SELECT sql FROM sqlite_master WHERE name='memory_run_events_no_delete'").fetchone()[0], trigger)
        self.assertIsNotNone(self.store.get(useful.view_id))
        with self.assertRaises(sqlite3.DatabaseError):
            with self.memory.transaction(write=True) as c:
                c.execute('DELETE FROM memory_run_events WHERE version_id=?', (useful.id,))

    def test_failed_technical_version_needed_by_useful_history_is_reported_and_retained(self):
        self.migrate()
        failed = Thought('failed', 1, 'Old failed draft.', None, {'kind': 'legacy_view'}, [],
            {'legacy': {'original': {'snapshot': json.dumps({'overview_review': {'status': 'failed'}})}}}, '2026-01-01')
        with self.memory.transaction(write=True) as c:
            self.store._insert(c, failed)
        parent = self.store.publish('Useful account of the failed attempt.', bases=[{'thought_id': failed.id, 'version': 1}])
        result = self.purge()
        self.assertTrue(result['blockers'])
        self.assertIsNotNone(self.store.get(failed.id, 1))
        self.assertEqual(self.store.get(parent.id), parent)
        with self.memory.transaction() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM memory_thought_purge_refs WHERE owner_id=? AND target_id=? AND target_version=?',
                (parent.id, failed.id, 1)).fetchone()[0], 1)
            plan = c.execute('EXPLAIN QUERY PLAN SELECT owner_id FROM memory_thought_purge_refs WHERE target_id=? AND target_version=?', (failed.id, 1)).fetchall()
            self.assertTrue(any('SEARCH' in row[3] for row in plan))


if __name__ == '__main__':
    unittest.main()
