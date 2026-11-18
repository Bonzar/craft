"""Invented C5-shaped histories; no real provider or private source is used."""
from copy import deepcopy
from dataclasses import replace
import unittest

from prototypes.assertion_memory.answer_source_gaps import semantic_terminal
from prototypes.assertion_memory.capture import RawEvent
from prototypes.assertion_memory.dialogue import _event_record, MAX_CONTEXT_CHARACTERS
from prototypes.assertion_memory.dialogue_context import ContextWindow, MAX_RECOVERY_ROUNDS
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
from prototypes.assertion_memory.source_coverage import _terminal_empty_prefix
from prototypes.assertion_memory.tests import test_answer_source_gaps as gap_fixture


class TerminalEmptyRepairTest(unittest.TestCase):
    setUp=gap_fixture.AnswerSourceGapsTest.setUp
    extracted_gap=gap_fixture.AnswerSourceGapsTest.extracted_gap
    extracted_clarification=gap_fixture.AnswerSourceGapsTest.extracted_clarification
    reviewed=gap_fixture.AnswerSourceGapsTest.reviewed
    gap=gap_fixture.AnswerSourceGapsTest.gap

    def window(self):
        event=RawEvent('target','session','turn','That happened after it finished.',
            'Mira','fixture://target','lab','2026-01-01',{})
        window=ContextWindow(event,[event],[_event_record(event,is_target=True)],
            _event_record,budget=MAX_CONTEXT_CHARACTERS)
        request=[{'operation':'search','query':'missing antecedent','limit':1}]
        for _ in range(MAX_RECOVERY_ROUNDS): window.recover(request)
        return window,request

    def test_checked_no_addition_repair_preserves_empty_prefix_terminal_proof(self):
        window,request=self.window()
        window.audit.insert(2,{'operation':'source_coverage_repair','attempt':1,
            'status':'checked','verdicts':[]})
        proof=_terminal_empty_prefix(window,request)
        self.assertIsNotNone(proof)
        self.assertEqual(proof['kind'],'empty_authorized_prefix')
        self.assertEqual(proof['recovery_rounds'],MAX_RECOVERY_ROUNDS)

    def test_attempted_unverified_failed_or_nonempty_repair_cannot_be_terminal(self):
        for repair in ({'status':'attempted','verdicts':[]}, {'verdicts':[]},
                       {'status':'failed','verdicts':[]}, {'status':'checked'},
                       {'status':'checked','verdicts':None},
                       {'status':'checked','verdicts':['accepted']},
                       {'status':'checked','verdicts':['unsupported']},
                       {'status':'checked','verdicts':['unresolved']}):
            with self.subTest(repair=repair):
                window,request=self.window()
                window.audit.insert(2,{'operation':'source_coverage_repair','attempt':1,**repair})
                self.assertIsNone(_terminal_empty_prefix(window,request))

    def test_empty_repair_does_not_hide_unavailable_source_or_failed_tool(self):
        for defect in ('unavailable_target','unavailable_read','failed_search','not_exhausted'):
            with self.subTest(defect=defect):
                window,request=self.window()
                window.audit.append({'operation':'source_coverage_repair','attempt':1,
                    'status':'checked','verdicts':[]})
                if defect=='unavailable_target':
                    window.event=replace(window.event,metadata={'_source_unavailable':True})
                elif defect=='unavailable_read':
                    window.audit.append({'operation':'read','status':'unavailable'})
                elif defect=='failed_search': window.audit[0]['status']='failed'
                else: window.recovery_rounds-=1
                self.assertIsNone(_terminal_empty_prefix(window,request))

    def test_c5_empty_repair_history_requires_current_answer_review_and_preserves_unknown(self):
        event,result=self.extracted_gap(empty_repair=True)
        repairs=[r for r in result['context_recovery'] if r['operation']=='source_coverage_repair']
        self.assertEqual(repairs,[{'operation':'source_coverage_repair','attempt':1,
            'status':'checked','verdicts':[]}])
        self.assertTrue(semantic_terminal(result))
        self.assertEqual(result['assertion_ids'],[])
        self.assertEqual(self.store.list_assertions(include_unknown_valid=True),[])
        self.assertEqual(result['source_revision']['status'],'unresolved')
        self.assertEqual(sum(r['operation']=='search' for r in result['context_recovery']),MAX_RECOVERY_ROUNDS)
        history=deepcopy(self.queue.extraction_history(event.id))
        _,proof_id=self.extracted_clarification()
        view,check=self.reviewed('addressed',build=True,proof_id=proof_id,
            canonical='The inspection followed completion of the calibration.',question='Which came first?')
        missing=deepcopy(check); missing['overview_review'].pop('source_gap_review')
        unreviewed=self.service._query_result(view,{},self.service.status(scope='lab'),missing,'lab',None)
        self.assertEqual(unreviewed['completeness'],'incomplete')
        self.assertIsNone(unreviewed['source_status']['processing_gaps_apply_to_query'])
        for _ in range(2):
            response=self.service._query_result(view,{},self.service.status(scope='lab'),check,'lab',None)
            self.assertFalse(response['refresh_required'])
            self.assertFalse(response['source_status']['processing_gaps_apply_to_query'])
            self.assertEqual(response['processing_state'],'needs_review')
            self.assertEqual(response['unresolved_count'],1)
            self.assertEqual(response['completeness'],'not_guaranteed')
        self.assertEqual(self.queue.extraction_history(event.id),history)
        frozen=capture(self.engine)
        self.gap('A newly unresolved condition.',turn='new',recorded='2026-01-05',completed='2026-01-06')
        response=self.service._query_result(view,{},self.service.status(scope='lab'),check,'lab',None)
        self.assertTrue(response['refresh_required'])
        self.assertIsNone(response['source_status']['processing_gaps_apply_to_query'])
        self.assertEqual(_restore_frozen(self.engine,frozen.descriptor()).source_gaps(('lab',)),frozen.source_gaps(('lab',)))

    def test_review_side_exhaustion_after_empty_repair_has_same_guarded_terminal_proof(self):
        _,result=self.extracted_gap(empty_repair=True,review_exhaustion=True)
        self.assertTrue(semantic_terminal(result))
        self.assertEqual(result['context_recovery'][-1]['recovery']['kind'],'empty_authorized_prefix')
        self.assertEqual(result['source_revision']['status'],'unresolved')

    def test_empty_repair_late_prefix_invalidates_current_but_preserves_frozen_frontier(self):
        from datetime import datetime, timezone
        self.extracted_gap(empty_repair=True,metadata={'source_position':2})
        view,check=self.reviewed('addressed',build=True)
        historical=capture(self.engine,recorded_as_of='2026-01-02T23:00:00Z')
        self.queue._clock=lambda:datetime(2026,1,5,tzinfo=timezone.utc)
        self.queue.capture_event('invented','late-prefix','The earlier event was calibration.',source='fixture://late',
            actor='assistant',scope='lab',recorded_at='2026-01-01',metadata={'source_position':1})
        response=self.service._query_result(view,{},self.service.status(scope='lab'),check,'lab',None)
        self.assertTrue(response['refresh_required'])
        self.assertFalse(self.service._query_processing_gaps('lab','2026-01-02T23:00:00Z',{},freshness_only=True))
        self.assertEqual(_restore_frozen(self.engine,historical.descriptor()).source_gaps(('lab',)),historical.source_gaps(('lab',)))


if __name__=='__main__': unittest.main()
