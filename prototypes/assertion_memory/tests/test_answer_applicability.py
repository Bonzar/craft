"""Immutable answers need independent exact-request certificates, never planner waivers."""
import json
import unittest
from dataclasses import replace
from prototypes.assertion_memory.model import ModelError

from prototypes.assertion_memory.tests.test_answer_contract_v21 import AnswerContractTest


class ApplicabilityTest(AnswerContractTest):
    # Keep the shared deterministic fixture, not its inherited test cases.
    def alias(self, question='Which serial was reported for the printer?', **cutoffs):
        original=self.provider
        def model(request):
            if request.task=='Plan memory retrieval for a question':
                self.requests.append(request)
                return json.dumps({'reuse_view_id':self.spec.id,'related_view_ids':[],
                    'subquestions':[],'reason':'Candidate nomination only.'})
            if getattr(self,'reject_alias',False) and request.task.startswith('Review') and any(
                    r.get('role')=='query_context' and r.get('question')!=self.spec.query for r in request.records) and any(
                    r.get('role')=='fixed_answer_applicability' for r in request.records):
                self.requests.append(request)
                return json.dumps({'status':'revise','coverage':'partial','reason':'Wider question needs new wording.',
                    'issues':['Missing facet.'],'context_requests':[],'unknown_needs':[]})
            return original(request)
        self.engine.model=model
        return self.service.query(question,scope='s',**cutoffs)

    def test_alias_receipt_same_body_original_pointer_warm_zero_calls(self):
        view,base=self.build(); self.requests=[]
        result=self.alias()
        self.assertTrue(result['answer_accepted'])
        self.assertEqual(result['view']['id'],view.id)
        self.assertEqual(self.engine.latest_check(view)['id'],base['id'])
        self.assertEqual(self.service.history(self.spec.id)['latest_check']['id'],base['id'])
        self.assertEqual(self.engine.inspect_view(self.spec.id)['current_check_id'],base['id'])
        reviews=[r for r in self.requests if r.task=='Review grounded memory overview']
        self.assertEqual(len(reviews),1)
        self.assertFalse(any(r.task.startswith(('Write','Draft','Synthesize')) for r in self.requests))
        self.requests=[]; warm=self.alias()
        self.assertTrue(warm['answer_accepted']); self.assertEqual(self.requests,[])
        self.assertEqual(len(self.engine.view_history(self.spec.id)),1)

    def test_rejection_materializes_original_full_question_once(self):
        view,base=self.build(); self.reject_alias=True; self.requests=[]
        question='Which serial and all permission conditions were reported for the printer?'
        result=self.alias(question)
        self.assertTrue(result['answer_accepted'])
        self.assertNotEqual(result['view']['view_id'],self.spec.id)
        self.assertEqual(self.engine._spec(result['view']['view_id']).query,question)
        self.assertEqual(len(self.engine.view_history(self.spec.id)),1)
        self.assertEqual(self.engine.latest_check(view)['id'],base['id'])

    def test_missing_historical_candidate_does_not_build_old_question(self):
        self.build(); self.requests=[]
        result=self.alias(recorded_as_of='2026-02-01')
        self.assertNotEqual(result['view']['view_id'],self.spec.id)
        self.assertEqual(len(self.engine.view_history(self.spec.id)),1)
        self.assertEqual(self.engine._spec(result['view']['view_id']).query,'Which serial was reported for the printer?')

    def test_relation_frontier_reassessed_for_exact_alias_and_pending_guard_survives(self):
        self.relation(); self.build(); self.requests=[]
        result=self.alias(); self.assertTrue(result['answer_accepted'])
        self.assertFalse(result['refresh_required'])
        self.assertFalse(result['source_status']['processing_gaps_apply_to_query'])
        self.service.queue.capture_event('pending','p','Unread qualifier.',source='fixture://pending',scope='s')
        self.requests=[]; result=self.alias()
        self.assertTrue(result['refresh_required'])
        self.assertTrue(result['source_status']['processing_gaps_apply_to_query'])
        self.assertTrue(any(r.task=='Review grounded memory overview' for r in self.requests))

    def test_forged_alias_frame_cannot_bind(self):
        view,_=self.build(); result=self.alias()
        check=self.engine.view_checks(self.spec.id)[-1]
        self.assertEqual(check['overview_review'].get('certificate',{}).get('kind'),'answer_applicability')
        altered={**check,'overview_review':{**check['overview_review'],'query_context':{'question':'forged'}}}
        self.assertFalse(self.service._answer_review_bound(altered,view,requested_question='forged'))


    def test_three_observed_alias_shapes_share_existing_text_including_existing_history(self):
        original='What does Mira now report was approved for the Harbor photographs, with every condition?'
        self.spec=replace(self.spec,id="harbor",query=original)
        self.engine.define_view(self.spec)
        self.summary='Mira reports conditional approval only; no online publication approval was verified. [source:serial]'
        current,currentcheck=self.build()
        from prototypes.assertion_memory.storage import _timestamp
        historical_cutoff=_timestamp('2026-02-01')
        receipts={}; historical=self.engine.build_view(self.spec.id,recorded_as_of=historical_cutoff,_receipts=receipts)
        history_before=self.engine.inspect_historical_view(self.spec.id,recorded_as_of=historical_cutoff)
        for question,cutoffs in [
            ('What does Mira report was approved for the Harbor photographs, with every condition?',{}),
            ("According to Mira corrected report, which photographs may be displayed or published under what limits?",{}),
            ('What does Mira report was approved for the Harbor photographs, with every condition?',{'recorded_as_of':'2026-02-01'})]:
            with self.subTest(question=question,cutoffs=cutoffs):
                self.requests=[]; result=self.alias(question,**cutoffs)
                self.assertTrue(result['answer_accepted'])
                self.assertEqual(result['view']['id'],historical.id if cutoffs else current.id)
                self.assertEqual(result['original_question'],original)
                self.assertEqual(len([r for r in self.requests if r.task.startswith('Review')]),1)
                self.assertFalse(any(r.task.startswith(('Draft','Synthesize')) for r in self.requests))
        self.assertEqual(self.engine.inspect_historical_view(self.spec.id,recorded_as_of=historical_cutoff),history_before)
        self.assertEqual(self.engine.inspect_view(self.spec.id)['current_check_id'],currentcheck['id'])

    def test_alias_newest_check_never_poisoned_original_cache_prior_or_semantic_child(self):
        from prototypes.assertion_memory.semantic_reuse import _prior
        from prototypes.assertion_memory.build_pipeline import _cached
        from prototypes.assertion_memory.semantic_views import contributions
        from prototypes.assertion_memory.query_snapshot import receipt
        from prototypes.assertion_memory.build_proof import accepted_review
        view,base=self.build(); result=self.alias(); alias=self.engine.view_checks(self.spec.id)[-1]
        snapshot=self.engine.capture_snapshot()
        self.assertEqual(_prior(self.engine,self.spec,None,None)[1]['id'],base['id'])
        self.assertEqual(_cached(self.engine,self.spec,None,None,snapshot.corpus(self.engine,self.spec),{})[2]['query_context']['question'],self.spec.query)
        self.assertFalse(accepted_review(self.engine,alias['overview_review']))
        with self.assertRaisesRegex(ModelError,'applicability'):
            contributions(self.engine,{self.spec.id:view},{self.spec.id:alias},snapshot)
        self.assertEqual(receipt(self.engine,alias['id']),alias)
        self.requests=[]; original=self.service.query(self.spec.query,scope='s')
        self.assertTrue(original['answer_accepted'])
        self.assertFalse(any(r.task.startswith(('Review','Draft','Synthesize')) for r in self.requests))

    def test_relation_revision_changes_cache_and_does_not_inherit_old_disposition(self):
        job=self.relation(); self.build(); first=self.alias(); self.requests=[]
        with self.store.transaction(write=True) as db:
            proposals=json.loads(db.execute('SELECT proposals FROM memory_capture_jobs WHERE id=?',(job,)).fetchone()[0])
            proposals[0]['reason']='Changed alias-material qualifier.'
            db.execute('UPDATE memory_capture_jobs SET proposals=? WHERE id=?',(json.dumps(proposals),job))
        self.disposition='material'; self.summary='Serial report remains conditional. [source:serial]'
        # Fixed text lacks the new oracle-required wording, so it must fork.
        self.reject_alias=True
        second=self.alias()
        self.assertNotEqual(second['view']['view_id'],self.spec.id)
        self.assertTrue(any(r.task=='Review grounded memory overview' for r in self.requests))
        self.assertEqual(len(self.engine.view_history(self.spec.id)),1)

    def test_new_canonical_source_outside_old_top_k_requires_new_review(self):
        self.build(); self.alias(); self.requests=[]
        self.store.append_assertion(assertion_id='new-qualifier',text='An additional required limitation outside prior selection.',
            actor='reporter',source='fixture://new',episode='new',scope='s',recorded_at='2026-01-01')
        result=self.alias()
        self.assertTrue(result['answer_accepted'])
        self.assertTrue(any(r.task=='Review grounded memory overview' for r in self.requests))
        self.assertTrue(any(row.get('id')=='new-qualifier' for request in self.requests
            if request.task=='Review grounded memory overview' for row in request.records))
        self.assertEqual(len(self.engine.view_history(self.spec.id)),1)

    def test_missing_required_closure_and_cross_scope_candidate_reject_without_alias_review(self):
        from prototypes.assertion_memory.answer_applicability import candidate
        self.build()
        request={'question':'alias','scopes':['other'],'valid_as_of':None,'recorded_as_of':None}
        self.assertIsNone(candidate(self.engine,{'view_id':self.spec.id},request,self.engine.capture_snapshot()))
        self.store.append_edge(edge_id='new-required',from_id='serial',to_id='garden',kind='depends_on',
            recorded_at='2026-01-02')
        request['scopes']=['s']
        self.assertIsNone(candidate(self.engine,{'view_id':self.spec.id},request,self.engine.capture_snapshot()))

    def test_navigation_candidate_is_not_an_alias_domain_answer(self):
        from prototypes.assertion_memory.answer_applicability import candidate
        view,base=self.build()
        with self.store.transaction(write=True) as db:
            audit={**base['overview_review'],'assembly_coverage':'navigation_index'}
            db.execute('UPDATE memory_view_checks SET overview_review=? WHERE id=?',(json.dumps(audit),base['id']))
        request={'question':'alias','scopes':['s'],'valid_as_of':None,'recorded_as_of':None}
        self.assertIsNone(candidate(self.engine,{'view_id':self.spec.id},request,self.engine.capture_snapshot()))

    def test_changed_frontier_during_alias_review_never_claims_current_clearance(self):
        self.build(); provider=self.provider
        def race(request):
            value=provider(request)
            if any(r.get('role')=='fixed_answer_applicability' for r in request.records):
                self.store.append_assertion(assertion_id='race',text='New material condition.',actor='r',
                    source='fixture://race',episode='race',scope='s',recorded_at='2026-01-01')
            return value
        self.provider=race
        result=self.alias()
        self.assertTrue(result['refresh_required'])
        self.assertTrue(result['freshness_check']['freshness']['newer_eligible_evidence_unchecked'])

    def test_bounded_read_obligation_consumed_by_fixed_reviewer_without_writer(self):
        self.build(); provider=self.provider; rounds=[]
        def acquire(request):
            if any(r.get('role')=='fixed_answer_applicability' for r in request.records):
                rounds.append(request)
                if len(rounds)==1:
                    self.requests.append(request)
                    return json.dumps({'status':'revise','coverage':'partial','reason':'Read named qualifier.',
                        'issues':[],'context_requests':[{'operation':'read','gap_id':'named','source_ids':['garden']}],'unknown_needs':[]})
            return provider(request)
        self.provider=acquire
        result=self.alias()
        self.assertTrue(result['answer_accepted']); self.assertEqual(len(rounds),2)
        self.assertFalse(any(r.task.startswith(('Draft','Synthesize')) for r in self.requests))
        check=self.engine.view_checks(self.spec.id)[-1]
        self.assertEqual(check['overview_review']['consumed_gaps'],['named'])

    def test_changed_text_or_cutoff_cannot_use_saved_certificate(self):
        view,_=self.build(); self.alias(); check=self.engine.view_checks(self.spec.id)[-1]
        from prototypes.assertion_memory.answer_applicability import bound
        self.assertFalse(bound(self.engine,check,replace(view,summary='Changed bytes.'),'Which serial was reported for the printer?'))
        self.assertFalse(bound(self.engine,check,replace(view,valid_as_of='2026-01-01'),'Which serial was reported for the printer?'))

    def test_mechanical_original_check_churn_does_not_destroy_alias_cache(self):
        self.build(); first=self.alias()
        self.service.query(self.spec.query,scope='s')
        self.requests=[]; repeated=self.alias()
        self.assertTrue(repeated['answer_accepted'])
        self.assertEqual(self.requests,[])
        self.assertEqual(first['answer_certificate']['check_id'],repeated['answer_certificate']['check_id'])

    def test_cached_certificate_snapshot_stays_pinned_beside_fresh_invocation_snapshot(self):
        self.build(); first=self.alias(); second=self.alias()
        self.assertEqual(first['freshness_check']['query_snapshot'],second['freshness_check']['query_snapshot'])
        self.assertNotEqual(first['query_snapshot']['captured_at'],second['query_snapshot']['captured_at'])

    def test_old_source_gap_waiver_cannot_replace_missing_exact_alias_disposition(self):
        event=self.service.queue.capture_event('source-gap','gap','Unresolved omitted source meaning.',
            source='fixture://gap',scope='s',recorded_at='2026-01-01')
        outcome={'assertion_ids':[],'reviews':[{'kind':'source_coverage','status':'unresolved','reason':'Unresolved referent.'}],
            'context_recovery':[{'operation':'source_coverage','protocol':'fixture-v1','status':'unresolved',
                'attempt':0,'omissions':[],'reason':'Unresolved referent.'}],
            'source_revision':{'schema':'source-interpretation/v1','event_id':event.id,'protocol':'fixture-v1',
                'input_fingerprint':'fixed','status':'unresolved','assertion_ids':[],'reconciliation':[]}}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,'gap','2026-01-02',json.dumps(outcome)))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',proposals=? WHERE event_id=?",
                (json.dumps(outcome['reviews']),event.id))
        provider=self.provider
        def source_oracle(request):
            response=provider(request)
            if not request.task.startswith('Review'): return response
            value=json.loads(response)
            inventory=next((r for r in request.records if r.get('role')=='source_gap_inventory'),None)
            fixed=any(r.get('role')=='fixed_answer_applicability' for r in request.records)
            if inventory and not fixed:
                value['source_gap_dispositions']=[{'event_id':r['event_id'],'run_id':r['run_id'],
                    'disposition':'nonmaterial','reason':'Independent normal answer review of source limitation.',
                    'source_ids':[]} for r in inventory['gaps']]
            return json.dumps(value)
        self.provider=source_oracle; self.engine.model=source_oracle
        original,base=self.build(); self.requests=[]; result=self.alias()
        self.assertTrue(result['answer_accepted'])
        self.assertNotEqual(result['view']['view_id'],self.spec.id)
        self.assertEqual(self.engine.latest_check(original)['id'],base['id'])
        self.assertEqual(len(self.engine.view_checks(self.spec.id)),1)
        self.assertEqual(self.engine._spec(result['view']['view_id']).query,'Which serial was reported for the printer?')
        fixed=[r for r in self.requests if any(row.get('role')=='fixed_answer_applicability' for row in r.records)]
        self.assertEqual(len(fixed),2)  # One semantic review and one existing structural repair.
        self.assertTrue(all(any(row.get('role')=='source_gap_inventory' for row in r.records) for r in fixed))

    def test_unrelated_zero_canonical_source_revision_does_not_bust_authorized_alias_cache(self):
        self.build(); first=self.alias()
        event=self.service.queue.capture_event('unrelated','event','Other scope source.',
            source='fixture://other',scope='other',recorded_at='2026-01-01')
        outcome={'reviews':[],'source_revision':{'schema':'source-interpretation/v1','event_id':event.id,
            'protocol':'fixture-v1','input_fingerprint':'other','status':'accepted_complete',
            'assertion_ids':[],'reconciliation':[]}}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,'other','2026-01-02',json.dumps(outcome)))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',proposals='[]' WHERE event_id=?",(event.id,))
        self.requests=[]; result=self.alias()
        self.assertTrue(result['answer_accepted']); self.assertFalse(result['refresh_required'])
        self.assertEqual(len(self.requests),0)
        self.assertIn('answer_certificate',result)
        self.assertEqual(result['answer_certificate']['check_id'],first['answer_certificate']['check_id'])

    def test_unrelated_relation_revision_does_not_retroactively_poison_certificate(self):
        self.store.append_assertion(assertion_id='other',text='Other scope condition.',actor='other',
            source='fixture://other',episode='other',scope='other',recorded_at='2026-01-01')
        proposal={'kind':'depends_on','from_id':'other','to_id':'other','status':'unresolved',
            'reason':'Unrelated scope ambiguity.','condition':'Other scope condition.'}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,proposals,updated_at,available_at) '
                'VALUES(?,?,?,?,?,?,?)',('other-job','other','completed','done',json.dumps([proposal]),'2026-01-02','2026-01-02'))
        self.build(); first=self.alias()
        proposal['reason']='Updated unrelated scope ambiguity.'
        with self.store.transaction(write=True) as db:
            db.execute('UPDATE memory_capture_jobs SET proposals=? WHERE id=?',(json.dumps([proposal]),'other-job'))
        self.requests=[]; result=self.alias()
        self.assertTrue(result['answer_accepted']); self.assertFalse(result['refresh_required'])
        self.assertIn('answer_certificate',result)
        self.assertEqual(result['answer_certificate']['check_id'],first['answer_certificate']['check_id'])
        self.assertEqual(len(self.requests),0)

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ApplicabilityTest(name) for name in ApplicabilityTest.__dict__ if name.startswith('test_'))

if __name__=='__main__': unittest.main()
