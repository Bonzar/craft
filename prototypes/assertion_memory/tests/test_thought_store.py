"""Canonical thoughts share content, history, exact bases and search."""
import tempfile
import unittest

from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore, ThoughtConflict


class ThoughtStoreTest(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryStore(':memory:')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)

    def test_private_and_general_content_share_versions_and_search(self):
        fact = self.store.publish('The kettle is red.', thought_id='fact')
        summary = self.store.publish('Kettles heat water.', question='What is a kettle?',
            bases=[{'thought_id': fact.id, 'version': fact.version}])
        self.assertEqual({x.id for x in self.store.search_text('kettle')}, {fact.id, summary.id})
        updated = self.store.publish('Kettles heat water safely.', thought_id=summary.id,
            question=summary.question, bases=summary.bases, expected_version=1)
        self.assertEqual([x.version for x in self.store.versions(summary.id)], [1, 2])
        self.assertEqual(self.store.get(summary.id).text, updated.text)
        with self.assertRaises(ThoughtConflict):
            self.store.publish('Older result.', thought_id=summary.id, expected_version=1)
        with self.memory.transaction() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM memory_assertions').fetchone()[0], 0)

    def test_pinned_stale_bases_reject_publication_and_links_allow_cycles(self):
        a = self.store.publish('First fact.', thought_id='a')
        b = self.store.publish('Second fact.', thought_id='b', bases=[{'thought_id': 'a', 'version': 1}])
        self.store.publish('Changed fact.', thought_id='a', expected_version=1)
        with self.assertRaises(ThoughtConflict):
            self.store.publish('Stale composition.', bases=[{'thought_id': a.id, 'version': a.version}])
        self.store.connect('a', 'b', 'relates to', source_span={'start': 0, 'end': 7})
        self.store.connect('b', 'a', 'relates to')
        self.assertEqual(len(self.store.links()), 2)
        first_link = self.store.connect('b', 'a', 'relates to')
        self.assertTrue(first_link['created_at'])
        self.assertEqual(self.store.connect('b', 'a', 'relates to')['created_at'], first_link['created_at'])
        self.assertEqual(b.bases[0]['version'], 1)

    def test_unchanged_publication_reuses_version_and_outer_rollback_is_atomic(self):
        a = self.store.publish('A useful fact.', thought_id='a')
        self.assertEqual(self.store.publish(a.text, thought_id='a', expected_version=1), a)
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.memory.transaction(write=True):
                self.store.publish('Will roll back.', thought_id='rollback')
                raise RuntimeError('rollback')
        self.assertIsNone(self.store.get('rollback'))

    def test_deep_bases_expand_iteratively_and_report_bounded_remainder(self):
        previous = self.store.publish('Depth zero.')
        for number in range(1050):
            previous = self.store.publish(f'Depth {number + 1}.',
                bases=[{'thought_id': previous.id, 'version': previous.version}])
        bounded = self.store.expand_bases(previous.id, limit=3)
        self.assertEqual(len(bounded['thoughts']), 3)
        self.assertEqual(len(bounded['remaining']), 1)
        self.assertEqual(len(self.store.expand_bases(previous.id, limit=1100)['thoughts']), 1051)

    def test_competing_connections_do_not_overwrite_same_head(self):
        with tempfile.TemporaryDirectory() as directory:
            first_memory = MemoryStore(directory + '/thoughts.sqlite')
            self.addCleanup(first_memory.close)
            second_memory = MemoryStore(directory + '/thoughts.sqlite')
            self.addCleanup(second_memory.close)
            first, second = ThoughtStore(first_memory), ThoughtStore(second_memory)
            first.publish('Original.', thought_id='shared')
            observed = second.get('shared')
            first.publish('First result.', thought_id='shared', expected_version=1)
            with self.assertRaises(ThoughtConflict):
                second.publish('Second result.', thought_id='shared', expected_version=observed.version)
            self.assertEqual(second.get('shared').text, 'First result.')

    def test_raw_basis_pins_verified_source_span(self):
        from prototypes.assertion_memory.queue import CaptureQueue
        CaptureQueue(self.memory)
        with self.memory.transaction(write=True) as c:
            c.execute('INSERT INTO memory_raw_events VALUES (?,?,?,?,?,?,?,?,?)',
                ('event', 'session', 'turn', 'The kettle is red.', 'user', 'test', 'home', '2026-11-17', '{}'))
        thought = self.store.publish('The kettle is red.', bases=[{'event_id': 'event', 'span': {'start': 4, 'end': 10}}])
        self.assertTrue(thought.bases[0]['content_hash'])
        with self.assertRaises(ValueError):
            self.store.publish('Unsupported.', bases=[{'event_id': 'event', 'span': {'start': 0, 'end': 90}}])


if __name__ == '__main__':
    unittest.main()

class ThoughtLifecycleTest(unittest.TestCase):
    def test_retirement_retains_exact_audit_and_indexed_dependents_but_hides_active_links(self):
        with MemoryStore(':memory:') as memory:
            store = ThoughtStore(memory)
            child = store.publish('Old source.', thought_id='child')
            parent = store.publish('Known parent.', question='What is known?', bases=[{'thought_id': child.id, 'version': 1}])
            store.connect(child.id, parent.id, 'supports')
            self.assertEqual([row.id for row in store.direct_dependents(child.id)], [parent.id])
            store.retire([child.id], 'Verified replacement.',
                {'verification': 'accepted', 'expected_versions': {child.id: 1}})
            self.assertIsNone(store.get(child.id))
            self.assertEqual(store.get(child.id, 1), child)
            self.assertEqual(store.links(), [])
            self.assertEqual([row.id for row in store.direct_dependents(child.id)], [parent.id])
            with self.assertRaises(ThoughtConflict):
                store.publish('Stale derived output.', bases=[{'thought_id': child.id, 'version': 1}])
