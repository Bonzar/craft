"""Individual source requests share a generation call, retaining their responses."""
from concurrent.futures import ThreadPoolExecutor
import json
import unittest

from prototypes.assertion_memory.model import ModelRequest
from prototypes.assertion_memory.source_model_batch import SourceModelBatch


class SourceModelBatchTest(unittest.TestCase):
    def test_two_sources_get_independent_drafts_from_one_call(self):
        calls = []
        batch = SourceModelBatch(2)
        def generate(request, bindings):
            calls.append((request, bindings))
            result = {row['source']: {'thoughts': [row['records'][0]['text']]}
                      for row in request.records}
            return json.dumps(result), 'batch-run'
        def construct(text):
            request = ModelRequest('Construct thought', [{'text': text}], 'shared rules',
                                   response_schema={'type': 'object'})
            return batch.submit(request, ({'event_id': text},), generate)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(construct, ['first', 'second']))
        self.assertEqual([json.loads(result.body) for result in results],
                         [{'thoughts': ['first']}, {'thoughts': ['second']}])
        self.assertEqual({result.run_id for result in results}, {'batch-run'})
        self.assertEqual(len(calls), 1)
        self.assertEqual({row['event_id'] for row in calls[0][1]}, {'first', 'second'})

    def test_invalid_envelope_falls_back_instead_of_crossing_source_responses(self):
        batch = SourceModelBatch(2)
        request = ModelRequest('Construct thought', [], 'rules', response_schema={'type': 'object'})
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: batch.submit(request, (), lambda *_: ('{}', 'run')), range(2)))
        self.assertEqual(results, [None, None])
