"""Meaningful selected evidence is not rejected by invented character windows."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, ScriptedModel, draft


class ThoughtCapacityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'capacity.sqlite3')
        self.addCleanup(self.memory.close)
        self.store, self.queue = ThoughtStore(self.memory), CaptureQueue(self.memory)

    def test_selected_large_source_reaches_provider_whole_with_final_qualification(self):
        first = 'Use focused tests for database work.'
        last = 'A full suite remains required when a concrete failure demands it.'
        text = first + '\n' + ('Relevant database test context. ' * 2500) + '\n' + last
        raw = self.queue.capture_event('fixture', 'large', text, source='fixture')
        known = self.store.publish(text, metadata={'verification': 'accepted', 'conditions': [last]})
        model = ScriptedModel(draft(first + ' ' + last, [first, last]), ACCEPT)
        trace = []
        engine = ThoughtEngine(self.store, model, trace=trace.append)
        records, _ = engine._current_material('What tests does database work require?', [known])
        self.assertEqual(records[0]['text'], text)
        self.assertEqual(records[0]['qualifications']['conditions'], [last])
        pending, _ = engine._pending_raw_material('What tests does database work require?')
        self.assertEqual(pending[0]['text'], text)
        created = engine.ingest(raw, consolidate=False)
        self.assertEqual([request.task for request in model.requests], ['Construct thought', 'Verify thought'])
        self.assertEqual(model.requests[0].records[0]['text'], text)
        self.assertNotIn('source_completeness', model.requests[0].records[0])
        self.assertIn(last, created[0].text)
        self.assertTrue(all(row['serialized_chars'] > 60000 for row in trace if row['stage'] == 'model_input'))

    def test_record_merge_deduplicates_exact_bindings_without_losing_conditions_or_spans(self):
        long_record = {'role': 'material', 'text': ('Necessary evidence. ' * 4000) + 'Only after approval.'}
        gap = {'coverage': 'partial', 'uncertainty': 'An additional exception remains unchecked.'}
        duplicate = dict(long_record, material_availability=gap)
        extra = {'role': 'material', 'text': 'Full testing is required only for a concrete failure.'}
        records, bases = ThoughtEngine._merge_material(
            [long_record], [{'event_id': 'first'}], [duplicate, extra],
            [{'event_id': 'first'}, {'event_id': 'second', 'span': {'start': 0, 'end': 10}}])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['text'], long_record['text'])
        self.assertEqual(records[0]['material_availability'], gap)
        self.assertEqual(records[1]['text'], extra['text'])
        self.assertGreater(len(json.dumps(records)), 60000)
        self.assertNotIn('material_availability', long_record)
        again, pinned = ThoughtEngine._merge_material(records, bases, [extra],
            [{'event_id': 'second', 'span': {'start': 10, 'end': 20}}])
        self.assertEqual(len(again), 3)
        self.assertEqual(len(pinned), 3)


if __name__ == '__main__':
    unittest.main()
