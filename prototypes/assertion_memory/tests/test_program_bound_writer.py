"""Writer prose never chooses source addresses; whole-packet review still gates publication."""
from dataclasses import asdict
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError, SemanticUnavailable
from prototypes.assertion_memory.overview import _model_record
from prototypes.assertion_memory.overview_projection import upstream_parts
from prototypes.assertion_memory.overview_review import (
    RetrievalLedger, _make_writer_request, _model_contribution, _review_text, _write,
)
from prototypes.assertion_memory.storage import MemoryStore


class ProgramBoundWriterTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.source = self.store.append_assertion(assertion_id='opaque-proof-a',
            text='The reported label is [source:literal-label]; release requires approval.', actor='reporter',
            source='fixture://approval', episode='fiction', scope='fiction', recorded_at='2026-01-01')
        self.record = {**asdict(self.source), 'id': self.source.assertion_id, 'disposition': 'assertion'}
        self.spec = ViewSpec('approval-view', 'Approval', 'What are the reported release conditions?',
                             scopes=('fiction',), root_ids=(self.source.assertion_id,))
        self.frame = {'role': 'query_context', 'question': self.spec.query, 'scopes': ['fiction'], 'cutoffs': {}}
        self.rows = [_model_record(self.record)]
        self.text = 'The reported label is [source:literal-label]; release still requires approval.'
        self.calls = []

    def provider(self, request):
        self.calls.append(request)
        if request.task.startswith('Review'):
            local = request.task == 'Review grounded memory contribution'
            return json.dumps({'grounding_status' if local else 'status': 'accepted',
                'coverage': 'packet_claims' if local else 'all_claims', 'reason': 'All claims and conditions checked.',
                'issues': [], 'context_requests': [], 'unknown_needs': []})
        return self.text

    def ledger(self):
        return RetrievalLedger(None, {self.record['id']: self.record}, {self.record['id']})

    def test_all_writer_phases_keep_brackets_literal_without_identity_retry(self):
        for phase in ('local', 'composition', 'global'):
            with self.subTest(phase=phase):
                ledger = self.ledger()
                engine = MemoryEngine(self.store, model=self.provider)
                before = len(self.calls)
                text, _ = _write(engine, self.spec, self.rows, [], self.frame, ledger, phase, packet_id='private-packet')
                self.assertEqual(text, self.text)
                self.assertEqual(len(self.calls) - before, 1)
                self.assertEqual(ledger.writer_format_retries, 0)
                verdict = _review_text(engine, self.spec, self.rows, [], text, self.frame, ledger, phase, 'private-packet')
                binding = verdict['packet_binding']
                self.assertEqual(binding['source_ids'], [self.record['id']])
                self.assertEqual(binding['scope'], 'whole_packet_not_individual_phrases')
                self.assertEqual(binding['review_coverage'], 'all_claims' if phase == 'global' else 'packet_claims')
                self.assertNotIn('literal-label', binding['source_ids'])

    def test_writer_is_not_asked_to_copy_source_identifiers_even_on_retry(self):
        from prototypes.assertion_memory.overview_projection import format_retry_request
        request = _make_writer_request(self.spec, self.rows, [], self.frame, self.ledger(), 'global')
        retry = format_retry_request(request, {'failure': 'encoded_length', 'stage': 'summary'})
        for candidate in (request, retry):
            self.assertNotIn('Cite supporting source IDs', candidate.instructions)
            self.assertNotIn('Copy actual citation IDs', candidate.instructions)
            self.assertIn('Do not produce source identifiers', candidate.instructions)

    def test_review_rejects_changed_writer_packet_before_any_reviewer_call(self):
        engine = MemoryEngine(self.store, model=self.provider)
        ledger = self.ledger()
        text, _ = _write(engine, self.spec, self.rows, [], self.frame, ledger, 'global')
        calls_before_review = len(self.calls)
        changed = [{**self.rows[0], 'text': 'An unrelated replacement evidence body.'}]
        with self.assertRaisesRegex(ModelError, 'packet binding'):
            _review_text(engine, self.spec, changed, [], text, self.frame, ledger, 'global')
        self.assertEqual(len(self.calls), calls_before_review)

    def test_fixed_legacy_text_has_separate_current_applicability_binding(self):
        engine = MemoryEngine(self.store, model=self.provider)
        ledger = self.ledger()
        verdict = _review_text(engine, self.spec, self.rows, [], self.text, self.frame, ledger, 'global', fixed=True)
        self.assertEqual(verdict['packet_binding']['purpose'], 'fixed_answer_applicability')
        self.assertNotIn('writer_request_sha256', verdict['packet_binding'])
        self.assertEqual(ledger.writer_bindings, [])

    def test_saved_answer_has_program_references_without_inline_id_expansion(self):
        engine = MemoryEngine(self.store, model=self.provider, verify_overviews=True)
        engine.define_view(self.spec)
        version = engine.build_view(self.spec.id)
        self.assertEqual(version.summary, self.text)
        self.assertEqual(version.overview_review['packet_binding']['source_ids'], [self.record['id']])
        self.assertEqual([row['id'] for row in version.evidence], [self.record['id']])
        self.assertNotIn(self.record['id'], version.summary)

    def test_existing_display_title_pair_keeps_exact_semantic_text(self):
        provider = lambda request: json.dumps({'summary': self.text, 'display_title': 'Reported release conditions'})
        engine = MemoryEngine(self.store, model=provider)
        ledger = self.ledger()
        text, _ = _write(engine, self.spec, self.rows, [], self.frame, ledger, 'global')
        self.assertEqual(text, self.text)
        self.assertEqual(ledger.visual_candidate['display_title'], 'Reported release conditions')
        self.assertEqual(ledger.writer_format_retries, 0)

    def test_empty_writer_body_still_uses_only_existing_bounded_format_retry(self):
        calls = []
        def provider(request):
            calls.append(request)
            return '' if len(calls) == 1 else self.text
        engine = MemoryEngine(self.store, model=provider)
        ledger = self.ledger()
        text, _ = _write(engine, self.spec, self.rows, [], self.frame, ledger, 'local')
        self.assertEqual(text, self.text)
        self.assertEqual(len(calls), 2)
        self.assertEqual(ledger.writer_format_retries, 1)

    def test_complete_packet_binding_does_not_bypass_semantic_rejection(self):
        def reject(request):
            if request.task.startswith('Review'):
                return json.dumps({'status': 'revise', 'coverage': 'partial', 'reason': 'Missing approval condition.',
                    'issues': ['Permission was broadened.'], 'context_requests': [], 'unknown_needs': []})
            return 'Release is permitted without approval. [untrusted-address]'
        engine = MemoryEngine(self.store, model=reject, verify_overviews=True)
        engine.define_view(self.spec)
        with self.assertRaises(SemanticUnavailable):
            engine.build_view(self.spec.id)
        snapshots = [json.loads(row[0]) for row in self.store._connection.execute('SELECT snapshot FROM memory_view_versions')]
        self.assertFalse(any((row.get('overview_review') or {}).get('status') == 'accepted' for row in snapshots))
        self.assertFalse(any('Release is permitted without approval.' in row['summary'] for row in snapshots))

    def test_accepted_upstream_text_is_preserved_without_parsing_brackets_as_addresses(self):
        item = {'role': 'verified_view', 'version_id': 7, 'summary': self.text,
                'source_ids': [self.record['id']], 'scopes': ['fiction'], 'cutoffs': {}}
        parts = upstream_parts([item], [self.record], [], self.spec.scopes, {})
        self.assertEqual(parts[0]['final'], self.text)
        self.assertEqual(parts[0]['records'], [self.record])

    def test_large_contribution_membership_never_comes_from_bracket_prose(self):
        records = [{'id': 'canonical-' + str(index)} for index in range(70)]
        part = {'key': 'private-key', 'records': records, 'final': 'A literal [canonical-69] is domain text.'}
        ledger = self.ledger()
        row = _model_contribution(part, ledger)
        self.assertEqual(row['source_ids'], [])
        self.assertEqual(ledger.manifests[row['lineage_manifest_sha256']], [record['id'] for record in sorted(records, key=lambda row: row['id'])])


if __name__ == '__main__':
    unittest.main()
