"""Contract repair preserves acquired verdicts without JSON-in-JSON expansion."""
import json
import hashlib
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError, ModelRequest, format_prompt
from prototypes.assertion_memory.overview_projection import preflight_family, repair_request
from prototypes.assertion_memory.storage import MemoryStore


def payload(request):
    return json.loads(format_prompt(request).split('\n\n', 1)[1])


class LosslessRepairProjectionTest(unittest.TestCase):
    def test_repair_delivers_original_verdict_once_as_structured_data(self):
        original = {
            'status': 'revise',
            'reason': 'The quoted path "C:\\reports\\draft" is not a confirmed result.\nУсловие сохраняется.',
            'context_requests': [{'operation': 'read', 'source_ids': ['fictional-source'],
                                  'gap_id': 'literal', 'include_source_evidence': True}],
            'unknown_needs': [{'gap_id': 'open-question', 'quote': 'yes, except room 214'}],
        }
        request = ModelRequest('Review synthetic answer', [{'role': 'evidence', 'text': 'Unchanged proof.'}])
        repaired = repair_request(request, original, ModelError('Invalid coverage'))
        rows = payload(repaired)['records']
        self.assertEqual(request.records[0], rows[0])
        self.assertEqual(original, rows[-1].get('original_response'))
        self.assertNotIn('original_response_json', rows[-1])

    def test_acquired_response_is_detached_without_changing_json_values_or_prompt_hash(self):
        original = {'nested': [None, True, False, 3.5, {'text': '\\"\n\tЯ😀'}]}
        acquired = json.loads(json.dumps(original, ensure_ascii=False))
        request = repair_request(ModelRequest('Review synthetic answer', []), original, ModelError('Invalid coverage'))
        wire = format_prompt(request)
        original['nested'][-1]['text'] = 'later external mutation'
        self.assertEqual(acquired, payload(request)['records'][-1].get('original_response'))
        self.assertEqual(hashlib.sha256(wire.encode()).hexdigest(),
                         hashlib.sha256(format_prompt(request).encode()).hexdigest())

    def test_maximal_structured_response_and_diagnostic_fit_exact_preflight_reservation(self):
        reviewer = ModelRequest('Review synthetic answer', [{'role': 'overview_draft', 'text': ''}])
        original = {'x': '\\' * 3996}
        self.assertEqual(8000, len(json.dumps(original, separators=(',', ':'))))
        sizes = preflight_family(ModelRequest('Write synthetic answer', []), reviewer)
        full_reviewer = ModelRequest(reviewer.task, [{'role': 'overview_draft', 'text': 'x' * 8000}])
        actual = repair_request(full_reviewer, original, ModelError('x' * 256))
        self.assertEqual(len(format_prompt(actual)), sizes[-1])
        self.assertEqual(original, payload(actual)['records'][-1].get('original_response'))

    def test_repair_serialization_versions_verified_pipeline_cache(self):
        from prototypes.assertion_memory import overview_projection
        with MemoryStore(':memory:') as store:
            current = MemoryEngine(store, verify_overviews=True).pipeline_key
            with patch.object(overview_projection, 'REPAIR_SERIALIZATION', 'legacy-json-string', create=True):
                legacy = MemoryEngine(store, verify_overviews=True).pipeline_key
        self.assertNotEqual(current, legacy)

    def test_reserved_repair_does_not_double_escape_complete_bounded_response(self):
        request = ModelRequest('Review synthetic answer', [{'role': 'evidence', 'text': 'Exact evidence.'}])
        original = {'reason': '\\' * 3980}
        encoded = json.dumps(original, ensure_ascii=False, separators=(',', ':'))
        self.assertLessEqual(len(encoded), 8000)
        result = repair_request(request, original, ModelError('x'))
        envelope = payload(result)['records'][-1]
        envelope_size = len(json.dumps(envelope, ensure_ascii=False, separators=(',', ':')))
        self.assertLessEqual(envelope_size, len(encoded) + 256)
        self.assertEqual(original, envelope.get('original_response'))

    def test_complete_proof_fits_family_without_spending_an_extra_escape_layer(self):
        proof = {'role': 'source_evidence', 'text': 'x' * 39000}
        writer = ModelRequest('Write synthetic answer', [proof])
        reviewer = ModelRequest('Review synthetic answer', [proof, {'role': 'overview_draft', 'text': ''}])
        sizes = preflight_family(writer, reviewer)
        self.assertEqual(4, len(sizes))
        self.assertLessEqual(max(sizes), 60000)
        self.assertEqual('x' * 39000, proof['text'])

    def test_over_limit_original_response_is_rejected_without_truncation(self):
        original = {'reason': '\\' * 4000}
        with self.assertRaisesRegex(ModelError, 'response exceeds'):
            repair_request(ModelRequest('Review synthetic answer', []), original, ModelError('Invalid coverage'))
        self.assertEqual('\\' * 4000, original['reason'])
