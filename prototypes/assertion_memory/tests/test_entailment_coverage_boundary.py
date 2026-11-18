"""Contract projection and fictional reconciliation probes, never a model-quality gate.

Scripted independent verdicts below are test inputs, not semantic predictions.
The prompt assertions guard the requested boundary; only a fresh actual-provider
gate can show that a model applies it (the recovery32 actual-provider run failed).
"""
import json
import unittest
from dataclasses import asdict
from unittest.mock import patch

from prototypes.assertion_memory.dialogue import (
    MATERIAL_REFERENT_RULES, _context_records, _event_record,
    _extraction_instructions, _normalize_claims, _verification_instructions,
    _verify_coverage, MAX_CONTEXT_CHARACTERS,
)
from prototypes.assertion_memory.dialogue_context import ContextWindow
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import format_prompt
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
from prototypes.assertion_memory.source_coverage import _COVERAGE, PROTOCOL as COVERAGE_PROTOCOL
from prototypes.assertion_memory.source_reprocessing import PROTOCOL
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.tests.test_dialogue_recovery import event
from prototypes.assertion_memory.worker import LinkWorker


class EntailmentCoverageContractTest(unittest.TestCase):
    def test_verifier_distinguishes_proposition_truth_from_source_inventory(self):
        for truncated in (False, True):
            instructions = _verification_instructions(truncated)
            self.assertIn('exact asserted proposition, not completeness of the source inventory', instructions)
            self.assertIn('separately stated reason alone does not make that proposition unsupported', instructions)
            self.assertIn('necessary condition, exception, negation, referent, uncertainty or temporal qualification', instructions)
            self.assertIn('broadens permission or strengthens the claim', instructions)
            self.assertNotIn(MATERIAL_REFERENT_RULES, instructions)

    def test_extraction_and_inventory_keep_full_material_referent_closure(self):
        self.assertIn(MATERIAL_REFERENT_RULES, _extraction_instructions())
        self.assertIn(MATERIAL_REFERENT_RULES, _COVERAGE)
        self.assertIn('An entailed bounded choice or report may omit a separately attributed reason', _COVERAGE)
        self.assertIn('still an inventory omission', _COVERAGE)

    def test_changed_contract_versions_frozen_input_identity(self):
        self.assertEqual(COVERAGE_PROTOCOL, 'source-material-coverage-v8:source-message-clock-v1')
        self.assertEqual(PROTOCOL, 'source-reconciliation/v2:' + COVERAGE_PROTOCOL)

    def verify_fixture(self, original, claim, expected, *, prefix=None, chunk_only=False):
        proposal = event('proposal', original)
        target = event('target', 'Use that option.', 'user')
        context = [proposal, target]
        records, _ = _context_records(target, context)
        window = ContextWindow(target, context, records, _event_record, budget=MAX_CONTEXT_CHARACTERS)
        if prefix and proposal.id not in window.spans:
            window.recover([{'operation': 'read', 'event_id': proposal.id, 'start': 0, 'end': len(prefix)}])
        candidate = {'text': claim, 'kind': 'decision', 'quote': target.prompt,
                     'evidence': [{'event_id': proposal.id, 'quote': prefix or original}]}
        claims = _normalize_claims(target, [candidate], context, window.records(), window)
        calls = []
        def reviewer(request):
            calls.append(request)
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            # Deliberately supplied independent judgment. No toy language model
            # infers semantics from a prompt substring or production keyword.
            scanned = any(r.get('coverage_scan') and 'Only after induction and signed release.' in r.get('text', '')
                          for r in request.records)
            status = expected if not chunk_only or scanned else 'accepted'
            return json.dumps({'verdicts': [{'index': 0, 'status': status,
                                             'reason': 'Independent fictional source judgment.'}]})
        verdicts = _verify_coverage(reviewer, window.records(), claims, window.truncated, window)
        self.assertEqual(verdicts[0]['status'], expected)
        self.assertTrue(calls)
        self.assertTrue(all(r.instructions == _verification_instructions(window.truncated) for r in calls))
        return calls

    def test_bounded_choice_survives_separate_reason_before_after_and_uncertain_report(self):
        for original in (
            'Morning access is reserved. I propose the late slot.',
            'I propose the late slot. My reason is the morning allocation.',
            'I propose the late slot. Noor says morning access may be reserved; I have not checked.',
            'I propose the late slot. Separately, the room has a window.',
        ):
            with self.subTest(original=original):
                self.verify_fixture(original, 'The user chose the proposed late slot.', 'accepted')

    def test_missing_either_conjunct_does_not_broaden_permission(self):
        for claim in ('The user may collect equipment.',
                      'The user may collect equipment after induction.',
                      'The user may collect equipment after signing the release.'):
            with self.subTest(claim=claim):
                self.verify_fixture('Collection is allowed only after induction AND signed release.', claim, 'unsupported')

    def test_exception_and_negation_are_not_additive_background(self):
        for original, claim in (
            ('Collection is allowed except for quarantined items.', 'All items may be collected.'),
            ('Collection is not allowed.', 'Collection is allowed.'),
        ):
            with self.subTest(original=original):
                self.verify_fixture(original, claim, 'unsupported')

    def test_definition_changes_referent_and_ambiguous_referent_stays_unresolved(self):
        self.verify_fixture('Blue means room 214. Use Blue.', 'The user selected room 215.', 'unsupported')
        self.verify_fixture('Use that room; the earlier alternatives are not identified.',
                            'The user selected room 214.', 'unresolved')

    def test_attribution_uncertainty_time_and_motive_strengthening_stay_unsupported(self):
        for original, claim in (
            ('I propose noon. Noor says access may be restricted.', 'Access is verified to be restricted.'),
            ('I propose noon because I think access is restricted.', 'The user chose noon because access is restricted.'),
            ('Start at eleven after inspection.', 'Inspection is the reason for choosing eleven.'),
            ('Start after inspection; the day is unknown.', 'The start is on 2026-11-14.'),
        ):
            with self.subTest(claim=claim):
                self.verify_fixture(original, claim, 'unsupported')

    def test_attributed_reason_and_sequence_can_be_preserved_without_inventing_causality(self):
        self.verify_fixture('I propose noon because I think access is restricted. Start Wednesday at eleven after inspection.',
            'The user chose Wednesday at eleven after inspection and the proposed noon otherwise; '
            'the assistant gave its belief about access as its reason for noon.', 'accepted')

    def test_missing_required_support_is_unresolved(self):
        self.verify_fixture('The referenced terms are not supplied.',
                            'The user chose the option under the earlier terms.', 'unresolved')

    def test_surrounding_source_restriction_overrides_apparent_short_span_entailment(self):
        prefix = 'Collection is available.'
        calls = self.verify_fixture(prefix + ' x' * 12000 + ' Only after induction and signed release.' + ' y' * 12000,
            'Collection is unconditionally available.', 'unsupported', prefix=prefix, chunk_only=True)
        scans = [r for r in calls if any(row.get('coverage_scan') for row in r.records)]
        self.assertTrue(scans)
        self.assertTrue(all('exact asserted proposition, not completeness of the source inventory' in r.instructions
                            for r in scans))


class EntailmentCoverageReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.queue, self.engine = CaptureQueue(self.store), MemoryEngine(self.store)
        self.proposal = self.queue.capture_event('fiction', 'proposal',
            'I propose the late slot. My reason is the morning allocation.',
            actor='assistant', source='fixture://proposal', scope='lab', position=1,
            recorded_at='2026-01-01T00:00:00+00:00')
        self.target = self.queue.capture_event('fiction', 'target', 'Use that proposed late slot.',
            actor='Mira', source='fixture://target', scope='lab', position=2,
            recorded_at='2026-01-01T00:01:00+00:00')
        self.old_text = 'The user chose the proposed late slot.'
        prefix = 'I propose the late slot.'
        self.store.append_assertion(assertion_id='old', text=self.old_text, actor=self.target.actor,
            source=self.target.source, episode=self.target.session_id, scope=self.target.scope,
            recorded_at=self.target.recorded_at, metadata={'kind': 'decision', 'excerpt': self.target.prompt,
                'source_span': {'start': 0, 'end': len(self.target.prompt)}, 'evidence_refs': [
                    {'event_id': self.target.id, 'start': 0, 'end': len(self.target.prompt)},
                    {'event_id': self.proposal.id, 'start': 0, 'end': len(prefix)}]})
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (self.target.id, 'legacy', '2026-01-02T00:00:00+00:00', json.dumps({'assertion_ids': ['old']})))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',extracted_ids=? WHERE event_id=?",
                       (json.dumps(['old']), self.target.id))
        self.addition = {'text': 'The user chose the late slot; the assistant gave the morning allocation as its reason.',
                         'quote': self.target.prompt, 'kind': 'decision', 'evidence': [
                             {'event_id': self.proposal.id, 'quote': prefix, 'relation': 'antecedent'},
                             {'event_id': self.proposal.id, 'quote': 'My reason is the morning allocation.', 'relation': 'reason'}]}
        self.calls = []

    def reviewer(self, *, prior_status='accepted', addition_status='accepted', withdrawal_status='unresolved'):
        def provider(request):
            self.calls.append(request)
            self.assertLessEqual(len(format_prompt(request)), 60_000)
            if request.task == 'Verify contextual assertion interpretations':
                return json.dumps({'verdicts': [{'index': row['index'],
                    'status': prior_status if row['text'] == self.old_text else addition_status,
                    'reason': 'Independent supplied-source proposition judgment.'}
                    for row in request.records if row.get('role') == 'interpretation']})
            if request.task == 'Verify source interpretation withdrawals':
                return json.dumps({'withdrawals': [{'assertion_id': row['assertion_id'], 'status': withdrawal_status,
                    'reason': 'Omitting a separate explanation does not disprove the bounded choice.',
                    'evidence': [{'event_id': self.target.id, 'quote': self.target.prompt}]}
                    for row in request.records if row.get('role') == 'withdrawal_hypothesis']})
            if request.task == 'Review source material coverage':
                present = any(row.get('role') == 'interpretation' and row['text'] == self.addition['text'] for row in request.records)
                return json.dumps({'source_coverage': {'status': 'accepted' if present else 'incomplete',
                    'reason': 'The separate attributed reason is material source inventory.',
                    'omissions': [] if present else [{'quote': self.target.prompt,
                        'reason': 'Preserve the assistant-stated reason without user motive or world endorsement.'}]}})
            if request.task == 'Repair omitted source meanings':
                return json.dumps({'assertions': [self.addition]})
            raise AssertionError(request.task)
        return provider

    def publish(self, **kwargs):
        worker = LinkWorker(self.queue, self.engine, self.reviewer(**kwargs))
        for _ in range(12):
            if worker.run_once() is None:
                break
        return self.queue.extraction_history(self.target.id)[-1]

    def test_true_prior_has_independent_retained_receipt_and_new_reason_is_introduced(self):
        old = asdict(self.store.get_assertion('old'))
        history = self.queue.extraction_history(self.target.id)[0]
        result = self.publish()
        revision = result['source_revision']
        self.assertEqual(revision['status'], 'accepted_complete')
        decisions = {row['assertion_id']: row for row in revision['reconciliation']}
        self.assertEqual(decisions['old']['decision'], 'retained')
        self.assertEqual(decisions['old']['verification']['status'], 'accepted')
        introduced = [key for key, row in decisions.items() if row['decision'] == 'introduced']
        self.assertEqual(len(introduced), 1)
        self.assertEqual(self.store.get_assertion(introduced[0]).text, self.addition['text'])
        self.assertEqual(asdict(self.store.get_assertion('old')), old)
        self.assertEqual(self.queue.extraction_history(self.target.id)[0], history)
        self.assertFalse(any(r.task.startswith('Extract') or 'withdrawal' in r.task for r in self.calls))
        coverage = [row for row in result['context_recovery'] if row['operation'] == 'source_coverage']
        self.assertEqual([row['status'] for row in coverage], ['unresolved', 'accepted'])

    def test_rejected_prior_with_unresolved_withdrawal_is_not_force_retained(self):
        result = self.publish(prior_status='unsupported')
        self.assertEqual(result['source_revision']['status'], 'unresolved')
        self.assertNotIn('old', {row['assertion_id'] for row in result['source_revision']['reconciliation']})
        self.assertEqual(result['context_recovery'][-1]['status'], 'accepted')
        self.assertIn('old', capture(self.engine).assertions)

    def test_failed_new_addition_preserves_true_prior_and_does_not_certify_completeness(self):
        result = self.publish(addition_status='unsupported')
        self.assertEqual(result['source_revision']['status'], 'unresolved')
        self.assertEqual(set(capture(self.engine).assertions), {'old'})
        self.assertEqual(result['source_revision']['reconciliation'][0]['decision'], 'retained')
        self.assertEqual(sum(r.task == 'Repair omitted source meanings' for r in self.calls), 2)

    def test_ambiguous_prior_remains_unresolved_even_when_new_inventory_is_complete(self):
        result = self.publish(prior_status='unresolved')
        self.assertEqual(result['source_revision']['status'], 'unresolved')
        self.assertNotIn('old', {row['assertion_id'] for row in result['source_revision']['reconciliation']})
        self.assertIn('old', capture(self.engine).assertions)
        self.assertFalse(any(r.task == 'Verify source interpretation withdrawals' for r in self.calls))

    def test_v6_audit_is_immutable_and_reaudited_once_with_new_contract_identity(self):
        from prototypes.assertion_memory import source_coverage, source_reprocessing
        old_protocol = 'source-material-coverage-v6:source-message-clock-v1'
        with patch.object(source_coverage, 'PROTOCOL', old_protocol), patch.object(
                source_reprocessing, 'PROTOCOL', 'source-reconciliation/v1:' + old_protocol):
            old_fingerprint = self.queue.freeze_input(self.target.id).fingerprint
            self.publish()
            old_history = self.queue.extraction_history(self.target.id)
        self.assertNotEqual(self.queue.freeze_input(self.target.id).fingerprint, old_fingerprint)
        result = self.publish()
        history = self.queue.extraction_history(self.target.id)
        self.assertEqual(history[:-1], old_history)
        self.assertEqual(result['source_revision']['protocol'], PROTOCOL)
        self.assertEqual(result['source_revision']['status'], 'accepted_complete')
        self.assertTrue(all(row['decision'] == 'retained' for row in result['source_revision']['reconciliation']))
        count = len(self.calls)
        self.publish()
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.queue.extraction_history(self.target.id), history)

    def test_v7_outcome_and_prompt_contract_reaudits_once_under_new_identity(self):
        from prototypes.assertion_memory import source_coverage, source_reprocessing
        old_coverage='source-material-coverage-v7:source-message-clock-v1'
        with patch.object(source_coverage,'PROTOCOL',old_coverage),patch.object(
                source_reprocessing,'PROTOCOL','source-reconciliation/v2:'+old_coverage):
            prior_fingerprint=self.queue.freeze_input(self.target.id).fingerprint
            self.publish()
            old_history=self.queue.extraction_history(self.target.id)
        self.assertNotEqual(self.queue.freeze_input(self.target.id).fingerprint,prior_fingerprint)
        result=self.publish()
        self.assertEqual(self.queue.extraction_history(self.target.id)[:-1],old_history)
        self.assertEqual(result['source_revision']['protocol'],PROTOCOL)
        self.assertEqual(result['source_revision']['status'],'accepted_complete')
        count=len(self.calls)
        self.publish()
        self.assertEqual(len(self.calls),count)
        self.assertEqual(self.queue.extraction_history(self.target.id)[:-1],old_history)

    def test_revision_completion_is_knowledge_boundary_and_old_source_view_pins_are_immutable(self):
        before = capture(self.engine, recorded_as_of='2026-01-03', captured_at='2026-01-03T12:00:00+00:00')
        self.engine.define_view(ViewSpec('slots', 'Slots', 'Which slot?', scopes=('lab',)))
        view = self.engine.build_view('slots')
        with self.store.transaction() as db:
            raw = [tuple(row) for row in db.execute('SELECT * FROM memory_raw_events ORDER BY id')]
            view_bytes = db.execute('SELECT snapshot FROM memory_view_versions WHERE id=?', (view.id,)).fetchone()[0]
        result = self.publish()
        introduced = next(row['assertion_id'] for row in result['source_revision']['reconciliation'] if row['decision'] == 'introduced')
        self.assertEqual(set(capture(self.engine).assertions), {'old', introduced})
        self.assertEqual(set(capture(self.engine, recorded_as_of='2026-01-03').assertions), {'old'})
        self.assertEqual(asdict(_restore_frozen(self.engine, before.descriptor())), asdict(before))
        with self.store.transaction() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM memory_raw_events ORDER BY id')], raw)
            self.assertEqual(db.execute('SELECT snapshot FROM memory_view_versions WHERE id=?', (view.id,)).fetchone()[0], view_bytes)


if __name__ == '__main__':
    unittest.main()
