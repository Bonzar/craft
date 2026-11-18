"""Program-owned envelopes bind semantic results without model-written references."""
import json
import unittest

from prototypes.assertion_memory.model import ModelError, ModelRequest, SemanticUnavailable, format_prompt
from prototypes.assertion_memory.semantic_jobs import (
    BoundBatchResponse, BoundSemanticJob, run_bound_job, run_bound_jobs,
)


class SemanticJobsTest(unittest.TestCase):
    def job(self, text, binding):
        return BoundSemanticJob(ModelRequest('Interpret this source', [{'text': text}]), binding)

    def test_single_job_keeps_private_binding_out_of_the_model_request(self):
        binding = {'source_id': 'private-source', 'hash': 'private-hash', 'span': (12, 80)}
        job = self.job('The gate opens only after approval.', binding)
        seen = []

        def model(request):
            seen.append(format_prompt(request))
            return '{"interpretations":["Approval precedes opening.","Opening is conditional."]}'

        result = run_bound_job(model, job, json.loads)
        self.assertIs(result.binding, binding)
        self.assertEqual(len(result.value['interpretations']), 2)
        self.assertNotIn('private-source', seen[0])
        self.assertNotIn('private-hash', seen[0])
        self.assertNotIn('span', seen[0])

    def test_model_body_cannot_replace_the_program_owned_binding(self):
        job = self.job('The gate stays closed.', ('source-a',))
        result = run_bound_job(lambda request: '{"binding":"source-b","meaning":"closed"}', job, json.loads)
        self.assertEqual(result.binding, ('source-a',))
        self.assertEqual(result.value['meaning'], 'closed')

    def test_single_job_propagates_transport_and_decoder_failures(self):
        job = self.job('The gate stays closed.', ('source-a',))
        failure = ModelError('transport unavailable')

        def fail(value):
            raise failure

        for model, decoder in ((fail, json.loads), (lambda request: '{}', fail)):
            with self.subTest(model=model):
                with self.assertRaises(ModelError) as raised:
                    run_bound_job(model, job, decoder)
                self.assertIs(raised.exception, failure)
                self.assertNotIsInstance(raised.exception, SemanticUnavailable)

    def test_batch_provider_reordering_does_not_swap_grounds(self):
        jobs = [self.job('A is closed.', ('source-a',)), self.job('B is open.', ('source-b',))]
        seen = []

        def provider(envelopes):
            seen.extend(envelopes)
            return [BoundBatchResponse(envelope.token, json.dumps({'text': envelope.request.records[0]['text']}))
                    for envelope in reversed(envelopes)]

        results = run_bound_jobs(provider, jobs, json.loads)
        self.assertEqual([(result.binding, result.value['text']) for result in results],
                         [(('source-a',), 'A is closed.'), (('source-b',), 'B is open.')])
        for envelope in seen:
            self.assertNotIn(envelope.token, format_prompt(envelope.request))
            self.assertNotIn('source-a', format_prompt(envelope.request))
            self.assertNotIn('source-b', format_prompt(envelope.request))

    def test_same_semantic_request_can_have_distinct_private_bindings(self):
        jobs = [self.job('Approved.', ('source-a',)), self.job('Approved.', ('source-b',))]

        def provider(envelopes):
            self.assertNotEqual(envelopes[0].token, envelopes[1].token)
            return [BoundBatchResponse(row.token, '{"accepted":true}') for row in envelopes]

        results = run_bound_jobs(provider, jobs, json.loads)
        self.assertEqual([row.binding for row in results], [('source-a',), ('source-b',)])

    def test_invalid_envelope_coverage_fails_before_any_body_is_decoded(self):
        jobs = [self.job('A.', ('source-a',)), self.job('B.', ('source-b',))]
        cases = {
            'missing': lambda rows: [BoundBatchResponse(rows[0].token, '{}')],
            'duplicate': lambda rows: [BoundBatchResponse(rows[0].token, '{}')] * 2,
            'foreign': lambda rows: [BoundBatchResponse(rows[0].token, '{}'), BoundBatchResponse('foreign', '{}')],
            'body_tokens_are_not_envelopes': lambda rows: [{'token': row.token, 'body': '{}'} for row in rows],
            'not_a_sequence': lambda rows: None,
        }
        for label, provider in cases.items():
            decoded = []
            with self.subTest(case=label):
                with self.assertRaises(ModelError) as raised:
                    run_bound_jobs(provider, jobs, lambda body: decoded.append(body))
                self.assertNotIsInstance(raised.exception, SemanticUnavailable)
                self.assertEqual(decoded, [])

    def test_empty_batch_does_not_start_a_provider(self):
        def provider(envelopes):
            self.fail('empty work must not incur a provider call')
        self.assertEqual(run_bound_jobs(provider, [], json.loads), ())

    def test_batch_provider_failure_propagates_without_single_call_fallback(self):
        failure = ModelError('batch transport failed')
        calls = []

        def provider(envelopes):
            calls.append(envelopes)
            raise failure

        with self.assertRaises(ModelError) as raised:
            run_bound_jobs(provider, [self.job('A.', ('source-a',))], json.loads)
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(calls), 1)

    def test_invalid_batch_body_fails_before_any_body_is_decoded(self):
        jobs = [self.job('A.', ('source-a',)), self.job('B.', ('source-b',))]
        for invalid in ('', None, 12):
            decoded = []

            def provider(rows):
                return [BoundBatchResponse(rows[0].token, '{}'), BoundBatchResponse(rows[1].token, invalid)]

            with self.subTest(body=invalid):
                with self.assertRaises(ModelError):
                    run_bound_jobs(provider, jobs, lambda body: decoded.append(body))
                self.assertEqual(decoded, [])


if __name__ == '__main__':
    unittest.main()
