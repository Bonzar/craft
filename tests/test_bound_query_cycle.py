"""A fresh semantic question is saved and reused without model citation addresses."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore


class BoundQueryCycleTest(unittest.TestCase):
    def test_new_answer_preserves_literal_and_condition_then_reuses_saved_version(self):
        calls = []
        body = 'The fictional owner requires an operator trial before inspection. The label is Literal[0].'

        def provider(request):
            calls.append(request.task)
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': None, 'related_view_ids': [],
                    'subquestions': [], 'discover_topics': False, 'reason': 'One synthetic subject.'})
            if request.task == 'Review grounded memory contribution':
                return json.dumps({'grounding_status': 'accepted', 'coverage': 'packet_claims',
                    'global_coverage': 'not_assessed', 'reason': 'All fixture meanings retained.',
                    'issues': [], 'context_requests': [], 'deferred_context_requests': [], 'global_requests': []})
            if request.task.startswith('Review grounded memory overview'):
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims',
                    'reason': 'The attributed trial condition and literal label are retained.',
                    'issues': [], 'context_requests': [], 'deferred_context_requests': [], 'unknown_needs': []})
            if 'overview-display-pair-v1' in request.instructions:
                return json.dumps({'summary': body, 'display_title': 'Operator trial condition'})
            return body

        with MemoryStore(':memory:') as store:
            store.append_assertion(assertion_id='fixture-source', text=body,
                actor='fictional-owner', source='fixture://condition', episode='fixture', scope='s',
                recorded_at='2026-01-01')
            engine = MemoryEngine(store, model=provider, verify_overviews=True)
            service = MemoryService(store, engine)
            first = service.query('What condition governs inspection of Literal[0]?', scope='s')
            self.assertTrue(first['answer_accepted'], first)
            self.assertIn('operator trial', first['view']['summary'])
            self.assertIn('Literal[0]', first['view']['summary'])
            saved = engine.get_version(first['view']['id'])
            self.assertEqual(saved.overview_review['packet_binding']['source_ids'], ['fixture-source'])
            previous_calls = len(calls)
            second = service.query('What condition governs inspection of Literal[0]?', scope='s')
            self.assertEqual(second['view']['id'], first['view']['id'])
            self.assertTrue(second['answer_accepted'], second)
            self.assertEqual(len(calls), previous_calls, 'unchanged saved answer must not call a model again')
