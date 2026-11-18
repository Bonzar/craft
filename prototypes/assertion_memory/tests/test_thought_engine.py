"""Small hermetic acceptance checks for the single thought semantic path."""
import json
import tempfile
import unittest
from unittest.mock import patch
import prototypes.assertion_memory.model as model_contract
from pathlib import Path

from prototypes.assertion_memory.model import ModelError, SemanticUnavailable, format_prompt
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError('unexpected model call: ' + request.task)
        value = self.responses.pop(0)
        return json.dumps(value)


def draft(text, support, question=None):
    return {'thoughts': [{'text': text, 'question': question, 'support': support}]}


ACCEPT = {'verdict': 'accept', 'text': 'All claims preserve the evidence and conditions.'}


class ThoughtEngineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'fixture.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.queue = CaptureQueue(self.memory)

    def test_unreconciled_parallel_variant_is_not_an_automatic_exact_answer(self):
        question = 'When can the pilot open?'
        alternative = self.store.publish('The pilot might open after approval.', question=question,
            metadata={'protocol': 'unified-thought-v1', 'verification': 'accepted',
                      'publication': {'state': 'parallel_alternative'}})
        text = 'One parallel interpretation allows opening after approval; compatibility with the other answer remains unresolved.'
        model = ScriptedModel(draft(text, [alternative.text]), ACCEPT)
        engine = ThoughtEngine(self.store, model)
        engine._save_check(alternative, question, engine._frontier([], alternative.id))
        result = engine.answer(question)
        self.assertNotEqual(result.id, alternative.id)
        self.assertEqual([request.task for request in model.requests], ['Construct thought', 'Verify thought'])
        self.assertIn('unresolved', result.text)

    def test_source_and_composite_use_same_verified_publication_and_program_bindings(self):
        raw = self.queue.capture_event('fiction', 'turn', 'This kettle heats water; this one is electric.', source='fixture')
        model = ScriptedModel(draft('This electric kettle heats water.', [raw.prompt]), ACCEPT,
            {'question': 'What is a kettle for?', 'text': 'A useful concept description.'},
            draft('A kettle heats water. This example is electric; other kettles are not established here.',
                  ['This electric kettle heats water.']), ACCEPT)
        bindings = []
        def gateway(task, request, pinned):
            bindings.append(pinned)
            return model(request)
        engine = ThoughtEngine(self.store, model, gateway=gateway)
        created = engine.ingest(raw)
        thoughts = self.store.list_thoughts()
        self.assertEqual(len(thoughts), 2)
        composite = next(row for row in thoughts if row.question)
        self.assertEqual(composite.bases[0]['thought_id'], created[0].id)
        self.assertIn('other kettles are not established', composite.text)
        self.assertEqual([row.task for row in model.requests].count('Construct thought'), 2)
        self.assertEqual([row.task for row in model.requests].count('Verify thought'), 2)
        for request in model.requests:
            self.assertNotIn(raw.id, format_prompt(request))
            self.assertNotIn(created[0].id, format_prompt(request))
        self.assertEqual(bindings[0][0]['event_id'], raw.id)

    def test_ready_answer_is_reused_without_models_or_flattening_deep_bases(self):
        base = self.store.publish('The gate opens only after approval.')
        for index in range(25):
            base = self.store.publish('Approval is required.', bases=[{'thought_id': base.id, 'version': 1}])
        ready = self.store.publish('The gate needs approval.', question='When can the gate open?',
            bases=[{'thought_id': base.id, 'version': 1}], metadata={'verification': 'accepted'})
        model = ScriptedModel()
        engine = ThoughtEngine(self.store, model)
        self.assertEqual(engine.answer(ready.question), ready)
        self.assertEqual(model.requests, [])

    def test_answer_selects_actual_semantic_grounds_and_keeps_room_dimensions_separate(self):
        dimensions = self.store.publish('Room 4 is 12 square metres.', question='What are room 4 dimensions?')
        readiness = self.store.publish('Room 4 has painted walls and a stove.', question='Is room 4 equipped?')
        model = ScriptedModel({'verdict': 'build', 'text': 'A readiness conclusion is needed.'},
            draft('Room 4 has painted walls and a stove; completion is not established.',
                  ['Room 4 has painted walls and a stove.']), ACCEPT)
        engine = ThoughtEngine(self.store, model)
        answer = engine.answer('What is room 4 readiness?', explicit_ids=[dimensions.id, readiness.id])
        self.assertEqual([basis['thought_id'] for basis in answer.bases], [readiness.id])
        verify = model.requests[-1]
        dimension_context = next(row for row in verify.records if '12 square metres' in row.get('text',''))
        self.assertFalse(dimension_context['selected_for_grounding'])
        self.assertEqual(len(model.requests), 3)

    def test_rejection_does_not_publish_and_unmatched_support_is_contract_failure(self):
        raw = self.queue.capture_event('fixture', 'one', 'One kettle is electric.', source='fixture')
        for response, error in (([draft('All kettles are electric.', [raw.prompt]),
                                 {'verdict': 'reject', 'text': 'An instance is not universal.'}], SemanticUnavailable),
                                ([draft('All kettles are electric.', ['Invented quote'])], ModelError)):
            with self.subTest(error=error):
                with self.assertRaises(error):
                    ThoughtEngine(self.store, ScriptedModel(*response)).ingest(raw, consolidate=False)
                self.assertEqual(self.store.list_thoughts(), [])

    def test_ambiguous_quote_uses_prebound_local_judgment_without_model_identity(self):
        first = self.store.publish('Approval is required. The user chose to wait.', origin={'actor': 'user'})
        second = self.store.publish('Approval is required. An assistant suggested waiting.', origin={'actor': 'assistant'})
        model = ScriptedModel({'verdict': 'build', 'text': 'Need a precise choice.'},
            draft('Approval is required.', ['Approval is required.']), ACCEPT, ACCEPT, ACCEPT)
        engine = ThoughtEngine(self.store, model)
        answer = engine.answer('What approval is required?', explicit_ids=[first.id, second.id])
        self.assertEqual({b['thought_id'] for b in answer.bases}, {first.id, second.id})
        self.assertEqual([r.task for r in model.requests].count('Disambiguate thought support'), 2)
        self.assertNotIn(answer.bases[0]['thought_id'], format_prompt(model.requests[-2]))

    def test_selective_drill_follows_only_needed_exact_basis(self):
        detail = self.store.publish('The gate needs approval from Mira.',
                                    question='Who approves the gate?')
        unrelated = self.store.publish('The kettle holds two litres.')
        parent = self.store.publish('The gate needs approval; the approver is in its details.',
            question='Who approves opening the gate?',
            bases=[{'thought_id': detail.id, 'version': 1, 'meaning': 'Who approves the gate?'}])
        model = ScriptedModel({'verdict': 'drill', 'question': 'Who approves the gate?',
                               'text': 'The requested approver is a necessary missing detail.'},
            {'verdict': 'ready', 'text': 'Mira is the approver.'},
            draft('Mira must approve before the gate opens.', [detail.text]), ACCEPT)
        events = []
        engine = ThoughtEngine(self.store, model, trace=events.append)
        answer = engine.answer('Who approves opening the gate?', explicit_ids=[parent.id])
        self.assertEqual(answer.bases[0]['thought_id'], detail.id)
        self.assertEqual(answer.question, 'Who approves opening the gate?')
        expansion = next(row for row in events if row['stage'] == 'expansion')
        self.assertEqual(expansion['parent']['thought_id'], parent.id)
        self.assertNotIn(unrelated.id, json.dumps(expansion))
        self.assertIn('details', model.requests[0].records[1])

    def test_batch_rejection_rolls_back_all_source_meanings(self):
        raw = self.queue.capture_event('fixture', 'atomic', 'The gate is shut. Approval is pending.', source='fixture')
        response = {'thoughts': [
            {'text': 'The gate is shut.', 'question': None, 'support': ['The gate is shut.']},
            {'text': 'Approval was granted.', 'question': None, 'support': ['Approval is pending.']}]}
        model = ScriptedModel(response, {'verdict': 'reject', 'text': 'Pending is not granted.'})
        with self.assertRaises(SemanticUnavailable):
            ThoughtEngine(self.store, model).ingest(raw, consolidate=False)
        self.assertEqual(self.store.list_thoughts(), [])

    def test_new_relevant_thought_invalidates_receipt_but_unchanged_repeat_is_free(self):
        first = self.store.publish('The gate requires Mira approval.')
        model = ScriptedModel({'verdict': 'build', 'text': 'Construct the requested approval answer.'},
            draft('Mira must approve the gate.', [first.text]), ACCEPT)
        engine = ThoughtEngine(self.store, model)
        answer = engine.answer('What approval opens the gate?')
        self.assertEqual(engine.answer(answer.question), answer)
        self.assertEqual(len(model.requests), 3)
        new = self.store.publish('The gate also requires Lena approval.')
        model.responses += [{'verdict': 'build', 'text': 'The new approval condition is material.'},
            draft('Both Mira and Lena must approve the gate.', [first.text, new.text]), ACCEPT]
        updated = engine.answer(answer.question)
        self.assertEqual(updated.id, answer.id)
        self.assertEqual(updated.version, 2)
        self.assertEqual(engine.answer(answer.question), updated)
        self.assertEqual(len(model.requests), 6)
        self.assertIn(new.text, format_prompt(model.requests[3]))

    def test_imported_assertion_is_attributed_material_but_never_reused_as_verified(self):
        raw = self.queue.capture_event('legacy', 'report', 'Mira reports the gate is shut.', source='fixture', actor='Mira')
        legacy = self.store.publish(raw.prompt, origin={'kind': 'legacy_assertion', 'actor': 'Mira'},
            bases=[{'event_id': raw.id}], metadata={'verification': 'legacy_unverified'})
        model = ScriptedModel(draft(legacy.text, [legacy.text]), ACCEPT)
        answer = ThoughtEngine(self.store, model).answer('What did Mira report about the gate?')
        self.assertNotEqual(answer.id, legacy.id)
        self.assertEqual(answer.bases[0]['event_id'], raw.id)
        self.assertEqual(model.requests[0].records[1]['verification'], 'original_source')
        self.assertEqual(model.requests[0].records[1]['origin']['actor'], 'Mira')

    def test_multiple_source_drafts_share_one_whole_batch_review(self):
        raw = self.queue.capture_event('fixture', 'batch', '  The gate is shut. Approval is pending.', source='fixture')
        response = {'thoughts': [
            {'text': 'The gate is shut.', 'question': None, 'support': ['The gate is shut.']},
            {'text': 'Approval is pending.', 'question': None, 'support': ['Approval is pending.']}]}
        model = ScriptedModel(response, ACCEPT)
        thoughts = ThoughtEngine(self.store, model).ingest(raw, consolidate=False)
        self.assertEqual(len(thoughts), 2)
        self.assertEqual([row.task for row in model.requests], ['Construct thought', 'Verify thought'])
        self.assertEqual(len([row for row in model.requests[-1].records if row['role'] == 'draft']), 2)
        self.assertEqual(thoughts[0].bases[0]['span']['start'], raw.prompt.index('The gate'))

    def test_long_source_keeps_later_exception_and_rejects_atomically(self):
        class ActualContextOverflow(ModelError):
            pass
        head = 'GATE: The gate requires approval.'
        tail = 'EXCEPTION: Approval is not required during evacuation.'
        text = head + '\n\n' + ('Background passage. ' * 4000) + '\n\n' + tail
        raw = self.queue.capture_event('fixture', 'long', text, source='fixture')
        for reject_tail in (True, False):
            calls, bindings = [], []
            def model(request):
                calls.append(request)
                if request.task == 'Construct thought':
                    body = request.records[0]['text']
                    if body == text:
                        raise ActualContextOverflow('Provider rejected the actual complete request context')
                    self.assertIn('source_completeness', request.records[0])
                    if tail in body:
                        result = draft('This partial passage exempts evacuation from approval.', [tail])
                    elif head in body:
                        result = draft('This partial passage requires gate approval.', [head])
                    else:
                        result = {'thoughts': []}
                else:
                    is_tail = any('evacuation' in row.get('text', '') for row in request.records)
                    result = ({'verdict': 'reject', 'text': 'Tail interpretation requires repair.'}
                              if reject_tail and is_tail and request.task == 'Verify source thought batch' else ACCEPT)
                return json.dumps(result)
            def gateway(task, request, pinned):
                if task == 'Construct thought' and 'span' in pinned[0]:
                    bindings.append(pinned[0])
                return model(request)
            engine = ThoughtEngine(self.store, model, gateway=gateway)
            with patch.object(model_contract, 'ModelContextExceeded', ActualContextOverflow, create=True):
                if reject_tail:
                    with self.assertRaises(SemanticUnavailable):
                        engine.ingest(raw, consolidate=False)
                    self.assertEqual(self.store.list_thoughts(), [])
                else:
                    thoughts = engine.ingest(raw, consolidate=False)
                    exception = next(row for row in thoughts if 'evacuation' in row.text)
                    span = exception.bases[0]['span']
                    self.assertEqual(raw.prompt[span['start']:span['end']], tail)
                    self.assertGreater(span['start'], 60000)
            self.assertEqual([r.task for r in calls].count('Verify source thought batch'), 1)
            final_review = calls[-1]
            self.assertTrue(any(tail in row.get('text', '') for row in final_review.records))
            self.assertEqual(''.join(raw.prompt[b['span']['start']:b['span']['end']] for b in bindings), text)
            self.assertEqual(calls[0].records[0]['text'], text)
            self.assertGreater(len(json.dumps(calls[0].records, ensure_ascii=False)), 60000)

    def test_consolidating_current_same_question_does_not_duplicate_it(self):
        current = self.store.publish('A kettle heats water.', question='What does a kettle do?',
                                     metadata={'verification': 'accepted'})
        model = ScriptedModel({'question': current.question, 'text': 'This question is already answered.'})
        result = ThoughtEngine(self.store, model).consolidate([current])
        self.assertEqual(result, [current])
        self.assertEqual(self.store.list_thoughts(), [current])
        self.assertEqual(len(model.requests), 1)

    def test_context_only_draft_cannot_borrow_primary_user_origin(self):
        context = self.queue.capture_event('fixture', 'context', 'I implemented the gate.',
                                           source='fixture', actor='assistant')
        raw = self.queue.capture_event('fixture', 'reply', 'What next?', source='fixture')
        model = ScriptedModel(draft('The user implemented the gate.', [context.prompt]))
        with self.assertRaises(SemanticUnavailable):
            ThoughtEngine(self.store, model).ingest(raw, [context], consolidate=False)
        self.assertEqual(self.store.list_thoughts(), [])

    def test_stale_and_unverified_parents_use_current_grounds_before_construction(self):
        for verification in ('accepted', 'legacy_unverified'):
            with self.subTest(verification=verification):
                child = self.store.publish('Mira approves gate ' + verification + '.')
                parent = self.store.publish('Old gate summary ' + verification + '.',
                    question='Who approves ' + verification + '?',
                    bases=[{'thought_id': child.id, 'version': 1}],
                    metadata={'verification': verification})
                latest = self.store.publish('Lena approves gate ' + verification + '.',
                                            thought_id=child.id, expected_version=1)
                responses = [draft(latest.text, [latest.text]), ACCEPT]
                if verification != 'legacy_unverified':
                    responses.insert(0, {'verdict': 'ready', 'text': 'The old summary appears sufficient.'})
                model = ScriptedModel(*responses)
                result = ThoughtEngine(self.store, model).answer(parent.question, explicit_ids=[parent.id])
                self.assertEqual(result.bases[0]['version'], 2)
                self.assertEqual(result.bases[0]['thought_id'], child.id)
                construct = next(row for row in model.requests if row.task == 'Construct thought')
                self.assertNotIn(parent.text, format_prompt(construct))
                self.assertEqual(model.requests[-1].records[-1]['origin'], {'kind': 'inference'})

    def test_short_source_keeps_context_and_assistant_attribution(self):
        context = self.queue.capture_event('fixture', 'a', 'I propose waiting for approval.', source='fixture', actor='assistant')
        raw = self.queue.capture_event('fixture', 'b', 'Yes, wait for approval.', source='fixture', actor='user',
            metadata={'role': 'user', 'timezone': 'Europe/Moscow', 'private_unrelated': 'do not forward',
                      'source_message_at': '2026-11-17T08:00:00Z',
                      'source_message_clock_basis': {'kind': 'producer', 'source': 'fixture',
                                                     'field': 'timestamp', 'value': '2026-11-17T08:00:00Z'}})
        model = ScriptedModel(draft('The user chose to wait for approval.', [raw.prompt, context.prompt]), ACCEPT)
        result = ThoughtEngine(self.store, model).ingest(raw, [context], consolidate=False)[0]
        self.assertEqual({b['event_id'] for b in result.bases}, {raw.id, context.id})
        self.assertEqual(result.origin['actor'], 'user')
        self.assertEqual(result.origin['role'], 'user')
        self.assertEqual(result.origin['timezone'], 'Europe/Moscow')
        self.assertIn('2026-11-17T08:00:00', result.origin['source_message_at'])
        self.assertNotIn('private_unrelated', format_prompt(model.requests[0]))
        self.assertEqual(model.requests[-1].records[1]['origin']['actor'], 'assistant')


if __name__ == '__main__':
    unittest.main()
