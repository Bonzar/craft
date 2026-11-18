"""A paused raw queue remains durable evidence for bounded central read repair."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_selection import ThoughtSelection
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, ScriptedModel, draft


class ThoughtPendingTest(unittest.TestCase):
    def test_pending_user_instruction_builds_saved_answer_without_queue_work(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / 'pending.sqlite3')
            self.addCleanup(memory.close)
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            def capture(turn, text, *, scope='work', valid='2026-11-01', recorded='2026-11-10', actor='Vlad'):
                metadata = {'role': 'tool' if actor == 'tool' else 'user'}
                if valid is not None:
                    metadata['valid_at'] = valid
                return queue.capture_event('fixture', turn, text, source='codex://fixture', actor=actor,
                    scope=scope, recorded_at=recorded, metadata=metadata)
            old_raw = capture('old', 'The earlier database draft left speed and token limits unsettled.')
            raw = capture('instruction', 'Keep database work fast and token spending minimal; use only focused tests.')
            for turn, kwargs in [('other-scope', {'scope': 'home'}), ('unknown-valid', {'valid': None}),
                                 ('future-valid', {'valid': '2026-12-01'}), ('future-recorded', {'recorded': '2026-12-01'}),
                                 ('private-tool', {'actor': 'tool'})]:
                capture(turn, 'EXCLUDED database fast token focused test instruction ' + turn, **kwargs)
            with memory.transaction() as connection:
                before = [dict(row) for row in connection.execute('SELECT * FROM memory_capture_jobs ORDER BY id')]
            selection = ThoughtSelection(store, scope='work', valid_as_of='2026-11-17', recorded_as_of='2026-11-17')
            selection.publish('UNVERIFIED earlier database speed and token overview.',
                origin={'kind': 'legacy_assertion'}, bases=[{'event_id': old_raw.id}],
                metadata={'verification': 'legacy_unverified'})
            text = 'Vlad required fast database work, minimal token spending and focused tests.'
            model = ScriptedModel(dict(draft(text, [raw.prompt]), needs_detail=None), ACCEPT)
            trace = []
            answer = ThoughtEngine(selection, model, trace=trace.append).answer('What database speed and token constraints did Vlad set?')
            self.assertEqual(answer.text, text)
            self.assertEqual(answer.bases[0]['event_id'], raw.id)
            self.assertIsNotNone(store.get(answer.id))
            self.assertEqual([request.task for request in model.requests], ['Construct thought', 'Verify thought'])
            self.assertNotIn('EXCLUDED', json.dumps([request.records for request in model.requests]))
            self.assertNotIn('UNVERIFIED', json.dumps([request.records for request in model.requests]))
            gap = next(row for row in trace if row['stage'] == 'raw_gap_search')
            self.assertIn(raw.id, [binding['event_id'] for binding in gap['selected']])
            self.assertLessEqual(gap['details']['primary_count'], 3)
            self.assertGreater(gap['details']['input_chars'], 0)
            with memory.transaction() as connection:
                after = [dict(row) for row in connection.execute('SELECT * FROM memory_capture_jobs ORDER BY id')]
            self.assertEqual(after, before)
            self.assertTrue(all(row['status'] == 'waiting_model' for row in after))


if __name__ == '__main__':
    unittest.main()
