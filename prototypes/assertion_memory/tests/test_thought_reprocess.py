"""Explicit source cleanup is semantic, atomic and preserves the raw/audit layers."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.model import SemanticUnavailable
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtConflict, ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, ScriptedModel, draft


class ThoughtReprocessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'reprocess.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.queue = CaptureQueue(self.memory)

    def source(self, text):
        raw = self.queue.capture_event('fixture', 'source', text, source='fixture', actor='assistant')
        old = self.store.publish('The agent ran a command successfully.',
            origin={'kind': 'source_interpretation', 'actor': raw.actor, 'source': raw.source,
                    'session_id': raw.session_id, 'recorded_at': raw.recorded_at},
            bases=[{'event_id': raw.id}], metadata={'protocol': 'unified-thought-v1'})
        return raw, old

    def test_execution_and_file_copy_noise_can_be_omitted_after_explicit_reprocessing(self):
        raw, old = self.source('Ran cat WORK_CHECKLIST, copied its 13 items. CLI identity detected. HTTP 200. Interrupted.')
        unrelated = self.store.publish('A kettle heats water.')
        model = ScriptedModel({'thoughts': []}, ACCEPT)
        result = ThoughtEngine(self.store, model).reprocess_source(raw)
        self.assertEqual(result['thoughts'], [])
        self.assertEqual([row.id for row in result['retired']], [old.id])
        self.assertIsNone(self.store.get(old.id))
        self.assertEqual(self.store.get(old.id, 1), old)
        self.assertEqual(self.store.get(unrelated.id), unrelated)
        self.assertEqual(self.queue.get_event(raw.id), raw)
        construct, review = model.requests
        self.assertIn('not an execution narrative', construct.instructions)
        self.assertIn('CLI identity', construct.instructions)
        self.assertIn('pure file/memory copies are intentionally omitted, never coverage gaps', review.instructions)
        self.assertIn('Origin fields already carry speaker', review.instructions)
        self.assertNotIn(raw.id, json.dumps(construct.records))

    def test_useful_reextraction_replaces_old_heads_only_after_review(self):
        raw, old = self.source('The test found: a missing lease flag permits an outdated worker to publish.')
        seen = []
        def model(request):
            self.assertEqual(self.store.get(old.id), old)
            seen.append(request.task)
            response = (draft('A missing lease flag permits an outdated worker to publish.', [raw.prompt])
                        if request.task == 'Construct thought' else ACCEPT)
            return json.dumps(response)
        result = ThoughtEngine(self.store, model).reprocess_source(raw)
        self.assertEqual(seen, ['Construct thought', 'Verify thought'])
        self.assertEqual(len(result['thoughts']), 1)
        new = result['thoughts'][0]
        self.assertNotEqual(new.id, old.id)
        self.assertEqual(new.origin['event_id'], raw.id)
        self.assertEqual(new.origin['kind'], 'source_interpretation')
        self.assertEqual(new.origin['role'], 'assistant')
        self.assertIsNone(self.store.get(old.id))
        self.assertEqual(self.store.get(old.id, 1), old)

    def test_mixed_source_batch_keeps_only_useful_meaning_in_two_model_calls(self):
        noise, old_noise = self.source('Ran cat WORK_CHECKLIST and copied it; HTTP 200.')
        useful = self.queue.capture_event('fixture', 'useful',
            'The test found a missing lease flag permits stale publication.', source='fixture', actor='tool')
        old_useful = self.store.publish('The tool finished successfully.',
            origin={'kind': 'legacy_assertion'}, bases=[{'event_id': useful.id}])
        model = ScriptedModel(draft('A missing lease flag permits stale publication.', [useful.prompt]), ACCEPT)
        result = ThoughtEngine(self.store, model).reprocess_sources([noise, useful])
        self.assertEqual([request.task for request in model.requests], ['Construct thought', 'Verify thought'])
        self.assertEqual({row.id for row in result['retired']}, {old_noise.id, old_useful.id})
        self.assertEqual(len(result['thoughts']), 1)
        new = result['thoughts'][0]
        self.assertEqual(new.origin, {'kind': 'inference', 'operation': 'source_refinement'})
        self.assertEqual([basis['event_id'] for basis in new.bases], [useful.id])
        self.assertEqual(model.requests[0].records[1]['origin']['role'], 'tool')
        self.assertEqual(self.queue.get_event(noise.id), noise)
        self.assertEqual(self.queue.get_event(useful.id), useful)

    def test_rejected_multi_source_batch_retires_nothing(self):
        first, old = self.source('Ran the build successfully.')
        second = self.queue.capture_event('fixture', 'other', 'The output was merely copied from memory.', source='fixture')
        model = ScriptedModel({'thoughts': []}, {'verdict': 'reject', 'text': 'A meaningful new constraint was omitted.'})
        with self.assertRaises(SemanticUnavailable):
            ThoughtEngine(self.store, model).reprocess_sources([first, second])
        self.assertEqual(self.store.list_thoughts(), [old])
        self.assertEqual(len(model.requests), 2)

    def test_rejection_or_changed_old_head_cannot_partially_replace_source(self):
        raw, old = self.source('The test found: a missing lease flag permits stale publication.')
        rejected = ScriptedModel(draft('A missing lease flag permits stale publication.', [raw.prompt]),
                                {'verdict': 'reject', 'text': 'The proposed meaning is incomplete.'})
        with self.assertRaises(SemanticUnavailable):
            ThoughtEngine(self.store, rejected).reprocess_source(raw)
        self.assertEqual(self.store.list_thoughts(), [old])
        def racing_model(request):
            if request.task == 'Construct thought':
                return json.dumps(draft('A missing lease flag permits stale publication.', [raw.prompt]))
            self.store.publish('A concurrent source interpretation.', thought_id=old.id, expected_version=1,
                               origin=old.origin, bases=old.bases, metadata=old.metadata)
            return json.dumps(ACCEPT)
        with self.assertRaises(ThoughtConflict):
            ThoughtEngine(self.store, racing_model).reprocess_source(raw)
        heads = self.store.list_thoughts()
        self.assertEqual(len(heads), 1)
        self.assertEqual((heads[0].id, heads[0].version), (old.id, 2))
        self.assertEqual(self.queue.get_event(raw.id), raw)


if __name__ == '__main__':
    unittest.main()
