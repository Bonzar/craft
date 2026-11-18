"""Bounded migration retains legacy identities and audit without replaying raw."""
import json
import unittest
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_store_migration import migrate_batch, rollback_migration


class ThoughtMigrationTest(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryStore(':memory:')
        self.addCleanup(self.memory.close)
        self.memory.append_assertion(assertion_id='fact', text='The kitchen kettle is red.', actor='user',
            source='dialogue:test', episode='test', scope='home', recorded_at='2026-11-17')
        engine = MemoryEngine(self.memory)
        engine.define_view(ViewSpec('kitchen', 'Kitchen', 'What is in the kitchen?', root_ids=('fact',)))
        self.old = engine.build_view('kitchen')
        engine.define_view(ViewSpec('empty', 'Empty', 'An unbuilt technical question?'))
        self.thoughts = ThoughtStore(self.memory)

    def test_batches_resume_preserve_history_and_exclude_empty_definitions(self):
        with self.memory.transaction() as c:
            original = [tuple(row) for row in c.execute('SELECT * FROM memory_view_versions')]
        first = migrate_batch(self.thoughts, limit=1)
        self.assertLessEqual(first['processed'], 1)
        while not first['complete']:
            first = migrate_batch(self.thoughts, limit=1)
            self.assertLessEqual(first['processed'], 1)
        self.assertEqual(self.thoughts.get('fact').text, 'The kitchen kettle is red.')
        kitchen = self.thoughts.get('kitchen')
        self.assertEqual(kitchen.text, self.old.summary)
        self.assertEqual(kitchen.metadata['legacy']['version_id'], self.old.id)
        self.assertEqual(kitchen.bases[0]['thought_id'], 'fact')
        self.assertIsNone(self.thoughts.get('empty'))
        self.assertEqual(migrate_batch(self.thoughts)['processed'], 0)
        with self.memory.transaction() as c:
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM memory_view_versions')], original)
        rollback_migration(self.thoughts)
        self.assertEqual(self.thoughts.list_thoughts(), [])
        self.assertEqual(self.memory.get_assertion('fact').text, 'The kitchen kettle is red.')

    def test_failed_current_and_contentless_built_view_stay_audit_only(self):
        engine = MemoryEngine(self.memory)
        engine.build_view('empty')
        with self.memory.transaction(write=True) as c:
            row = c.execute('SELECT * FROM memory_view_versions WHERE id=?', (self.old.id,)).fetchone()
            snapshot = json.loads(row['snapshot'])
            snapshot['summary'] = 'A failed unsupported draft.'
            snapshot['overview_review'] = {'status': 'unresolved'}
            new = c.execute('INSERT INTO memory_view_versions(view_id,fingerprint,snapshot) VALUES(?,?,?)',
                ('kitchen', 'failed', json.dumps(snapshot))).lastrowid
            c.execute('UPDATE memory_views SET current_id=? WHERE id=?', (new, 'kitchen'))
        while not migrate_batch(self.thoughts, limit=2)['complete']:
            pass
        self.assertIsNone(self.thoughts.get('kitchen'))
        self.assertEqual(len(self.thoughts.versions('kitchen')), 2)
        self.assertEqual(self.thoughts.versions('kitchen')[-1].metadata['legacy']['version_id'], new)
        self.assertIsNone(self.thoughts.get('empty'))
        self.assertEqual(self.thoughts.search_text('unsupported'), [])

    def test_service_scope_roots_with_prose_and_bases_remain_audit_only(self):
        engine = MemoryEngine(self.memory)
        for key, title in [('scope-overview-test', 'Old scope root'), ('service', 'Shared scope overview')]:
            engine.define_view(ViewSpec(key, title, 'What is known in this scope?', root_ids=('fact',)))
            engine.build_view(key)
        while not migrate_batch(self.thoughts, limit=2)['complete']:
            pass
        self.assertIsNone(self.thoughts.get('scope-overview-test'))
        self.assertIsNone(self.thoughts.get('service'))
        self.assertTrue(self.thoughts.versions('scope-overview-test')[0].text)
        self.assertTrue(self.thoughts.versions('service')[0].bases)

    def test_old_codex_tool_raw_basis_migrates_as_audit_without_semantic_admission(self):
        from prototypes.assertion_memory.queue import CaptureQueue
        queue = CaptureQueue(self.memory)
        event = queue.capture_event('legacy-tool', 'one', 'Old exec result.', actor='tool:exec_command',
            source='codex://session/legacy/tool/exec', metadata={'role': 'tool'})
        self.memory.append_assertion(assertion_id='old-tool', text='Old tool interpretation.',
            actor=event.actor, source=event.source, episode=event.session_id, scope=event.scope,
            recorded_at=event.recorded_at, metadata={'event_id': event.id, 'role': 'tool'})
        while not migrate_batch(self.thoughts, limit=2)['complete']:
            pass
        self.assertIsNone(self.thoughts.get('old-tool'))
        old = self.thoughts.get('old-tool', 1)
        self.assertEqual(old.text, 'Old tool interpretation.')
        self.assertEqual(old.bases[0]['event_id'], event.id)
        self.assertTrue(old.bases[0]['content_hash'])
        self.assertEqual(queue.get_event(event.id), event)
        with self.assertRaises(ValueError):
            self.thoughts.publish('New tool interpretation.', bases=[{'event_id': event.id}])

    def test_source_revision_withdrawal_does_not_reactivate_old_meaning(self):
        from prototypes.assertion_memory.queue import CaptureQueue
        queue = CaptureQueue(self.memory)
        event = queue.capture_event('test', 'one', 'Prior claim was withdrawn. Current fact stands.',
            source='test', scope='home')
        self.memory.append_assertion(assertion_id='old', text='Withdrawn old claim.', actor='user',
            source='test', episode='test', scope='home', recorded_at='2026-11-17')
        self.memory.append_edge(edge_id='withdrawn-edge', from_id='fact', to_id='old',
            kind='supports', recorded_at='2026-11-17')
        proof = {'status': 'accepted', 'verifier': 'test', 'reason': 'Checked synthetic source.',
                 'evidence_refs': [{'event_id': event.id, 'start': 0, 'end': len(event.prompt)}]}
        result = {'source_revision': {'schema': 'source-interpretation/v1', 'event_id': event.id,
            'protocol': 'test', 'input_fingerprint': 'test', 'status': 'accepted_complete',
            'assertion_ids': ['fact'], 'reconciliation': [
                {'assertion_id': 'fact', 'decision': 'retained', 'verification': proof},
                {'assertion_id': 'old', 'decision': 'withdrawn', 'verification': proof}]}}
        with self.memory.transaction(write=True) as c:
            for fingerprint, payload in [('old', {'assertion_ids': ['old', 'fact']}), ('new', result)]:
                c.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                    (event.id, fingerprint, '2026-11-17', json.dumps(payload)))
        while not migrate_batch(self.thoughts, limit=2)['complete']:
            pass
        self.assertIsNone(self.thoughts.get('old'))
        self.assertEqual(self.thoughts.links(), [])
        with self.memory.transaction() as c:
            self.assertEqual(c.execute("SELECT exclusion FROM memory_thought_legacy WHERE source_table='memory_edges' AND source_key='withdrawn-edge'").fetchone()[0], 'inactive_link_endpoint')
        self.assertEqual(self.thoughts.versions('old')[0].text, 'Withdrawn old claim.')
        self.assertEqual(self.thoughts.search_text('Withdrawn'), [])
        self.assertIsNotNone(self.thoughts.get('fact'))

    def test_rollback_refuses_to_discard_native_updates(self):
        while not migrate_batch(self.thoughts, limit=20)['complete']:
            pass
        self.thoughts.publish('The kitchen kettle is blue.', thought_id='fact', expected_version=1)
        with self.assertRaisesRegex(RuntimeError, 'native'):
            rollback_migration(self.thoughts)
        self.assertEqual(self.thoughts.get('fact').version, 2)


if __name__ == '__main__':
    unittest.main()
