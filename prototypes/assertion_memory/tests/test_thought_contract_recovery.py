"""Bounded wire repair keeps accepted work and never publishes invalid drafts."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.model import ModelContextExceeded, ModelError, SemanticUnavailable, format_prompt
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
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


if __name__ == '__main__':
    unittest.main()
