"""Frozen pending-chat fingerprints prevent permanently stale accepted answers."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.tests.test_thought_engine import ACCEPT, draft


class ThoughtRawFreshnessTest(unittest.TestCase):
    def test_new_pending_correction_refreshes_cached_answer_then_repeats_without_model(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / 'freshness.sqlite3')
            self.addCleanup(memory.close)
            store, queue = ThoughtStore(memory), CaptureQueue(memory)
            first = queue.capture_event('fixture', 'a', 'Gate approval requires Mira only.', source='codex://fixture')
            correction_text = 'Correction: gate approval requires both Mira and Lena; Mira alone is insufficient.'
            question = 'What gate approval is required?'
            requests, added = [], []
            def provider(request):
                requests.append(request)
                if request.task == 'Navigate thought graph':
                    options = [row for row in request.records if row['role'] in ('option', 'current_thought')]
                    selected = next((row for row in options if row.get('description', row.get('text')) == correction_text), options[0])
                    return json.dumps({'action': 'focus', 'question': None,
                        'quote': selected.get('description', selected.get('text')),
                        'text': 'This visible content is the useful answer location.'})
                if request.task == 'Check ready thought':
                    selected = next(row for row in request.records if row['role'] == 'candidate')
                    visible_correction = any(row.get('text') == correction_text for row in request.records)
                    verdict = 'build' if visible_correction and selected['text'] != correction_text else 'ready'
                    return json.dumps({'verdict': verdict, 'question': None,
                        'text': 'The visible correction must be included, or the candidate already contains it.'})
                if request.task == 'Construct thought':
                    text = correction_text if added else first.prompt
                    self.assertIn(text, json.dumps(request.records))
                    return json.dumps(dict(draft(text, [text]), needs_detail=None))
                self.assertEqual(request.task, 'Verify thought')
                # A new source arriving during review must not enter its receipt.
                if not added:
                    added.append(queue.capture_event('fixture', 'b', correction_text, source='codex://fixture'))
                return json.dumps(ACCEPT)
            engine = ThoughtEngine(store, provider)
            old = engine.answer(question)
            self.assertEqual(old.text, first.prompt)
            with memory.transaction() as connection:
                before = [dict(row) for row in connection.execute('SELECT * FROM memory_capture_jobs ORDER BY id')]
            refreshed = engine.answer(question)
            self.assertNotEqual((refreshed.id, refreshed.version), (old.id, old.version))
            self.assertEqual(refreshed.text, correction_text)
            self.assertEqual(refreshed.bases[0]['event_id'], added[0].id)
            self.assertEqual(store.get(old.id, old.version), old)
            self.assertEqual(queue.get_event(first.id), first)
            constructions = [row for row in requests if row.task == 'Construct thought']
            self.assertEqual(len(constructions), 2)
            self.assertIn(correction_text, json.dumps(constructions[1].records))
            self.assertEqual(sum(row.task == 'Verify thought' for row in requests), 2)
            before_warm = len(requests)
            self.assertEqual(engine.answer(question), refreshed)
            self.assertIn([row.task for row in requests[before_warm:]],
                ([], ['Navigate thought graph', 'Check ready thought']))
            warm_calls = len(requests)
            self.assertEqual(engine.answer(question), refreshed)
            self.assertEqual(len(requests), warm_calls)
            with memory.transaction() as connection:
                after = [dict(row) for row in connection.execute('SELECT * FROM memory_capture_jobs ORDER BY id')]
            self.assertEqual(after, before)
            queue.capture_event('unrelated', 'c', 'Bananas.', source='codex://fixture')
            self.assertEqual(engine.answer(question), refreshed)
            self.assertEqual(len(requests), warm_calls)


if __name__ == '__main__':
    unittest.main()
