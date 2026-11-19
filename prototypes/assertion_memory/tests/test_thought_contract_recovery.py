"""Bounded wire repair keeps accepted work and never publishes invalid drafts."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.model import ModelContextExceeded, ModelError, SemanticUnavailable, format_prompt
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_engine_contract import thought_drafts, validate_response
from prototypes.assertion_memory.thought_schema import response_schema
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker, _Replay


def draft(text, support):
    return json.dumps({'thoughts': [{'text': text, 'question': None, 'support_fragments': support}], 'needs_detail': None})


def answer(text, support):
    return json.dumps({'thought': {'text': text, 'question': None, 'support_fragments': support}, 'needs_detail': None})


ACCEPT = json.dumps({'verdict': 'accept', 'text': 'Every claim preserves its exact evidence and conditions.'})


class ThoughtContractRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / 'repair.sqlite3')
        self.addCleanup(self.memory.close)
        self.store = ThoughtStore(self.memory)
        self.source = self.store.publish('The gate opens only after Mira approval.')
        self.records = [{'role': 'material', 'text': self.source.text, 'origin': {'actor': 'fixture'}}]
        self.bindings = [{'thought_id': self.source.id, 'version': 1}]

    def replay_engine(self, responses, *, cached=None):
        calls, bindings = [], []
        cached = {} if cached is None else cached
        def provider(request):
            calls.append(request)
            return responses.pop(0)
        replay = _Replay(provider, cached.get, lambda key, body: cached.__setitem__(key, body),
                         lambda: None, ('query', 'fixture'),
                         invalidate=lambda keys: [cached.pop(key, None) for key in keys])
        def gateway(task, request, pinned):
            bindings.append(pinned)
            return replay(request)
        gateway.discard_provisional = replay.discard_provisional
        gateway.reject_construct = replay.reject_construct
        return ThoughtEngine(self.store, provider, gateway=gateway), replay, calls, bindings, cached

    def test_malformed_verify_retries_only_verify_and_provider_failures_are_not_format_errors(self):
        good = draft('Mira approval is required before opening.', [0])
        engine, replay, calls, _, cached = self.replay_engine([good, 'not JSON', ACCEPT])
        result = engine._construct(self.records, self.bindings)
        replay.confirm()
        self.assertEqual([request.task for request in calls], ['Construct thought', 'Verify thought', 'Verify thought'])
        self.assertEqual(format_prompt(calls[1]), format_prompt(calls[2]))
        self.assertNotIn('not JSON', cached.values())
        self.assertEqual(result[0].bases[0]['thought_id'], self.source.id)
        before = self.store.list_thoughts()
        broken, _, attempts, _, cache = self.replay_engine([good, 'bad', 'bad again'])
        with self.assertRaises(ModelError) as caught:
            broken._construct(self.records, self.bindings)
        self.assertTrue(caught.exception.recoverable)
        self.assertNotIsInstance(caught.exception, SemanticUnavailable)
        self.assertEqual(len(attempts), 3)
        self.assertNotIn('bad', cache.values())
        self.assertNotIn('bad again', cache.values())
        self.assertEqual(self.store.list_thoughts(), before)
        for failure in (ModelError('network unavailable'), ModelContextExceeded('actual provider context limit'),
                        ValueError('provider programming bug')):
            with self.subTest(failure=type(failure).__name__):
                invoked = []
                def fail(request):
                    invoked.append(request)
                    raise failure
                with self.assertRaises(type(failure)) as actual:
                    ThoughtEngine(self.store, fail)._call('Verify thought', self.records, self.bindings, '')
                self.assertIs(actual.exception, failure)
                self.assertEqual(len(invoked), 1)

    def test_unavailable_fragment_retries_once_on_unchanged_pins_and_discards_bad_provisional(self):
        bad = draft('Mira approval is required.', [999])
        good = draft('Mira approval is required.', [0])
        engine, replay, calls, bindings, cached = self.replay_engine([bad, good, ACCEPT])
        result = engine._construct(self.records, self.bindings)
        replay.confirm()
        self.assertEqual([request.task for request in calls], ['Construct thought', 'Construct thought', 'Verify thought'])
        self.assertEqual(calls[0].records, calls[1].records)
        self.assertEqual(bindings[0], bindings[1])
        self.assertEqual(calls[0].instructions,calls[1].instructions)
        bounds=calls[1].response_schema['properties']['thoughts']['items']['properties']['support_fragments']['items']
        self.assertEqual(bounds,{'type':'integer','minimum':0,'maximum':0})
        self.assertNotIn(self.source.id, json.dumps(calls[1].records))
        self.assertNotIn(bad, cached.values())
        self.assertEqual(result[0].bases[0]['span'], {'start': 0, 'end': len(self.source.text)})
        before = self.store.list_thoughts()
        broken, _, repeated, _, bad_cache = self.replay_engine([bad, bad])
        with self.assertRaises(ModelError) as caught:
            broken._construct(self.records, self.bindings)
        self.assertTrue(caught.exception.recoverable)
        self.assertNotIsInstance(caught.exception, SemanticUnavailable)
        self.assertEqual(len(repeated), 2)
        self.assertNotIn(bad, bad_cache.values())
        self.assertEqual(self.store.list_thoughts(), before)

    def test_requested_answer_cardinality_repairs_query_and_whole_refresh(self):
        good = answer('Mira approval is required before opening.', [0])
        row = json.loads(good)['thought']
        for whole in (False, True):
            for count in (0, 1, 4):
                with self.subTest(whole=whole, count=count):
                    records, bindings = self.records, self.bindings
                    question = 'When may the gate open?'
                    if whole:
                        records = [{'role': 'whole_refresh_scope', 'prior_content': self.source.text,
                                    'questions': [question]}, *records]
                        bindings = [self.bindings[0], *bindings]
                        question = None
                    bad = json.dumps({'thoughts': [row] * count, 'needs_detail': None})
                    engine, replay, calls, pinned, cached = self.replay_engine([bad, good, ACCEPT])
                    result = engine._prepare(records, bindings, question=question)
                    replay.confirm()
                    self.assertEqual(len(result), 1)
                    self.assertEqual(result[0][0]['text'], row['text'])
                    self.assertEqual([call.task for call in calls],
                                     ['Construct thought', 'Construct thought', 'Verify thought'])
                    self.assertEqual(format_prompt(calls[0]), format_prompt(calls[1]))
                    self.assertEqual(pinned[0], pinned[1])
                    self.assertNotIn(bad, cached.values())
                    self.assertIn(good, cached.values())
                    properties = calls[0].response_schema['properties']
                    self.assertNotIn('thoughts', properties)
                    self.assertEqual(properties['thought']['type'], 'object')
                    self.assertNotIn('anyOf', calls[0].response_schema)

    def test_repeated_invalid_answer_count_is_bounded_and_never_published(self):
        row = json.loads(draft('Mira approval is required.', [0]))['thoughts'][0]
        bad = json.dumps({'thoughts': [row] * 4, 'needs_detail': None})
        engine, replay, calls, _, cached = self.replay_engine([bad, bad])
        before = self.store.list_thoughts()
        with self.assertRaises(ModelError) as caught:
            engine._construct(self.records, self.bindings, question='When may the gate open?')
        replay.confirm()
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(caught.exception.stage, 'Construct thought')
        self.assertEqual(len(calls), 2)
        self.assertEqual(cached, {})
        self.assertEqual(self.store.list_thoughts(), before)

    def test_raw_construction_keeps_multiple_drafts(self):
        row = json.loads(draft('Mira approval is required.', [0]))['thoughts'][0]
        body = json.dumps({'thoughts': [row] * 4, 'needs_detail': None})
        engine, _, calls, _, _ = self.replay_engine([body, ACCEPT])
        result = engine._prepare([dict(self.records[0], role='source')], self.bindings)
        self.assertEqual(len(result), 4)
        self.assertEqual([call.task for call in calls], ['Construct thought', 'Verify thought'])
        self.assertNotIn('maxItems', calls[0].response_schema['properties']['thoughts'])

    def test_raw_extraction_publishes_multiple_thoughts_with_their_own_support(self):
        text = 'Approval is required. Evacuation is the exception.'
        raw = CaptureQueue(self.memory).capture_event('fixture', 'raw', text, source='fixture')
        rows = [{'text': claim, 'question': None, 'support_fragments': [index]}
                for index, claim in enumerate(('Approval is required.', 'Evacuation is the exception.'))]
        body = json.dumps({'thoughts': rows, 'needs_detail': None})
        engine, _, calls, _, _ = self.replay_engine([body, json.dumps({
            'verdict': 'accept', 'text': 'Both drafts pass.',
            'rejected_drafts': [], 'missing_meaning': None})])
        result = engine.ingest(raw, consolidate=False)
        self.assertEqual([thought.text for thought in result], [row['text'] for row in rows])
        for thought in result:
            basis = thought.bases[0]
            self.assertEqual(basis['event_id'], raw.id)
            self.assertEqual(text[basis['span']['start']:basis['span']['end']].strip(), thought.text)
        self.assertIn('"thoughts":', calls[0].instructions)
        self.assertNotIn('"thought":', calls[0].instructions)

    def test_initial_residual_context_overflow_uses_full_source_chunk_review(self):
        first = 'The eastern gate opens at dawn.'
        second = 'The western gate opens at dusk.'
        raw = CaptureQueue(self.memory).capture_event(
            'fixture', 'long-residual', first + '\n' + 'x' * 10100 + '\n\n' + second + '\n' + 'y' * 10100,
            source='fixture')
        calls = []

        def provider(request):
            calls.append(request.task)
            if request.task == 'Construct thought':
                if any(row.get('role') == 'rejected_draft' for row in request.records):
                    raise ModelContextExceeded('Residual prompt exceeded provider capacity')
                source = next(row for row in request.records if row.get('role') == 'source')
                pieces = {fragment['text'].strip(): fragment['option'] for fragment in source['fragments']}
                drafts = [{'text': text, 'question': None, 'support_fragments': [pieces[text]]}
                          for text in (first, second) if text in pieces]
                return json.dumps({'thoughts': drafts, 'needs_detail': None})
            if 'rejected_drafts' in request.response_schema['properties']:
                return json.dumps({'verdict': 'reject', 'text': 'Check the second gate timing.',
                    'rejected_drafts': [{'draft': 1, 'text': 'Confirm that opening is at dusk.'}],
                    'missing_meaning': None})
            return ACCEPT

        engine = ThoughtEngine(self.store, provider)
        published = engine.ingest(raw, consolidate=False)
        self.assertEqual([thought.text for thought in published], [first, second])
        self.assertEqual(calls.count('Verify source thought batch'), 1)

    def test_source_repair_preserves_a_verified_independent_thought(self):
        raw = CaptureQueue(self.memory).capture_event(
            'fixture', 'raw', 'Mira approves openings. The bridge closes at night.', source='fixture')
        accepted = 'Mira approves openings.'
        wrong = 'The bridge closes at noon.'
        fixed = 'The bridge closes at night.'
        initial = json.dumps({'thoughts': [
            {'text': accepted, 'question': None, 'support_fragments': [0]},
            {'text': wrong, 'question': None, 'support_fragments': [1]},
        ], 'needs_detail': None})
        partial = json.dumps({'verdict': 'reject', 'text': 'The bridge time changed.',
            'rejected_drafts': [{'draft': 1, 'text': 'Noon misstates night.'}], 'missing_meaning': None})
        passed = json.dumps({'verdict': 'accept', 'text': 'The new draft preserves the time.',
            'rejected_drafts': [], 'missing_meaning': None})
        engine, _, calls, _, _ = self.replay_engine([initial, partial, draft(fixed, [1]), passed])
        result = engine.ingest(raw, consolidate=False)
        self.assertEqual([thought.text for thought in result], [accepted, fixed])
        self.assertEqual([call.task for call in calls],
                         ['Construct thought', 'Verify thought', 'Construct thought', 'Verify thought'])
        self.assertEqual([row['text'] for row in calls[2].records if row['role'] == 'rejected_draft'], [wrong])
        self.assertEqual([row['text'] for row in calls[3].records if row['role'] == 'draft'], [fixed])
        self.assertEqual([row['text'] for row in calls[3].records if row['role'] == 'accepted_coverage'], [accepted])
        self.assertEqual([row.bases[0]['meaning'].strip() for row in result], [accepted, fixed])

    def test_source_worker_retry_reuses_accepted_review_checkpoint(self):
        raw = CaptureQueue(self.memory).capture_event(
            'fixture', 'raw', 'Mira approves openings. The bridge closes at night.', source='fixture')
        initial = json.dumps({'thoughts': [
            {'text': 'Mira approves openings.', 'question': None, 'support_fragments': [0]},
            {'text': 'The bridge closes at noon.', 'question': None, 'support_fragments': [1]},
        ], 'needs_detail': None})
        partial = json.dumps({'verdict': 'reject', 'text': 'The bridge time changed.',
            'rejected_drafts': [{'draft': 1, 'text': 'Noon misstates night.'}], 'missing_meaning': None})
        rejected_repair = json.dumps({'verdict': 'reject', 'text': 'The repair still changed the time.',
            'rejected_drafts': [{'draft': 0, 'text': 'Sunset does not establish night.'}], 'missing_meaning': None})
        correct = 'The bridge closes at night.'
        accepted_repair = json.dumps({'verdict': 'accept', 'text': 'The new draft preserves the time.',
            'rejected_drafts': [], 'missing_meaning': None})
        engine, _, _, _, cached = self.replay_engine(
            [initial, partial, draft('The bridge closes at sunset.', [1]), rejected_repair])
        with self.assertRaises(ModelError) as caught:
            engine.ingest(raw, consolidate=False)
        self.assertTrue(caught.exception.recoverable)
        self.assertCountEqual(cached.values(), [initial, partial])
        self.assertEqual(self.store.list_thoughts(), [self.source])

        restarted, _, calls, _, _ = self.replay_engine([draft(correct, [1]), accepted_repair], cached=cached)
        result = restarted.ingest(raw, consolidate=False)
        self.assertEqual([row.text for row in result], ['Mira approves openings.', correct])
        self.assertEqual([call.task for call in calls], ['Construct thought', 'Verify thought'])

    def test_worker_publishes_accepted_thought_and_restarts_only_residual_repair(self):
        queue = CaptureQueue(self.memory)
        raw = queue.capture_event(
            'fixture', 'raw', 'Mira approves openings. The bridge closes at night.', source='fixture')
        initial = json.dumps({'thoughts': [
            {'text': 'Mira approves openings.', 'question': None, 'support_fragments': [0]},
            {'text': 'The bridge closes at noon.', 'question': None, 'support_fragments': [1]},
        ], 'needs_detail': None})
        partial = json.dumps({'verdict': 'reject', 'text': 'The bridge time changed.',
            'rejected_drafts': [{'draft': 1, 'text': 'Noon misstates night.'}], 'missing_meaning': None})
        rejected = json.dumps({'verdict': 'reject', 'text': 'The repair still changed the time.',
            'rejected_drafts': [{'draft': 0, 'text': 'Sunset does not establish night.'}], 'missing_meaning': None})
        accepted = json.dumps({'verdict': 'accept', 'text': 'The new draft preserves the time.',
            'rejected_drafts': [], 'missing_meaning': None})
        requests = []
        responses = iter([initial, partial, draft('The bridge closes at sunset.', [1]), rejected,
                          draft('The bridge closes at night.', [1]), accepted])

        def model(request):
            requests.append(request)
            return next(responses)

        engine = ThoughtEngine(self.store, model)
        first = ThoughtWorker(queue, engine, lane='source').run_once()
        self.assertEqual(first.status, 'pending')
        self.assertEqual(len(first.extracted_ids), 1)
        carried = self.store.get(first.extracted_ids[0])
        self.assertEqual(carried.text, 'Mira approves openings.')
        self.assertEqual(carried.bases[0]['event_id'], raw.id)
        with self.memory.transaction(write=True) as c:
            c.execute('DELETE FROM memory_refresh_generations')
        queue.retry(first.id)
        resumed = ThoughtWorker(queue, ThoughtEngine(self.store, model), lane='source').run_once()
        self.assertEqual(resumed.status, 'completed')
        self.assertEqual(resumed.extracted_ids[0], carried.id)
        self.assertEqual(self.store.get(carried.id), carried)
        raw_thoughts = [row for row in self.store.list_thoughts() if row.origin.get('event_id') == raw.id]
        self.assertCountEqual([row.text for row in raw_thoughts],
                              ['Mira approves openings.', 'The bridge closes at night.'])
        self.assertEqual([call.task for call in requests[4:]], ['Construct thought', 'Verify thought'])
        self.assertEqual([row['text'] for row in requests[4].records if row['role'] == 'rejected_draft'],
                         ['The bridge closes at sunset.'])
        self.assertEqual([row['text'] for row in requests[5].records if row['role'] == 'draft'],
                         ['The bridge closes at night.'])

    def test_required_reconciliation_fields_cannot_be_omitted(self):
        classify = ('Find useful shared thought question', {'question': None, 'text': 'No new topic.'})
        review = ('Verify thought', {'verdict': 'accept', 'text': 'Supported.'})
        cases = [(classify, {'refresh_option_count': 1}, {'affected_candidates': []}),
                 (review, {'compare_meaning': True}, {'meaning_changed': False}),
                 (review, {'review_option_count': 1}, {'open_support': None})]
        for (task, response), options, required in cases:
            with self.subTest(options=options):
                with self.assertRaises(ModelError):
                    validate_response(task, response, **options)
                validate_response(task, {**response, **required}, **options)

    def test_independent_review_validates_rejection_options_and_missing_coverage(self):
        feedback = {'verdict': 'reject', 'text': 'The draft is unsupported.',
                    'rejected_drafts': [{'draft': 0, 'text': 'The source says the opposite.'}],
                    'missing_meaning': None}
        validate_response('Verify thought', feedback, source_draft_count=1)
        missing = dict(feedback, rejected_drafts=[], missing_meaning='The opening still requires approval.')
        validate_response('Verify thought', missing, source_draft_count=0)
        for broken in (dict(feedback, verdict='accept'), dict(feedback, rejected_drafts=[]),
                       dict(feedback, rejected_drafts=feedback['rejected_drafts'] * 2),
                       dict(feedback, rejected_drafts=[{'draft': 1, 'text': 'Not offered.'}])):
            with self.subTest(response=broken), self.assertRaises(ModelError):
                validate_response('Verify thought', broken, source_draft_count=1)

    def test_raw_construction_rejects_scalar_wire_without_publishing(self):
        body = answer('Approval is required.', [0])
        engine, replay, calls, _, cached = self.replay_engine([body, body])
        before = self.store.list_thoughts()
        with self.assertRaises(ModelError) as caught:
            engine._construct(self.records, self.bindings)
        replay.confirm()
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(len(calls), 2)
        self.assertEqual(cached, {})
        self.assertEqual(self.store.list_thoughts(), before)

    def test_null_thought_with_detail_remains_valid_wire_without_support(self):
        detail = {'question': 'Who may approve the opening?', 'text': 'The approver is unspecified.'}
        body = {'thought': None, 'needs_detail': detail}
        validate_response('Construct thought', body, support_count=0, single_thought=True)
        self.assertEqual(thought_drafts(body, support_count=0, single_thought=True), [])
        schema = response_schema('Construct thought', support_count=0, single_thought=True)
        self.assertEqual(schema['properties']['thought'], {'type': 'null'})
        with self.assertRaises(ModelError):
            validate_response('Construct thought', body, support_count=1, single_thought=True)

    def test_scalar_query_and_whole_refresh_publish_one_thought_with_selected_support(self):
        text = 'Mira approval is required before opening.'
        for whole in (False, True):
            with self.subTest(whole=whole):
                target = self.store.publish('The prior gate interpretation.') if whole else None
                question = 'When may the gate open?'
                records, bindings = self.records, self.bindings
                if whole:
                    records = [{'role': 'whole_refresh_scope', 'prior_content': target.text,
                                'questions': [question]}, *records]
                    bindings = [{'thought_id': target.id, 'version': target.version}, *bindings]
                engine, _, calls, _, _ = self.replay_engine([answer(text, [0]), ACCEPT])
                result = engine._construct(records, bindings, question=None if whole else question, target=target)
                self.assertEqual(len(result), 1)
                saved = self.store.get(result[0].id)
                self.assertEqual(saved.text, text)
                self.assertEqual(saved.question, None if whole else question)
                self.assertEqual(saved.bases[0]['thought_id'], self.source.id)
                self.assertEqual(saved.bases[0]['meaning'], self.source.text)
                self.assertEqual(saved.bases[0]['span'], {'start': 0, 'end': len(self.source.text)})
                if whole:
                    self.assertEqual((saved.id, saved.version), (target.id, 2))
                self.assertIn('"thought":', calls[0].instructions)
                self.assertNotIn('"thoughts":', calls[0].instructions)

    def test_malformed_scalar_and_mixed_responses_are_bounded_without_publication(self):
        row = json.loads(answer('Mira approval is required.', [0]))['thought']
        detail = {'question': 'Which approval?', 'text': 'The required approval is unspecified.'}
        malformed = [
            {'thought': [row], 'needs_detail': None},
            {'thought': None, 'needs_detail': None},
            {'thought': row, 'needs_detail': detail},
            {'thought': row, 'thoughts': [row], 'needs_detail': None},
            {'thought': row},
            {'thought': 'unsupported scalar text', 'needs_detail': None},
            {'thought': dict(row, support_fragments=[999]), 'needs_detail': None}]
        for payload in malformed:
            with self.subTest(payload=payload):
                body = json.dumps(payload)
                engine, replay, calls, _, cached = self.replay_engine([body, body])
                before = self.store.list_thoughts()
                with self.assertRaises(ModelError) as caught:
                    engine._construct(self.records, self.bindings, question='When may the gate open?')
                replay.confirm()
                self.assertTrue(caught.exception.recoverable)
                self.assertEqual(len(calls), 2)
                self.assertEqual(cached, {})
                self.assertEqual(self.store.list_thoughts(), before)


if __name__ == '__main__':
    unittest.main()
