"""Malformed model drafts recover within fixed bounds, before semantic review."""
import json
from pathlib import Path
import tempfile
import unittest

from prototypes.assertion_memory.dialogue import extract_dialogue
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.tests.test_dialogue_recovery import event
from prototypes.assertion_memory.worker import LinkWorker


class DialogueValidationRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.target = event('target', 'Проверка завершилась без ошибок.', 'tool')
        self.valid = {'text': self.target.prompt, 'quote': self.target.prompt}
        self.calls = []

    def response(self, request, *, verdict='accepted'):
        if request.task.startswith('Verify contextual'):
            return json.dumps({'verdicts': [
                {'index': row['index'], 'status': verdict, 'reason': 'Independent source check.'}
                for row in request.records if row.get('role') == 'interpretation']})
        if request.task == 'Review source material coverage':
            return json.dumps({'source_coverage': {
                'status': 'accepted', 'reason': 'Independent complete source check.', 'omissions': []}})
        if request.task.startswith('Repair'):
            return json.dumps({'assertions': []})
        self.fail('Unexpected model task: ' + request.task)

    def test_invalid_quote_is_repaired_for_each_origin_and_fully_verified(self):
        for actor in ('user', 'assistant', 'tool'):
            with self.subTest(actor=actor):
                target = event('target', self.target.prompt, actor)
                calls = []
                def model(request):
                    calls.append(request)
                    if request.task.startswith('Extract'):
                        drafts = [call for call in calls if call.task.startswith('Extract')]
                        return json.dumps({'assertions': [
                            {**self.valid, 'quote': ''} if len(drafts) == 1 else self.valid]})
                    return self.response(request)
                result = extract_dialogue(model, target, [target])
                self.assertEqual([row['text'] for row in result['assertions']], [target.prompt])
                self.assertTrue(any(call.task.startswith('Verify contextual') for call in calls))
                self.assertTrue(any(call.task == 'Review source material coverage' for call in calls))
                correction = [call for call in calls if call.task.startswith('Extract')][1]
                self.assertIn('evidence quote must be nonempty source text', correction.instructions)
                self.assertTrue(any(row['operation'] == 'extraction_format_repair'
                                    for row in result['context_recovery']))

    def test_corrected_empty_draft_still_requires_independent_source_coverage(self):
        def model(request):
            self.calls.append(request)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [{**self.valid, 'quote': None}] if len(self.calls) == 1 else []})
            return self.response(request)
        result = extract_dialogue(model, self.target, [self.target])
        self.assertEqual(result['assertions'], [])
        self.assertEqual(sum(call.task == 'Review source material coverage' for call in self.calls), 1)
        self.assertTrue(any(row['operation'] == 'source_coverage' and row['status'] == 'accepted'
                            for row in result['context_recovery']))

    def test_normalization_recovery_applies_to_invalid_context_evidence_too(self):
        prior = event('prior', 'Использовался тестовый стенд.', 'assistant')
        invalid = {**self.valid, 'evidence': [{'event_id': prior.id, 'quote': '  '}]}
        def model(request):
            self.calls.append(request)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [invalid if len(self.calls) == 1 else self.valid]})
            return self.response(request)
        result = extract_dialogue(model, self.target, [prior, self.target])
        self.assertEqual(len(result['assertions']), 1)
        self.assertEqual(result['assertions'][0]['evidence_refs'][0]['quote'], self.target.prompt)

    def test_corrected_draft_cannot_bypass_independent_semantic_rejection(self):
        def model(request):
            self.calls.append(request)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [
                    {**self.valid, 'quote': ''} if len(self.calls) == 1 else self.valid]})
            return self.response(request, verdict='unsupported')
        result = extract_dialogue(model, self.target, [self.target])
        self.assertEqual(result['assertions'], [])
        self.assertTrue(any(row['kind'] == 'interpretation' and row['status'] == 'unsupported'
                            for row in result['reviews']))

    def test_permanent_malformed_draft_remains_a_visible_bounded_worker_failure(self):
        calls = []
        def model(request):
            calls.append(request)
            return json.dumps({'assertions': [{**self.valid, 'quote': ''}]})
        with tempfile.TemporaryDirectory() as directory, MemoryStore(Path(directory) / 'toy.sqlite') as store:
            queue = CaptureQueue(store, max_attempts=1)
            target = queue.capture_event('toy', 'one', self.target.prompt, source='test://target', actor='tool')
            LinkWorker(queue, MemoryEngine(store), model).run_once()
            job = queue.get(target.id)
            self.assertEqual(job.status, 'failed')
            self.assertIn('evidence quote must be nonempty source text', job.error)
            self.assertEqual(len(calls), 3, 'initial draft plus at most two schema corrections')
            self.assertEqual(store.list_assertions(), [])
            self.assertEqual(queue.get_event(target.id).prompt, self.target.prompt)

    def test_provider_acquisition_failure_is_not_retried_as_a_malformed_draft(self):
        def model(request):
            self.calls.append(request)
            raise ModelError('model command timed out')
        with self.assertRaisesRegex(ModelError, 'model command timed out'):
            extract_dialogue(model, self.target, [self.target])
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
