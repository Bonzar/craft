"""Canonical graph snapshots preserve version binding and documentary ancestry."""
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_graph import export_graph, version_node_id
from prototypes.assertion_memory.thought_store import ThoughtStore


class ThoughtGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'graph.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.queue = CaptureQueue(self.memory)

    def test_uniform_nodes_pin_historical_basis_and_keep_semantic_cycles(self):
        private = self.store.publish('This kettle heats water.', origin={'session_id': 'session-one'})
        general = self.store.publish('A kettle heats water.', question='What does a kettle do?',
            bases=[{'thought_id': private.id, 'version': 1, 'meaning': 'A particular kettle example'}])
        self.store.connect(private.id, general.id, 'is an example of')
        self.store.connect(general.id, private.id, 'has an example')
        latest = self.store.publish('This kettle also has a lid.', thought_id=private.id, expected_version=1)
        # Legacy semantic tables are audit only and cannot leak into this graph.
        self.memory.append_assertion(assertion_id='old-service-root', text='Unrelated old root',
            actor='fixture', source='fixture', episode='fixture', recorded_at='2026-11-17')
        graph = export_graph(self.store)
        nodes = {row['id']: row for row in graph['nodes']}
        self.assertEqual(len(nodes), 3)
        self.assertEqual({row['entity_type'] for row in nodes.values()}, {'thought'})
        self.assertIn(version_node_id(private.id, 1), nodes)
        self.assertIn(version_node_id(latest.id, 2), nodes)
        self.assertFalse(nodes[version_node_id(private.id, 1)]['is_current'])
        self.assertEqual(nodes[version_node_id(general.id, 1)]['label'], general.text)
        self.assertEqual(nodes[version_node_id(general.id, 1)]['questions'], [general.question])
        self.assertNotIn('question', nodes[version_node_id(general.id, 1)]['thought'])
        self.assertEqual(nodes[version_node_id(general.id, 1)]['session_ids'], ['session-one'])
        basis = next(row for row in graph['edges'] if row['relation_type'] == 'derivation')
        self.assertEqual(basis['to_id'], version_node_id(private.id, 1))
        semantic = [row for row in graph['edges'] if row['relation_type'] == 'semantic']
        self.assertEqual(len(semantic), 2)
        self.assertTrue(all(row['recorded_at'] is not None for row in semantic))
        self.assertEqual(graph['overviews'], [])
        self.assertNotIn('old-service-root', nodes)

    def test_session_filter_inherits_exact_raw_ancestry_and_marks_context_only(self):
        event = self.queue.capture_event('session-a', 'turn', 'Approval is required.', source='fixture')
        private = self.store.publish(event.prompt,
            bases=[{'event_id': event.id, 'span': {'start': 0, 'end': len(event.prompt)}}])
        other = self.store.publish('Approval is conditional.', origin={'session_id': 'session-b'})
        summary = self.store.publish('Approval is required under conditions.',
            bases=[{'thought_id': private.id, 'version': 1}, {'thought_id': other.id, 'version': 1}])
        unrelated = self.store.publish('A kettle heats water.', origin={'session_id': 'session-c'})
        graph = export_graph(self.store, session_id='session-a')
        nodes = {row['thought_ref']['thought_id']: row for row in graph['nodes']}
        self.assertEqual(set(nodes), {private.id, other.id, summary.id})
        self.assertEqual(nodes[summary.id]['session_ids'], ['session-a', 'session-b'])
        self.assertTrue(nodes[other.id]['session_context_only'])
        self.assertNotIn(unrelated.id, nodes)
        self.assertEqual(graph['episodes'][0]['id'], event.id)
        self.assertEqual(nodes[private.id]['evidence_refs'][0]['end'], len(event.prompt))

    def test_export_is_read_only_deterministic_and_preserves_unknown_provenance(self):
        thought = self.store.publish('An attributed undated report.', origin={'actor': 'assistant'})
        before = self.memory._connection.total_changes
        graph = export_graph(self.store)
        again = export_graph(self.store)
        self.assertEqual(graph, again)
        self.assertEqual(self.memory._connection.total_changes, before)
        node = graph['nodes'][0]
        self.assertEqual(node['thought_ref'], {'thought_id': thought.id, 'version': 1})
        self.assertEqual(node['session_membership'], 'unknown')
        self.assertEqual(node['valid_precision'], 'unknown')
        self.assertEqual(node['thought']['origin']['actor'], 'assistant')
        self.assertEqual(export_graph(self.store, session_id='unknown')['nodes'], [])


if __name__ == '__main__':
    unittest.main()
