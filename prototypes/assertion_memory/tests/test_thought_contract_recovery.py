"""Bounded wire repair keeps accepted work and never publishes invalid drafts."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.model import ModelContextExceeded, ModelError, SemanticUnavailable, format_prompt
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtDetailNeeded, ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import _Replay


def draft(text, support):
    return json.dumps({'thoughts': [{'text': text, 'question': None, 'support_fragments': support}], 'needs_detail': None})


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

    def replay_engine(self, responses):
        calls, bindings, cached = [], [], {}
        def provider(request):
            calls.append(request)
            return responses.pop(0)
        replay = _Replay(provider, cached.get, lambda key, body: cached.__setitem__(key, body),
                         lambda: None, ('query', 'fixture'))
        def gateway(task, request, pinned):
            bindings.append(pinned)
            return replay(request)
        gateway.discard_provisional = replay.discard_provisional
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
        good = draft('Mira approval is required before opening.', [0])
        row = json.loads(good)['thoughts'][0]
        for whole in (False, True):
            for count in (0, 4):
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
                    thoughts = calls[0].response_schema['properties']['thoughts']
                    self.assertEqual(thoughts['maxItems'], 1)

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

    def test_needs_detail_is_valid_for_requested_answers_even_without_support(self):
        detail = {'question': 'Who may approve the opening?', 'text': 'The approver is unspecified.'}
        body = json.dumps({'thoughts': [], 'needs_detail': detail})
        for whole in (False, True):
            for has_support in (False, True):
                with self.subTest(whole=whole, has_support=has_support):
                    records = self.records if has_support else []
                    bindings = self.bindings if has_support else []
                    if whole:
                        records = [{'role': 'whole_refresh_scope', 'questions': [detail['question']]}, *records]
                        bindings = [{}, *bindings]
                    engine, replay, calls, _, _ = self.replay_engine([body])
                    with self.assertRaises(ThoughtDetailNeeded) as caught:
                        engine._prepare(records, bindings, question=None if whole else detail['question'])
                    self.assertEqual(caught.exception.question, detail['question'])
                    self.assertEqual(len(calls), 1)
                    self.assertIsNotNone(replay.pending)


if __name__ == '__main__':
    unittest.main()
