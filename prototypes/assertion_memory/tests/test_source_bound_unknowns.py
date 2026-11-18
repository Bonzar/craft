"""Source-bound unknowns require selective original delivery, never raw fact promotion."""
from dataclasses import asdict, replace
from datetime import datetime
import json
import unittest

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate, ModelError
from prototypes.assertion_memory.overview import _model_record
from prototypes.assertion_memory.overview_review import (RetrievalLedger, _coverage_needs, review_overview,
    _make_review_request, _review_contract)
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore

STAMP = '2026-02-01T12:00:00.000000+00:00'
PREFIX = 'I propose the late slot.'
REASON = 'My stated reason is that morning access may be reserved.'
UNKNOWN = 'The reason is not established by the accepted interpretation.'


class Ranker:
    identity = 'fictional-original-question'
    def rank(self, query, candidates, *, limit):
        return [Candidate(k, 1.0, 'fixture') for k in candidates][:limit]


class SourceBoundUnknownTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store, clock=lambda: datetime.fromisoformat(STAMP))
        self.engine = MemoryEngine(self.store)
        self.proposal = self.queue.capture_event('fiction', 'proposal', PREFIX + ' ' + REASON,
            actor='assistant', source='fixture://proposal', scope='fiction')
        self.target = self.queue.capture_event('fiction', 'reply', 'Use the proposed late slot.',
            actor='Mira', source='fixture://reply', scope='fiction')
        self.rows = self.canonical(self.proposal, self.target)
        self.snapshot = self.engine.capture_snapshot(recorded_as_of=STAMP)
        self.frame = {'question': 'What slot was chosen, and what reason did the proposal state?'}

    def canonical(self, proposal, target, key='schedule'):
        refs = [{'event_id': target.id, 'start': 0, 'end': len(target.prompt), 'role': 'user', 'relation': 'statement'},
                {'event_id': proposal.id, 'start': 0, 'end': len(PREFIX), 'role': 'assistant', 'relation': 'antecedent'}]
        row = self.queue.capture(CaptureInput('Mira chose the assistant-proposed late slot.', 'Mira', target.source,
            target.session_id, assertion_id=key, scope='fiction', recorded_at=STAMP, metadata={
                'event_id': target.id, 'excerpt': target.prompt, 'source_span': {'start': 0, 'end': len(target.prompt)},
                'evidence_refs': refs, 'interpretation': {'status': 'model_verified'}}))
        return {key: {**asdict(row), 'id': key, 'disposition': 'assertion'}}

    def ledger(self, rows=None, snapshot=None):
        return RetrievalLedger(Ranker(), rows or self.rows, set(rows or self.rows),
            source_resolver=(snapshot or self.snapshot).source_proof_resolver(self.engine))

    def need(self, **updates):
        return {'quote': UNKNOWN, 'gap_id': 'explanation', 'basis': 'searched_unresolved',
                'source_ids': ['schedule'], 'include_source_evidence': True,
                'reason': 'This asks the identified proposal wording, not only the canonical choice.', **updates}

    def searched(self, ledger):
        ledger.register([{'operation': 'search', 'gap_id': 'explanation', 'query': 'proposal reason', 'limit': 3}], 'global')
        ledger.execute()

    def test_performed_canonical_search_still_registers_exact_original_read(self):
        ledger = self.ledger(); self.searched(ledger)
        trace = _coverage_needs({'unknown_needs': [self.need()]}, UNKNOWN, self.frame, ledger)
        self.assertEqual(trace[0]['acquisition_status'], 'pending')
        self.assertEqual(len(ledger.pending()), 1)
        request = ledger.pending()[0]
        self.assertEqual(request['operation'], 'read')
        self.assertEqual(request['source_ids'], ['schedule'])
        self.assertTrue(request['include_source_evidence'])
        self.assertEqual(ledger.proof_for('global')['bodies'], [])
        ledger.execute()
        self.assertEqual(ledger.pending(), [])
        self.assertTrue(any(b['text'] == self.proposal.prompt and b['source_text_complete'] for b in ledger.proof_for('global')['bodies']))

    def test_original_need_requires_visible_source_ids_and_boolean_proof_flag(self):
        for change in ({'source_ids': []}, {'source_ids': ['hidden']}, {'source_ids': self.target.id},
                       {'include_source_evidence': 'yes'}):
            with self.subTest(change=change):
                ledger = self.ledger(); self.searched(ledger)
                with self.assertRaises(ModelError):
                    _coverage_needs({'unknown_needs': [self.need(**change)]}, UNKNOWN, self.frame, ledger)

    def test_source_specific_original_unknown_also_requires_delivery(self):
        ledger = self.ledger()
        need = self.need(basis='source_specific_question', question_quote='what reason did the proposal state')
        _coverage_needs({'unknown_needs': [need]}, UNKNOWN, self.frame, ledger)
        self.assertEqual(len(ledger.pending()), 1)
        self.assertTrue(ledger.pending()[0]['include_source_evidence'])

    def test_canonical_only_unknown_without_original_question_keeps_existing_scoped_search_path(self):
        ledger = self.ledger(); self.searched(ledger)
        need = self.need(include_source_evidence=False)
        _coverage_needs({'unknown_needs': [need]}, UNKNOWN, self.frame, ledger)
        self.assertEqual(ledger.pending(), [])
        self.assertEqual(ledger.proof_for('global')['bodies'], [])

    def test_read_identity_cannot_be_satisfied_by_other_source_or_canonical_read(self):
        ledger = self.ledger(); self.searched(ledger)
        ledger.register([{'operation': 'read', 'gap_id': 'canonical-read', 'source_ids': ['schedule']}], 'global')
        ledger.execute()
        _coverage_needs({'unknown_needs': [self.need()]}, UNKNOWN, self.frame, ledger)
        self.assertTrue(ledger.pending())
        self.assertFalse(ledger.proof_for('global')['bodies'])

    def test_long_original_delivers_only_exact_accepted_prefix_never_absence_in_tail(self):
        long = self.queue.capture_event('fiction', 'long', PREFIX + ' ' + 'x' * 7000 + ' ' + REASON,
            actor='assistant', source='fixture://long', scope='fiction')
        rows = self.canonical(long, self.target, 'long-schedule')
        ledger = self.ledger(rows, self.engine.capture_snapshot(recorded_as_of=STAMP))
        ledger.register([{'operation': 'read', 'gap_id': 'long-original', 'source_ids': ['long-schedule'],
                          'include_source_evidence': True}], 'global')
        ledger.execute()
        body = next(b for b in ledger.proof_for('global')['bodies'] if b['source_length'] > 6000)
        self.assertEqual(body['text'], PREFIX)
        self.assertEqual(body['source_span'], {'start': 0, 'end': len(PREFIX)})
        self.assertFalse(body['source_text_complete'])
        self.assertNotIn(REASON, body['text'])

    def test_short_original_reveals_reason_beyond_accepted_prefix_without_accepting_it(self):
        ledger = self.ledger(); self.searched(ledger)
        _coverage_needs({'unknown_needs': [self.need()]}, UNKNOWN, self.frame, ledger)
        ledger.execute()
        body = next(b for b in ledger.proof_for('global')['bodies'] if REASON in b['text'])
        self.assertTrue(body['source_text_complete'])
        self.assertNotIn(REASON, ledger.visible['schedule']['text'])
        self.assertEqual(len(self.store.list_assertions()), 1)

    def test_source_bound_need_is_not_reset_after_original_delivery(self):
        ledger = self.ledger(); self.searched(ledger)
        _coverage_needs({'unknown_needs': [self.need()]}, UNKNOWN, self.frame, ledger)
        ledger.execute()
        before = ledger.proof_for('global')
        _coverage_needs({'unknown_needs': [self.need()]}, UNKNOWN, self.frame, ledger)
        self.assertEqual(ledger.pending(), [])
        self.assertEqual(ledger.execute(), [])
        self.assertEqual(ledger.proof_for('global'), before)
        self.assertTrue(before['anchors'])

    def test_actual_overview_cannot_accept_canonical_search_unknown_before_original_is_delivered(self):
        calls = []
        def provider(request):
            calls.append(request)
            proof = next((r for r in request.records if r.get('role') == 'source_evidence'), None)
            if request.task.startswith('Review'):
                if len([r for r in calls if r.task.startswith('Review')]) == 1:
                    need = self.need(basis='lookup_required', query='proposal reason', limit=3,
                                     include_source_evidence=False)
                    need.pop('source_ids')
                else:
                    need = self.need()
                return json.dumps({'status': 'accepted', 'coverage': 'all_claims',
                    'reason': 'Keep the original interpretation limitation explicit.', 'issues': [],
                    'unknown_needs': [need]})
            if proof:
                self.assertTrue(any(REASON in b['text'] for b in proof['bodies']))
            return 'Mira chose the proposed late slot. [source:schedule] ' + UNKNOWN
        engine = MemoryEngine(self.store, model=provider, candidate_search=Ranker(), verify_overviews=True)
        spec = ViewSpec('slot', 'Slot', self.frame['question'], scopes=('fiction',), root_ids=('schedule',))
        final, _, _, audit = review_overview(engine, spec, list(self.rows.values()), [], [],
            self.snapshot.assertions, [], snapshot=self.snapshot)
        self.assertEqual(audit['rounds'], 3)
        self.assertTrue(audit['source_evidence']['bodies'])
        self.assertTrue(any(REASON in b['text'] for b in audit['source_evidence']['bodies']))
        self.assertIn(UNKNOWN, final)
        self.assertNotIn(REASON, final)
        self.assertEqual(len(self.store.list_assertions()), 1)

    def test_contract_repair_cannot_drop_or_downgrade_source_bound_unknown_intent(self):
        for basis in ('searched_unresolved', 'source_specific_question'):
            with self.subTest(basis=basis):
                ledger = self.ledger()
                request = _make_review_request([_model_record(self.rows['schedule'])], [], UNKNOWN,
                    self.frame, ledger, 'global')
                need = self.need(basis=basis, question_quote='what reason did the proposal state')
                original = {'status': 'accepted', 'coverage': 'invalid', 'reason': 'Check original.',
                    'issues': [], 'unknown_needs': [need]}
                repaired = {**original, 'coverage': 'all_claims',
                    'unknown_needs': [{**need, 'include_source_evidence': False}]}
                responses = iter([original, repaired])
                with self.assertRaisesRegex(ModelError, 'retrieval intent'):
                    _review_contract(lambda r: json.dumps(next(responses)), request,
                        visible_ids=set(self.rows))

    def test_raw_hash_scope_role_and_range_mismatch_fail_closed(self):
        for change in ({'source_sha256': 'forged'}, {'role': 'user'}, {'end': 99999}):
            with self.subTest(change=change):
                rows = json.loads(json.dumps(self.rows))
                rows['schedule']['metadata']['evidence_refs'][1].update(change)
                ledger = self.ledger(rows, replace(self.snapshot, assertions=rows))
                ledger.register([{'operation': 'read', 'gap_id': 'proof', 'source_ids': ['schedule'],
                                  'include_source_evidence': True}], 'global')
                ledger.execute()
                self.assertEqual(ledger.proof_for('global')['bodies'], [])
                self.assertTrue(ledger.proof_for('global')['unavailable'])


if __name__ == '__main__':
    unittest.main()
