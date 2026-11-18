"""Assembly depth is a storage property, independent of the Python call stack."""
import json
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.storage import MemoryStore


class AssemblyDepthTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.store.append_assertion(assertion_id='approval',
            text='The north gate may open only during the approved pilot.',
            actor='council', source='test://pilot', episode='approval',
            valid_at='2026-11-01', recorded_at='2026-11-02')
        self.engine = MemoryEngine(self.store)

    def chain(self, depth):
        previous = ()
        for level in range(depth):
            key = f'gate-{level}'
            self.engine.define_view(ViewSpec(key, 'Pilot gate operation',
                'Under what conditions may the north gate open?',
                root_ids=('approval',) if not previous else (), depends_on=previous))
            previous = (key,)
        return previous[0]

    def test_deep_chain_build_and_saved_read_preserve_direct_upstream_versions(self):
        root = self.chain(350)
        version = self.engine.build_view(root)
        self.assertEqual([row['id'] for row in version.evidence], ['approval'])
        seen = set()
        while True:
            self.assertNotIn(version.id, seen)
            seen.add(version.id)
            self.assertEqual(self.engine.get_version(version.id), version)
            if not version.upstream:
                break
            self.assertEqual(len(version.upstream), 1)
            version = self.engine.get_version(next(iter(version.upstream.values())))
        self.assertEqual(len(seen), 350)

    def test_shared_child_is_built_once_with_one_frozen_frontier(self):
        self.engine.define_view(ViewSpec('pilot', 'Pilot restriction',
            'What pilot restriction applies?', root_ids=('approval',)))
        for key in ('staff', 'visitors'):
            self.engine.define_view(ViewSpec(key, key, f'When may {key} use the gate?',
                depends_on=('pilot',)))
        self.engine.define_view(ViewSpec('operation', 'Gate operation',
            'When may staff and visitors use the gate?', depends_on=('staff', 'visitors')))
        snapshot = self.engine.capture_snapshot()
        receipts = {}
        with patch.object(self.engine, '_build', wraps=self.engine._build) as build:
            version = self.engine.build_view('operation', _snapshot=snapshot, _receipts=receipts)
        self.assertEqual(build.call_count, 4)
        self.assertEqual([row['id'] for row in version.evidence], ['approval'])
        parents = [self.engine.get_version(key) for key in version.upstream.values()]
        self.assertEqual(parents[0].upstream, parents[1].upstream)
        self.assertEqual(set(receipts), {'pilot', 'staff', 'visitors', 'operation'})
        for receipt in receipts.values():
            self.assertEqual(receipt['query_snapshot'], snapshot.descriptor())
        self.assertEqual(self.store.list_assertions()[0].assertion_id, 'approval')
        self.assertEqual(len(self.store.list_assertions()), 1)

    def relate(self, view_id, *related):
        from prototypes.assertion_memory.question_routes import _ensure_schema, PLAN_PROTOCOL
        plan = {'protocol': PLAN_PROTOCOL, 'related_view_ids': list(related)}
        with self.store.transaction(write=True) as connection:
            _ensure_schema(connection)
            connection.execute('INSERT INTO memory_question_route_versions VALUES (?,?,?,?,?)',
                (view_id, 1, view_id, json.dumps(plan), '2026-11-03'))

    def test_deep_saved_source_index_can_be_read_without_recursive_collection(self):
        child = self.chain(1050)
        self.engine.define_view(ViewSpec('lookup', 'Pilot rule lookup',
            'What is the north gate pilot rule?'))
        self.relate('lookup', child)
        version = self.engine.build_view('lookup')
        self.assertEqual([row['id'] for row in version.evidence], ['approval'])
        receipt = self.engine.latest_check(version)
        lineage = receipt['freshness']['retrieval_dependencies']
        self.assertEqual(len(lineage), 1050)
        self.assertEqual(len({row['view_id'] for row in lineage}), 1050)

    def test_derivation_cycle_is_reported_and_semantic_relation_cycle_is_allowed(self):
        self.engine.define_view(ViewSpec('first', 'First condition', 'First condition?',
            root_ids=('approval',)))
        self.engine.define_view(ViewSpec('second', 'Second condition', 'Second condition?',
            depends_on=('first',)))
        cyclic = replace(self.engine._spec('first'), depends_on=('second',))
        with self.store.transaction(write=True) as connection:
            connection.execute('UPDATE memory_views SET definition=? WHERE id=?',
                (json.dumps(asdict(cyclic)), 'first'))
        with self.assertRaisesRegex(ValueError, 'cyclic assembly dependency: first -> second -> first'):
            self.engine.build_view('first')
        self.assertIsNone(self.engine.inspect_view('first')['current_id'])
        self.store.append_assertion(assertion_id='condition', text='The gate requires pilot approval.',
            actor='council', source='test://conditions', episode='condition', recorded_at='2026-11-02')
        for key, source, target in (('forward', 'approval', 'condition'), ('backward', 'condition', 'approval')):
            self.store.append_edge(edge_id=key, from_id=source, to_id=target,
                kind='depends_on', recorded_at='2026-11-03')
        self.engine.define_view(ViewSpec('semantic', 'Gate requirements',
            'What gate requirements apply?', root_ids=('approval',)))
        version = self.engine.build_view('semantic')
        self.assertEqual({row['id'] for row in version.evidence}, {'approval', 'condition'})
        self.assertEqual({row['id'] for row in version.edges}, {'forward', 'backward'})

    def test_cyclic_source_indexes_retain_unique_sources(self):
        self.engine.define_view(ViewSpec('one', 'Pilot rule', 'What pilot rule applies?',
            root_ids=('approval',)))
        self.engine.define_view(ViewSpec('two', 'Gate rule', 'What gate rule applies?'))
        self.relate('one', 'two')
        self.relate('two', 'one')
        version = self.engine.build_view('one')
        self.assertEqual([row['id'] for row in version.evidence], ['approval'])

    def test_model_receives_direct_verified_child_not_the_stored_ancestry(self):
        from prototypes.assertion_memory.model import format_prompt
        requests = []
        def provider(request):
            requests.append(request)
            if request.task.startswith('Review'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims',
                    'reason': 'The exact approved-pilot condition is retained.',
                    'issues': [], 'context_requests': [], 'unknown_needs': []})
            return 'The north gate may open only during the approved pilot.'
        self.engine = MemoryEngine(self.store, model=provider, verify_overviews=True)
        root = self.chain(24)
        version = self.engine.build_view(root)
        writers = [request for request in requests if not request.task.startswith('Review')]
        self.assertEqual(len(writers), 24)
        self.assertLess(max(len(format_prompt(request)) for request in writers), 18000)
        for request in writers[1:]:
            children = [row for row in request.records if row.get('role') == 'verified_view']
            self.assertEqual(len(children), 1)
            self.assertNotIn('provenance', children[0])
            self.assertFalse(any(row.get('text') == self.store.get_assertion('approval').text
                                 for row in request.records))
        self.assertIn('only during the approved pilot', version.summary)
        self.assertEqual([row['id'] for row in version.evidence], ['approval'])

    def test_write_during_descendant_generation_does_not_change_frozen_parent_inputs(self):
        calls = []
        def provider(request):
            if not calls:
                self.store.append_assertion(assertion_id='late',
                    text='The north gate pilot now requires a staffed desk.',
                    actor='council', source='test://late', episode='late',
                    valid_at='2026-11-03', recorded_at='2026-11-04')
            calls.append(request)
            return 'The gate follows the applicable pilot conditions.'
        self.engine = MemoryEngine(self.store, model=provider)
        root = self.chain(3)
        receipts = {}
        version = self.engine.build_view(root, _receipts=receipts)
        self.assertEqual([row['id'] for row in version.evidence], ['approval'])
        frontiers = [receipt['query_snapshot'] for receipt in receipts.values()]
        self.assertTrue(all(frontier == frontiers[0] for frontier in frontiers))
        self.assertTrue(all(receipt['freshness']['refresh_required'] for receipt in receipts.values()))
        refreshed = self.engine.build_view(root)
        self.assertEqual({row['id'] for row in refreshed.evidence}, {'approval', 'late'})
        self.assertEqual([row['id'] for row in self.engine.get_version(version.id).evidence], ['approval'])


if __name__ == '__main__':
    unittest.main()
