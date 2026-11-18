"""Invented deterministic contract oracles; these do not measure model semantics."""
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
from prototypes.assertion_memory.overview_review import review_overview
from prototypes.assertion_memory.model import ModelError, format_prompt


class AnswerSourceGapsTest(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        self.store = MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)
        self.queue._clock=lambda:datetime(2026,1,1,tzinfo=timezone.utc)
        self.engine = MemoryEngine(self.store)
        self.service = MemoryService(self.store, self.engine)

    def gap(self, text='That happened after it finished.', *, turn='gap', failed=False,
            recorded='2026-01-01', completed='2026-01-02'):
        event = self.queue.capture_event('invented', turn, text, source='fixture://'+turn,
            actor='Mira', scope='lab', recorded_at=recorded)
        result = {'assertion_ids': [], 'reviews': [{'kind':'source_coverage','status':'unresolved',
            'reason':'Referents remain unresolved.'}], 'context_recovery': [] if failed else [
                {'operation':'source_coverage','protocol':'fixture-v1','status':'unresolved',
                 'attempt':0,'omissions':[],'reason':'Referents remain unresolved.'}],
            'source_revision':{'schema':'source-interpretation/v1','event_id':event.id,
                'protocol':'fixture-v1','input_fingerprint':'frozen-input','status':'failed' if failed else 'unresolved',
                'assertion_ids':[],'reconciliation':[]}}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,turn,completed,json.dumps(result)))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',proposals=? WHERE event_id=?",
                (json.dumps(result['reviews']),event.id))
        return event

    def test_stable_zero_canonical_gap_limits_completeness_without_perpetual_refresh(self):
        self.gap()
        self.assertTrue(self.service._query_processing_gaps('lab',None,{}))
        self.assertFalse(self.service._query_processing_gaps('lab',None,{},freshness_only=True))

    def test_failed_unparsed_and_new_raw_keep_freshness_gate(self):
        self.gap(failed=True)
        self.assertTrue(self.service._query_processing_gaps('lab',None,{},freshness_only=True))

    def test_snapshot_pins_zero_canonical_inventory_and_historical_cutoff(self):
        old=self.gap()
        snapshot=capture(self.engine,recorded_as_of='2026-01-03')
        inventory=snapshot.source_gaps(('lab',))
        self.assertTrue(inventory['complete'])
        self.assertEqual([row['event_id'] for row in inventory['gaps']],[old.id])
        self.gap(turn='future',recorded='2026-01-04',completed='2026-01-05')
        restored=_restore_frozen(self.engine,snapshot.descriptor())
        self.assertEqual(restored.source_gaps(('lab',)),inventory)
        self.assertEqual(capture(self.engine,recorded_as_of='2025-12-31').source_gaps(('lab',))['gaps'],[])

    def test_bounded_inventory_never_equates_omission_with_nonmateriality(self):
        for index in range(18): self.gap(turn=str(index))
        inventory=capture(self.engine).source_gaps(('lab',))
        self.assertFalse(inventory['complete'])
        self.assertEqual(inventory['total'],18)
        self.assertGreater(inventory['unassessed_count'],0)

    def reviewed(self, disposition='nonmaterial', *, text=None, qualified=True, omit=False, build=False,
                 canonical='Printer serial is PX-41.',question='What is the reported printer serial?',repair_reclassifies=False,
                 canonical_recorded='2026-01-01',repair_reverses_qualification=False,proof_id='settled'):
        self.requests=[]
        if self.store.get_assertion(proof_id) is None:
            self.store.append_assertion(assertion_id=proof_id,text=canonical,
                actor='Mira',source='fixture://settled',episode='invented',scope='lab',recorded_at=canonical_recorded)
        snapshot=capture(self.engine)
        spec=ViewSpec('answer','Answer',question,root_ids=(proof_id,),scopes=('lab',))
        def provider(request):
            self.requests.append(request)
            if request.task=='Review grounded memory overview':
                inventory=next((r for r in request.records if r.get('role')=='source_gap_inventory'),None)
                value={'status':'accepted','coverage':'all_claims','reason':'Fixture semantic oracle.',
                    'issues':[],'context_requests':[],'unknown_needs':[]}
                if inventory and not omit:
                    repaired=repair_reclassifies and any(r.get('role')=='review_contract_feedback' for r in request.records)
                    value['source_gap_dispositions']=[{'event_id':row['event_id'],'run_id':row['run_id'],
                        'disposition':'nonmaterial' if repaired else disposition,'reason':'Gardening ambiguity cannot alter a reported serial.' if disposition=='nonmaterial' else 'Explicit canonical statement settles the asked facet.',
                        'source_ids':[proof_id] if disposition=='addressed' else [],
                        'answer_qualified':True if repair_reverses_qualification and any(r.get('role')=='review_contract_feedback' for r in request.records) else qualified,
                        'qualification_quote':text.split(' [source:')[0] if text else 'The condition remains unresolved; permission cannot be established.'}
                        for row in inventory['gaps']]
                    value['unassessed_gaps_qualified']=qualified
                    value['unassessed_gap_qualification']={'answer_qualified':qualified,
                        'quote':text.split(' [source:')[0] if text else '',
                        'reason':'Fixture checks the full answer conservatively gates action.'}
                return json.dumps(value)
            return text or canonical+f' [source:{proof_id}]'
        self.engine.model=provider
        if build:
            self.engine=MemoryEngine(self.store,model=provider,verify_overviews=True)
            self.engine.define_view(spec)
            receipts={}
            view=self.engine.build_view(spec.id,_snapshot=snapshot,_receipts=receipts)
            return view,receipts[spec.id]
        from prototypes.assertion_memory.evidence import _expand_evidence
        evidence,edges=_expand_evidence(set(snapshot.assertions),snapshot.assertions,[])
        return review_overview(self.engine,spec,evidence,edges,[],snapshot.assertions,[],snapshot=snapshot)

    def test_unrelated_zero_canonical_gap_is_reviewed_before_answer_waiver(self):
        self.gap('Move that after it sprouts.')
        final,_,_,review=self.reviewed()
        self.assertFalse(review['source_gap_review']['processing_gaps_apply_to_query'])
        writer=next(r for r in self.requests if r.task!='Review grounded memory overview')
        reviewer=next(r for r in self.requests if r.task=='Review grounded memory overview')
        self.assertNotIn('Move that after it sprouts.',format_prompt(writer))
        self.assertIn('Move that after it sprouts.',format_prompt(reviewer))
        self.assertTrue(all(len(format_prompt(r))<=60000 for r in self.requests))

    def test_later_canonical_answer_can_address_gap_without_rewriting_history(self):
        event=self.gap()
        history=self.queue.extraction_history(event.id)
        _,_,_,review=self.reviewed('addressed',canonical='The inspection followed completion of the calibration.',
            question='Which came first, the inspection or the calibration?',canonical_recorded='2026-01-03')
        self.assertFalse(review['source_gap_review']['processing_gaps_apply_to_query'])
        self.assertEqual(history,self.queue.extraction_history(event.id))
        self.assertNotIn('settled',capture(self.engine,recorded_as_of='2026-01-02T23:00:00Z').assertions)

    def test_missing_disposition_cannot_publish_no_material_gap_answer(self):
        self.gap()
        with self.assertRaises(ModelError): self.reviewed(omit=True)

    def test_unknown_word_does_not_qualify_positive_permission(self):
        self.gap('The permit applies only when that condition is met.')
        # Independent semantic oracle marks this granting answer unqualified;
        # the guard must honor it despite the literal word "unknown".
        with self.assertRaises(ModelError):
            self.reviewed('material',text='The condition is unknown, but operation is allowed now. [source:settled]',qualified=False)

    def test_material_gap_retains_incompleteness_after_qualified_review(self):
        self.gap()
        text='The condition remains unresolved; permission cannot be established. [source:settled]'
        _,_,_,review=self.reviewed('material',text=text)
        self.assertTrue(review['source_gap_review']['processing_gaps_apply_to_query'])

    def test_scope_needs_review_remains_while_answer_local_waiver_clears_refresh(self):
        self.gap('Move that after it sprouts.')
        view,check=self.reviewed(build=True)
        service=MemoryService(self.store,self.engine)
        response=service._query_result(view,{},service.status(scope='lab'),check,'lab',None)
        self.assertEqual(response['processing_state'],'needs_review')
        self.assertFalse(response['refresh_required'])
        self.assertEqual(response['completeness'],'not_guaranteed')
        self.assertFalse(response['source_status']['processing_gaps_apply_to_query'])

    def test_omitted_inventory_has_unknown_applicability_even_with_qualified_answer(self):
        for index in range(18): self.gap(turn=str(index))
        text='The reported serial is PX-41; unassessed source limitations prevent an exhaustive conclusion. [source:settled]'
        _,_,_,review=self.reviewed(text=text)
        self.assertIsNone(review['source_gap_review']['processing_gaps_apply_to_query'])

    def test_new_zero_canonical_gap_cannot_bypass_review_via_semantic_reuse(self):
        self.reviewed(build=True)
        self.gap('Move that after it sprouts.')
        self.reviewed(build=True)
        self.assertTrue(any(r.task=='Review grounded memory overview' for r in self.requests))

    def test_actual_late_context_invalidates_current_not_historical_frontier(self):
        from datetime import datetime, timezone
        now=[datetime(2026,1,1,tzinfo=timezone.utc)]; self.queue._clock=lambda:now[0]
        hidden=self.queue.capture_event('invented','hidden','It means calibration.',
            source='fixture://hidden',actor='assistant',scope='lab',await_origin=True,recorded_at='2026-01-01')
        self.gap()
        frozen=capture(self.engine,recorded_as_of='2026-01-02T23:00:00Z')
        now[0]=datetime(2026,1,3,tzinfo=timezone.utc)
        self.queue.capture_event('invented','hidden',hidden.prompt,source=hidden.source,
            actor='assistant',scope='lab',recorded_at='2026-01-01')
        self.assertTrue(self.service._query_processing_gaps('lab',None,{},freshness_only=True))
        self.assertFalse(self.service._query_processing_gaps('lab','2026-01-02T23:00:00Z',{},freshness_only=True))
        self.assertEqual(_restore_frozen(self.engine,frozen.descriptor()).source_gaps(('lab',)),frozen.source_gaps(('lab',)))

    def test_new_raw_without_canonical_output_keeps_freshness_gate(self):
        self.gap()
        self.queue.capture_event('invented','pending','A potentially material new condition.',source='fixture://pending',scope='lab')
        self.assertTrue(self.service._query_processing_gaps('lab',None,{},freshness_only=True))

    def test_contract_repair_cannot_reclassify_material_gap_to_evade_qualification(self):
        self.gap('The permit applies only when that condition is met.')
        with self.assertRaises(ModelError):
            self.reviewed('material',qualified=False,repair_reclassifies=True)

    def test_stable_unassessed_gap_is_incomplete_without_refresh_remediation(self):
        self.gap()
        view,check=self.reviewed(build=True)
        check['overview_review'].pop('source_gap_review')
        service=MemoryService(self.store,self.engine)
        response=service._query_result(view,{},service.status(scope='lab'),check,'lab',None)
        self.assertFalse(response['refresh_required'])
        self.assertEqual(response['completeness'],'incomplete')
        self.assertIsNone(response['source_status']['processing_gaps_apply_to_query'])

    def test_new_completed_zero_canonical_gap_invalidates_old_disposition_frontier(self):
        self.gap()
        view,check=self.reviewed(build=True)
        self.gap('A second unresolved condition.',turn='second')
        service=MemoryService(self.store,self.engine)
        response=service._query_result(view,{},service.status(scope='lab'),check,'lab',None)
        self.assertTrue(response['refresh_required'])
        self.assertIsNone(response['source_status']['processing_gaps_apply_to_query'])

    def test_cli_uses_same_current_answer_gap_receipt(self):
        from prototypes.assertion_memory.__main__ import _query_diagnostics
        self.gap()
        view,check=self.reviewed(build=True)
        result=_query_diagnostics(self.store,self.engine,view,check)
        self.assertFalse(result['refresh_required'])
        self.assertEqual(result['completeness'],'not_guaranteed')
        self.assertEqual(result['source_status']['latest_scope_health'][0]['unresolved_count'],1)

    def test_contract_repair_cannot_reverse_negative_semantic_permission_verdict(self):
        self.gap('The permit applies only when that condition is met.')
        with self.assertRaises(ModelError):
            self.reviewed('material',qualified=False,repair_reverses_qualification=True,
                text='The condition is unknown, but operation is allowed now. [source:settled]')

    def test_unparsed_completed_job_cannot_claim_no_processing_gap(self):
        view,check=self.reviewed(build=True)
        event=self.queue.capture_event('new-session','unparsed','A condition not yet interpreted.',
            source='fixture://unparsed',scope='lab')
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',proposals='[]' WHERE event_id=?",(event.id,))
        service=MemoryService(self.store,self.engine)
        response=service._query_result(view,{},service.status(scope='lab'),check,'lab',None)
        self.assertTrue(response['refresh_required'])
        self.assertEqual(response['completeness'],'incomplete')
        self.assertTrue(response['source_status']['processing_gaps_apply_to_query'])


    def extracted_gap(self, *, failure=None, text='That happened after it finished.', metadata=None,
                      empty_repair=False, review_exhaustion=False, operational=False):
        """Exercise actual bounded extraction/coverage/repair and ledger publication."""
        from datetime import datetime, timezone
        from prototypes.assertion_memory.worker import LinkWorker
        event=self.queue.capture_event('invented','exhausted',text,source='fixture://exhausted',
            actor='Mira',scope='lab',recorded_at='2026-01-01',metadata=metadata or {})
        self.source_calls=[]
        def provider(request):
            self.source_calls.append(request)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions':[]})
            if failure=='provider': raise ModelError('Fixture provider unavailable.')
            if failure=='format': return 'unparseable fixture'
            if request.task=='Review source material coverage':
                if empty_repair and sum(r.task==request.task for r in self.source_calls)<=2:
                    return json.dumps({'context_requests':[{'operation':'search','query':'missing antecedent','limit':1}]})
                if review_exhaustion and any(r.task=='Repair omitted source meanings' for r in self.source_calls):
                    return json.dumps({'context_requests':[{'operation':'search','query':'missing antecedent','limit':1}]})
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'Unresolved source referents.',
                    'omissions':[{'quote':text[:32], 'reason':'The relation requires an antecedent.'}]}})
            if request.task=='Repair omitted source meanings':
                if empty_repair and sum(r.task==request.task for r in self.source_calls)==1:
                    return json.dumps({'assertions':[]})
                if failure=='late-malformed' and sum(r.task==request.task for r in self.source_calls)>6:
                    return json.dumps({'context_requests':'malformed after six real searches'})
                return json.dumps({'context_requests':[{'operation':'search','query':'missing antecedent','limit':1}]})
            raise AssertionError(request.task)
        self.queue._clock=lambda:datetime(2026,1,2,tzinfo=timezone.utc)
        job=LinkWorker(self.queue,self.engine,provider).run_once()
        self.assertEqual(job.status,'pending' if operational else 'completed')
        result=self.queue.extraction_history(event.id)[-1]
        self.assertEqual(result['source_revision']['status'],'failed' if operational else 'unresolved')
        self.assertEqual(bool(result['context_recovery'][-1].get('operational_error')),operational)
        return event,result

    def extracted_clarification(self):
        from datetime import datetime, timezone
        from prototypes.assertion_memory.worker import LinkWorker
        text='The inspection followed completion of the calibration.'
        event=self.queue.capture_event('invented','clarification',text,source='fixture://clarification',
            actor='Mira',scope='lab',recorded_at='2026-01-03')
        def provider(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions':[{'text':text,'quote':text}]})
            if request.task.startswith('Verify'):
                return json.dumps({'verdicts':[{'index':row['index'],'status':'accepted','reason':'Explicit target statement.'}
                    for row in request.records if row.get('role')=='interpretation']})
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'accepted','reason':'Explicit statement covered.','omissions':[]}})
            raise AssertionError(request.task)
        self.queue._clock=lambda:datetime(2026,1,4,tzinfo=timezone.utc)
        worker=LinkWorker(self.queue,self.engine,provider)
        self.assertEqual(worker.run_once().status,'completed')
        self.assertEqual(worker.run_once().status,'completed')
        return event,self.queue.extraction_history(event.id)[-1]['assertion_ids'][0]

    def test_real_exhausted_empty_prefix_gap_has_current_service_and_cli_answer(self):
        from prototypes.assertion_memory.__main__ import _query_diagnostics
        from prototypes.assertion_memory.dialogue_context import MAX_RECOVERY_ROUNDS
        event,result=self.extracted_gap()
        self.assertFalse(result['context_truncated'])
        self.assertEqual(sum(r.task=='Repair omitted source meanings' for r in self.source_calls),MAX_RECOVERY_ROUNDS+1)
        self.assertIn('exhausted bounded context recovery',result['reviews'][-1]['reason'])
        history=self.queue.extraction_history(event.id)
        _,proof_id=self.extracted_clarification()
        view,check=self.reviewed('addressed',build=True,proof_id=proof_id,
            canonical='The inspection followed completion of the calibration.',question='Which came first?')
        self.assertFalse(check['overview_review']['source_gap_review']['processing_gaps_apply_to_query'])
        response=self.service._query_result(view,{},self.service.status(scope='lab'),check,'lab',None)
        self.assertFalse(response['refresh_required'])
        self.assertEqual(response['completeness'],'not_guaranteed')
        self.assertEqual(response['processing_state'],'needs_review')
        self.assertEqual(response['unresolved_count'],1)
        self.assertFalse(response['source_status']['processing_gaps_apply_to_query'])
        cli=_query_diagnostics(self.store,self.engine,view,check)
        self.assertFalse(cli['refresh_required'])
        self.assertEqual(cli['completeness'],'not_guaranteed')
        self.assertEqual(history,self.queue.extraction_history(event.id))
        self.assertNotIn(proof_id,capture(self.engine,recorded_as_of='2026-01-02T23:00:00Z').assertions)

    def test_real_coverage_provider_format_and_budget_errors_keep_refresh(self):
        for failure in ('provider','format','budget','late-malformed'):
            with self.subTest(failure=failure):
                self.setUp()
                event,_=self.extracted_gap(failure=failure,text='x'*200000 if failure=='budget' else 'That happened after it finished.',operational=True)
                view,check=self.reviewed('addressed',build=True,text='Unassessed sources prevent an exhaustive answer. [source:settled]')
                response=self.service._query_result(view,{},self.service.status(scope='lab'),check,'lab',None)
                self.assertTrue(response['refresh_required'])
                self.assertEqual(response['completeness'],'incomplete')

    def test_real_exhausted_gap_with_missing_origin_or_prefix_is_not_empty_prefix_terminal(self):
        for origin in ('unconfirmed','explicit_gap','confirmed'):
            with self.subTest(origin=origin):
                self.setUp()
                metadata={'unavailable_context_before':[{'reason':'Imported prefix absent.'}]} if origin=='explicit_gap' else {}
                if origin!='explicit_gap':
                    self.queue.capture_event('invented','earlier','Earlier context.',source='fixture://earlier',
                        actor='assistant',scope='lab',await_origin=origin=='unconfirmed',recorded_at='2026-01-01')
                self.extracted_gap(metadata=metadata,operational=True)
                self.assertTrue(self.service._query_processing_gaps('lab',None,{},freshness_only=True))

    def test_real_exhausted_gap_new_prefix_invalidates_current_not_frozen_answer(self):
        from datetime import datetime, timezone
        self.extracted_gap(metadata={'source_position':2})
        view,check=self.reviewed('addressed',build=True)
        historical=capture(self.engine,recorded_as_of='2026-01-02T23:00:00Z')
        self.queue._clock=lambda:datetime(2026,1,5,tzinfo=timezone.utc)
        self.queue.capture_event('invented','late-prefix','The earlier event was calibration.',source='fixture://late',
            actor='assistant',scope='lab',recorded_at='2026-01-01',metadata={'source_position':1})
        response=self.service._query_result(view,{},self.service.status(scope='lab'),check,'lab',None)
        self.assertTrue(response['refresh_required'])
        self.assertFalse(self.service._query_processing_gaps('lab','2026-01-02T23:00:00Z',{},freshness_only=True))
        self.assertEqual(_restore_frozen(self.engine,historical.descriptor()).source_gaps(('lab',)),historical.source_gaps(('lab',)))

    def test_old_positive_claim_cannot_address_its_changed_input_gap(self):
        self.extracted_gap()
        event,proof_id=self.extracted_clarification()
        # A completed modern revalidation encountered different input, retained no
        # new positive proof, and deliberately preserved the historical claim.
        history=self.queue.extraction_history(event.id)[-1]
        revision={**history['source_revision'],'input_fingerprint':'different-input',
            'status':'unresolved','assertion_ids':[],'reconciliation':[]}
        result={'assertion_ids':[],'reviews':[{'kind':'source_coverage','status':'unresolved','reason':'New condition unresolved.'}],
            'context_recovery':[{'operation':'source_coverage','protocol':'fixture-v1','attempt':0,'omissions':[],
                'status':'unresolved'}], 'source_revision':revision}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,'different-input','2026-01-05',json.dumps(result)))
        projected=capture(self.engine).assertions[proof_id]['current_source_revalidation']
        self.assertNotEqual(projected['source_run_id'],projected['last_positive_claim_run_id'])
        with self.assertRaises(ModelError):
            self.reviewed('addressed',proof_id=proof_id)


    def test_prior_positive_at_unchanged_input_can_still_address_a_gap(self):
        self.extracted_gap()
        event,proof_id=self.extracted_clarification()
        history=self.queue.extraction_history(event.id)[-1]
        revision={**history['source_revision'],'status':'unresolved','assertion_ids':[],'reconciliation':[]}
        result={'assertion_ids':[],'reviews':[{'kind':'source_coverage','status':'unresolved','reason':'Same input limitation.'}],
            'context_recovery':[{'operation':'source_coverage','protocol':'fixture-v1','attempt':0,'omissions':[],
                'status':'unresolved'}], 'source_revision':revision}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,'same-input-followup','2026-01-05',json.dumps(result)))
        _,_,_,review=self.reviewed('addressed',proof_id=proof_id)
        self.assertFalse(review['source_gap_review']['processing_gaps_apply_to_query'])


if __name__=='__main__': unittest.main()
