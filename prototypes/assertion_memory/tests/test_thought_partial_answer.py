"""Answer from retrieved facts with scoped gaps; empty material is a program notice."""
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.model import ModelError, SemanticUnavailable
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded
from prototypes.assertion_memory.thought_answering import answer_selected
from prototypes.assertion_memory.thought_navigation import neighborhood
from prototypes.assertion_memory.thought_service import ThoughtService
from prototypes.assertion_memory.thought_store import ThoughtStore


EMPTY_NOTICE = 'В полученных материалах нет сведений для ответа на этот вопрос'
ACCEPT = json.dumps({'verdict': 'accept', 'text': 'The supplied fact and scoped unknowns are preserved.'})


class ThoughtPartialAnswerTest(unittest.TestCase):
    def memory(self):
        memory = MemoryStore(':memory:')
        self.addCleanup(memory.close)
        return memory

    def test_empty_and_excluded_sources_return_complete_scoped_notice_without_model_or_thought(self):
        for excluded in (False, True):
            with self.subTest(excluded=excluded):
                memory = self.memory()
                calls = []
                def model(request):
                    calls.append(request.task)
                    self.fail('Empty admitted material must not invoke a model')
                service = ThoughtService(memory, model)
                if excluded:
                    service.queue.capture_event('fixture', 'tool', 'Private tool result.',
                        source='codex://fixture', actor='tool', metadata={'role': 'tool'})
                before = service.thoughts.list_thoughts()
                result = service.query('What budget and date were agreed?')
                self.assertEqual(result['answer'], EMPTY_NOTICE)
                self.assertFalse(result['answer_accepted'])
                self.assertTrue(result['answer_payload_complete'])
                self.assertEqual(result['answer_continuations'], [])
                notice = result['answer_metadata']['notice']
                self.assertEqual(notice['purpose'], 'knowledge_limit_notice')
                self.assertEqual(notice['scope'], 'retrieved_material')
                self.assertEqual(notice['text'], EMPTY_NOTICE)
                self.assertTrue(notice['notice_payload_complete'])
                self.assertNotIn('knowledge_at_cutoff', result['answer_metadata'])
                self.assertTrue(result['knowledge_limit'])
                self.assertEqual(calls, [])
                self.assertEqual(service.thoughts.list_thoughts(), before)

    def test_no_prepared_support_cannot_invoke_construction(self):
        memory = self.memory()
        calls = []
        def model(request):
            calls.append(request.task)
            self.fail('No support fragments must be handled before model acquisition')
        store = ThoughtStore(memory)
        engine = ThoughtEngine(store, model)
        with self.assertRaises(SemanticUnavailable) as caught:
            engine._construct([{'role': 'whole_refresh_scope', 'questions': ['What is known?']}], [{}])
        self.assertTrue(caught.exception.empty_ground)
        self.assertEqual(calls, [])
        self.assertEqual(store.list_thoughts(), [])

    def test_partial_budget_answer_keeps_known_fact_and_gaps_without_another_search(self):
        memory = self.memory()
        text = 'The budget is 120 units. The date is not established in the supplied material.'
        calls = []
        def model(request):
            calls.append(request.task)
            if request.task == 'Construct thought':
                self.assertEqual(request.response_schema['properties']['thought'].get('type'), 'object')
                self.assertEqual(request.response_schema['properties']['needs_detail'], {'type': 'null'})
                self.assertNotIn('If a necessary semantic detail is missing, return', request.instructions)
                return json.dumps({'thought': {'text': text, 'question': None, 'support_fragments': [0]},
                                   'needs_detail': None})
            self.assertIn('Do not reject merely because a requested', request.instructions)
            return ACCEPT
        service = ThoughtService(memory, model)
        raw = service.queue.capture_event('fixture', 'budget', 'The budget is 120 units.', source='fixture')
        with patch.object(ThoughtEngine, 'retrieve', autospec=True, return_value=[]) as retrieval:
            result = service.query('What budget and date were agreed?')
        self.assertTrue(result['answer_accepted'])
        self.assertEqual(result['answer'], text)
        self.assertEqual(result['thought']['bases'][0]['event_id'], raw.id)
        self.assertEqual(calls, ['Construct thought', 'Verify thought'])
        self.assertEqual(retrieval.call_count, 1)
        self.assertEqual(len(service.thoughts.list_thoughts()), 1)

    def test_construction_cannot_turn_a_known_fact_into_a_detail_search(self):
        memory = self.memory()
        calls = []
        def model(request):
            calls.append(request.task)
            return json.dumps({'thought': None, 'needs_detail': {'question': 'Which date?', 'text': 'The date is missing.'}})
        service = ThoughtService(memory, model)
        service.queue.capture_event('fixture', 'budget', 'The budget is 120 units.', source='fixture')
        with patch.object(ThoughtEngine, 'retrieve', autospec=True, return_value=[]) as retrieval:
            with self.assertRaises(ModelError) as caught:
                service.query('What budget and date were agreed?')
        self.assertNotIsInstance(caught.exception, SemanticUnavailable)
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(calls, ['Construct thought', 'Construct thought'])
        self.assertEqual(retrieval.call_count, 1)
        self.assertEqual(service.thoughts.list_thoughts(), [])

    def test_whole_refresh_keeps_known_budget_and_replaces_unsupported_date_with_a_gap(self):
        memory = self.memory()
        store = ThoughtStore(memory)
        source = store.publish('The budget is 120 units.')
        target = store.publish('The budget is 120 units and the date is Friday.',
            bases=[{'thought_id': source.id, 'version': source.version}])
        text = 'The budget is 120 units. The current supplied material does not establish the date.'
        calls = []
        def model(request):
            calls.append(request.task)
            if request.task == 'Construct thought':
                return json.dumps({'thought': {'text': text, 'question': None, 'support_fragments': [0]},
                                   'needs_detail': None})
            self.assertEqual(request.records[-1]['text'], text)
            self.assertIn('gap rather than an unsupported old assertion', request.instructions)
            return ACCEPT
        result = ThoughtEngine(store, model)._construct([
            {'role': 'whole_refresh_scope', 'prior_content': target.text, 'questions': ['What budget?', 'Which date?']},
            {'role': 'material', 'text': source.text}],
            [{'thought_id': target.id, 'version': target.version}, {'thought_id': source.id, 'version': source.version}],
            target=target)[0]
        self.assertEqual((result.id, result.version), (target.id, 2))
        self.assertEqual(result.text, text)
        self.assertIsNone(result.question)
        self.assertEqual(result.bases[0]['thought_id'], source.id)
        self.assertEqual(calls, ['Construct thought', 'Verify thought'])

    def test_description_without_full_ground_is_empty_but_known_link_can_still_be_read(self):
        for linked, opened in ((False, False), (True, False), (True, True)):
            with self.subTest(linked=linked, opened=opened):
                memory = self.memory()
                store = ThoughtStore(memory)
                child = store.publish('An unverified linked description.', metadata={'verification': 'legacy_unverified'})
                hint = store.publish('An unverified starting description.',
                    bases=[{'thought_id': child.id, 'version': child.version}] if linked else [],
                    metadata={'verification': 'legacy_unverified'})
                calls = []
                def model(request):
                    calls.append(request.task)
                    self.fail('Descriptions alone cannot supply construction facts')
                engine = ThoughtEngine(store, model)
                with self.assertRaises(SemanticUnavailable) as caught:
                    answer_selected(engine, 'What is known?', hint, neighborhood=neighborhood,
                        additional=(child,) if opened else ())
                should_read_link = linked and not opened
                self.assertEqual(isinstance(caught.exception, ThoughtDetailNeeded), should_read_link)
                self.assertEqual(bool(getattr(caught.exception, 'empty_ground', False)), not should_read_link)
                self.assertEqual(calls, [])

    def test_reviewer_rejection_and_provider_errors_are_not_empty_material_notices(self):
        for failure in ('reject', 'provider'):
            with self.subTest(failure=failure):
                memory = self.memory()
                calls = []
                def model(request):
                    calls.append(request.task)
                    if failure == 'provider':
                        raise ModelError('Fixture provider unavailable')
                    if request.task == 'Construct thought':
                        return json.dumps({'thought': {'text': 'The date is tomorrow.', 'question': None,
                                                       'support_fragments': [0]}, 'needs_detail': None})
                    return json.dumps({'verdict': 'reject', 'text': 'The supplied material does not establish a date.'})
                service = ThoughtService(memory, model)
                service.queue.capture_event('fixture', 'budget', 'The budget is 120 units.', source='fixture')
                with self.assertRaises(ModelError) as caught:
                    service.query('What budget and date were agreed?')
                self.assertNotIsInstance(caught.exception, SemanticUnavailable)
                self.assertEqual(len(calls), 1 if failure == 'provider' else 4)
                self.assertEqual(service.thoughts.list_thoughts(), [])

    def test_other_semantic_limits_are_not_claimed_as_empty_retrieved_material(self):
        service = ThoughtService(self.memory(), None)
        with patch.object(ThoughtEngine, 'answer', side_effect=SemanticUnavailable('Graph reading is incomplete')):
            result = service.query('What is known?')
        self.assertIsNone(result['answer'])
        self.assertNotIn('answer_metadata', result)
        self.assertEqual(result['knowledge_limit'], 'Graph reading is incomplete')
