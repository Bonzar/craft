"""Exact historical knowledge inventories; synthetic stores and semantic oracles only."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory import query_snapshot
from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.overview_review import RetrievalLedger, _coverage_needs
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore

EARLY='2026-03-11T23:59:59Z'
LATE='2026-11-12T12:00:00+00:00'
LIMIT='At that knowledge cutoff, this base cannot establish what Mira reported or when.'
LIMIT_VARIANT='This base has no knowledge at that cutoff establishing Mira’s report or its date.'


class EmptyRecordedHistoryTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.requests=[]; self.mode='accept'; self.text=LIMIT
        self.engine=MemoryEngine(self.store,model=self.provider,verify_overviews=True)
        self.service=MemoryService(self.store,self.engine)
        self.spec=ViewSpec('history','History','What did Mira report about the Larch receiver, and when?',scopes=('s',))
        self.engine.define_view(self.spec)
        self.queue=CaptureQueue(self.store,clock=lambda:datetime.fromisoformat(LATE))

    def canonical(self,key='claim',**kwargs):
        return self.store.append_assertion(assertion_id=key,text='Mira reported a fictional receiver.',actor='Mira',
            source='fixture://'+key,episode=key,recorded_at=kwargs.pop('recorded_at',LATE),scope=kwargs.pop('scope','s'),**kwargs)

    def raw(self,key='raw',**kwargs):
        return self.queue.capture_event('fiction',key,'Fictional source body.',source='fixture://'+key,scope=kwargs.pop('scope','s'),**kwargs)

    def certificate(self,snapshot=None,spec=None):
        return query_snapshot.recorded_history_certificate(self.engine,spec or self.spec,
            snapshot or self.engine.capture_snapshot(recorded_as_of=EARLY))

    def provider(self,request):
        self.requests.append(request)
        if request.task=='Plan memory retrieval for a question':
            return json.dumps({'reuse_view_id':None,'related_view_ids':[],'subquestions':[],
                               'reason':'Direct synthetic exact-question route.'})
        if request.task.startswith('Review'):
            if self.mode=='unavailable': raise ModelError('synthetic reviewer unavailable')
            accepted=self.mode=='accept' and self.text in (LIMIT,LIMIT_VARIANT)
            return json.dumps({'status':'accepted' if accepted else 'unresolved','coverage':'all_claims' if accepted else 'partial',
                'reason':'Limited knowledge statement only.' if accepted else 'World absence is not established.',
                'issues':[] if accepted else ['Requested facts remain unestablished.'],
                'context_requests':[],'deferred_context_requests':[],
                'unknown_needs':[{'quote':self.text,'gap_id':'report','basis':'empty_eligible_corpus','reason':'Certified base history is empty.'}] if accepted else []})
        return self.text

    def build(self):
        receipts={}
        view=self.engine.build_view(self.spec.id,recorded_as_of=EARLY,_receipts=receipts)
        return view,receipts[self.spec.id]

    def failure(self):
        snapshot=self.engine.capture_snapshot(recorded_as_of=EARLY)
        try: self.build()
        except ModelError as error:
            return self.service._failed_query(error,{'plan':{'request':{'question':self.spec.query}}},snapshot,'s',EARLY)
        self.fail('Expected semantic refusal')

    def test_01_observed_four_raw_five_canonical_inventory_projects_to_both_roles(self):
        for i in range(4): self.raw(str(i))
        for i in range(5): self.canonical(str(i))
        proof=self.certificate(); self.assertEqual(proof['raw_known_count'],0)
        self.assertEqual(proof['canonical_known_count_before_validity_filter'],0)
        view,check=self.build()
        for request in self.requests:
            metadata=next(r for r in request.records if r.get('role')=='answer_metadata')
            self.assertEqual(metadata['knowledge_at_cutoff'],proof)
            self.assertEqual(next(r for r in request.records if r.get('role')=='retrieval_state')['coverage'],'bounded_not_exhaustive')
        self.assertEqual(check['overview_review']['query_context']['knowledge_at_cutoff'],proof)
        self.assertEqual(check['overview_review']['unknown_needs'][0]['proof_sha256'],proof['proof_sha256'])

    def test_02_imported_canonical_before_cutoff_blocks_even_when_validity_hides_it(self):
        self.canonical(recorded_at='2026-01-01',valid_at='2027-01-01')
        snapshot=self.engine.capture_snapshot(valid_as_of='2026-03-11',recorded_as_of=EARLY)
        self.assertFalse(snapshot.assertions); self.assertIsNone(self.certificate(snapshot))

    def test_03_context_only_zero_canonical_raw_blocks(self):
        queue=CaptureQueue(self.store,clock=lambda:datetime(2026,1,1,tzinfo=timezone.utc))
        queue.capture_event('fiction','assistant','Context only.',source='fixture://context',scope='s',actor='assistant')
        self.assertIsNone(self.certificate())

    def test_04_unknown_valid_or_withdrawn_stored_canonical_still_counts(self):
        self.canonical(recorded_at='2026-01-01',metadata={'interpretation':{'status':'withdrawn'}})
        snapshot=self.engine.capture_snapshot(valid_as_of='2026-03-11',recorded_as_of=EARLY)
        self.assertIsNone(self.certificate(snapshot))

    def test_05_zero_visible_or_forged_model_certificate_does_not_justify_empty_basis(self):
        ledger=RetrievalLedger(self.engine.candidates,{},set())
        frame={'question':self.spec.query,'scopes':['s'],'knowledge_at_cutoff':{'complete':True,'raw_known_count':0}}
        value={'unknown_needs':[{'quote':'Unknown','gap_id':'report','basis':'empty_eligible_corpus','reason':'No hits.'}]}
        with self.assertRaisesRegex(ModelError,'certif|proof|empty'):
            _coverage_needs(value,'Unknown',frame,ledger)

    def test_06_unrelated_scope_is_private_but_explicit_root_and_dependency_prevent_false_empty(self):
        self.canonical('private',recorded_at='2026-01-01',scope='private')
        proof=self.certificate(); self.assertTrue(proof['complete']); self.assertNotIn('private',json.dumps(proof))
        explicit=replace(self.spec,id='explicit',root_ids=('private',)); self.engine.define_view(explicit)
        self.assertIsNone(self.certificate(spec=explicit))
        self.engine.define_view(ViewSpec('dep','Dependency','Other source',root_ids=('private',),scopes=('s',)))
        dependency=replace(self.spec,id='dependency',depends_on=('dep',)); self.engine.define_view(dependency)
        self.assertIsNone(self.certificate(spec=dependency))

    def test_07_legacy_unknown_raw_watermark_and_malformed_proof_fail_closed(self):
        snapshot=self.engine.capture_snapshot(recorded_as_of=EARLY)
        self.assertIsNone(self.certificate(replace(snapshot,raw_event_watermark=None)))
        proof=self.certificate(snapshot); ledger=RetrievalLedger(self.engine.candidates,{},set())
        ledger.knowledge_at_cutoff=proof
        value={'unknown_needs':[{'quote':'Unknown','gap_id':'report','basis':'empty_eligible_corpus','reason':'No history.'}]}
        frame={'question':self.spec.query,'scopes':['wrong'],'knowledge_at_cutoff':proof}
        with self.assertRaises(ModelError): _coverage_needs(value,'Unknown',frame,ledger)

    def test_08_source_origin_does_not_backdate_base_availability(self):
        self.raw(metadata={'source_message_at':'2026-03-10T12:00:00Z','source_message_clock_basis':{
            'kind':'producer','field':'sent_at','value':'2026-03-10T12:00:00Z','source':'fixture://raw'}})
        self.assertEqual(self.certificate()['raw_known_count'],0)

    def test_09_backdated_arrival_invalidates_current_inventory_but_later_arrival_does_not(self):
        snapshot=self.engine.capture_snapshot(recorded_as_of=EARLY)
        old=self.certificate(snapshot)
        self.raw()
        self.assertEqual(self.certificate(snapshot),old)
        self.assertFalse(query_snapshot.freshness(self.engine,self.spec,snapshot)['refresh_required'])
        self.canonical(recorded_at='2026-01-01')
        self.assertTrue(query_snapshot.freshness(self.engine,self.spec,snapshot)['refresh_required'])
        self.assertEqual(self.certificate(snapshot),old)
        self.assertIsNone(self.certificate())

    def test_10_independent_reviewer_rejects_world_negative_despite_certificate(self):
        self.text='Mira never reported anything; the event never happened.'
        result=self.failure()
        self.assertFalse(result['answer_accepted']); self.assertEqual(result['view']['summary'],'')
        self.assertEqual(result['answer_metadata']['notice']['purpose'],'knowledge_limit_notice')

    def test_11_independently_reviewed_limitation_retains_exact_question_and_domain_limit(self):
        view,check=self.build()
        result=self.service._query_result(view,{'request':{'question':self.spec.query}},self.service.status(scope='s'),check,'s',EARLY)
        self.assertTrue(result['answer_accepted']); self.assertTrue(result['answer_payload_complete'])
        self.assertEqual(result['completeness'],'incomplete')
        self.assertEqual(result['answer_metadata']['knowledge_at_cutoff']['domain_facts'],'not_established')
        self.assertEqual(check['overview_review']['query_context']['question'],self.spec.query)
        bad=replace(view,summary='A different answer.')
        self.assertFalse(self.service._answer_review_bound(check,bad,requested_question=self.spec.query))

    def test_12_refusal_or_unavailable_reviewer_yields_readable_notice_without_fake_acceptance(self):
        self.mode='unavailable'; result=self.failure()
        self.assertFalse(result['answer_accepted']); self.assertFalse(result['answer_payload_complete'])
        self.assertEqual(result['view']['summary'],''); self.assertEqual(result['answer_continuations'],[])
        notice=result['answer_metadata']['notice']
        self.assertEqual(notice['purpose'],'knowledge_limit_notice'); self.assertTrue(notice['notice_payload_complete'])
        self.assertIn('base',notice['text']); self.assertIn('cutoff',notice['text'])
        self.assertEqual(result['view']['overview_review']['status'],'unresolved')

    def test_13_inventory_uses_no_raw_body_reads_and_restore_pins_capability(self):
        statements=[]; self.store._connection.set_trace_callback(statements.append)
        snapshot=self.engine.capture_snapshot(recorded_as_of=EARLY)
        statements.clear(); proof=self.certificate(snapshot)
        self.assertTrue(proof); self.assertFalse(any('prompt' in sql.lower() for sql in statements))
        restored=query_snapshot._restore_frozen(self.engine,snapshot.descriptor())
        self.assertEqual(self.certificate(restored),proof)
        legacy=snapshot.descriptor(); legacy.pop('raw_store_absent')
        self.assertIsNone(self.certificate(query_snapshot._restore_frozen(self.engine,legacy)))

    def test_14_empty_proof_does_not_clear_other_obligations(self):
        proof=self.certificate(); ledger=RetrievalLedger(self.engine.candidates,{},set()); ledger.knowledge_at_cutoff=proof
        ledger.register([{'gap_id':'other','query':'Other prerequisite','limit':1}],'global')
        value={'unknown_needs':[{'quote':'Unknown','gap_id':'report','basis':'empty_eligible_corpus','reason':'No history.'}]}
        frame={'question':self.spec.query,'scopes':['s'],'knowledge_at_cutoff':proof,
            'cutoffs':{'valid_as_of':None,'recorded_as_of':EARLY}}
        _coverage_needs(value,'Unknown',frame,ledger)
        self.assertTrue(ledger.pending())

    def test_15_unpersisted_spec_has_no_authoritative_inventory(self):
        standalone=replace(self.spec,id='not-persisted')
        self.assertIsNone(self.certificate(spec=standalone))

    def test_16_positive_absent_raw_store_differs_from_legacy_unknown(self):
        store=MemoryStore(':memory:'); self.addCleanup(store.close)
        engine=MemoryEngine(store)
        spec=ViewSpec('no-raw','No raw','What is known?',scopes=('s',)); engine.define_view(spec)
        snapshot=engine.capture_snapshot(recorded_as_of=EARLY)
        proof=query_snapshot.recorded_history_certificate(engine,spec,snapshot)
        self.assertTrue(proof['raw_store_absent']); self.assertIsNone(proof['raw_event_watermark'])
        self.assertIsNone(query_snapshot.recorded_history_certificate(engine,spec,replace(snapshot,raw_store_absent=None)))

    def test_17_cross_scope_future_root_cannot_hide_early_raw_reference(self):
        event=self.raw(scope='private',recorded_at='2026-01-01',actor='assistant')
        self.canonical('future-root',scope='private',metadata={'evidence_refs':[{'event_id':event.id,'start':0,'end':1}]})
        spec=replace(self.spec,id='future-root-view',root_ids=('future-root',)); self.engine.define_view(spec)
        self.assertIsNone(self.certificate(spec=spec))

    def test_18_cross_scope_mandatory_graph_is_counted_before_validity(self):
        self.canonical('future-root',scope='private')
        self.canonical('condition',recorded_at='2026-01-01',scope='private',valid_at='2027-01-01')
        self.store.append_edge(edge_id='condition-edge',from_id='future-root',to_id='condition',kind='depends_on',recorded_at='2026-01-01')
        spec=replace(self.spec,id='closure-view',root_ids=('future-root',)); self.engine.define_view(spec)
        self.assertIsNone(self.certificate(spec=spec))

    def test_19_tampered_certificate_cannot_pass_server_guard_or_service(self):
        from prototypes.assertion_memory.recorded_history import valid
        proof=self.certificate(); self.assertTrue(valid(proof))
        for change in ({'raw_known_count':1},{'complete':False},{'effective_recorded_as_of':LATE},{'proof_sha256':'0'*64}):
            self.assertFalse(valid({**proof,**change}))
        view,check=self.build(); forged={**check['overview_review'],'query_context':{
            **check['overview_review']['query_context'],'knowledge_at_cutoff':{**proof,'raw_known_count':1}}}
        self.assertFalse(self.service._answer_review_bound({**check,'overview_review':forged},view,requested_question=self.spec.query))

    def test_20_readable_notice_survives_failed_failure_proof_persistence(self):
        self.mode='unavailable'
        with patch('prototypes.assertion_memory.build_proof._write_failure',side_effect=OSError('synthetic persistence failure')):
            result=self.failure()
        self.assertFalse(result['answer_accepted']); self.assertFalse(result['answer_payload_complete'])
        self.assertTrue(result['answer_metadata']['notice']['notice_payload_complete'])
        self.assertIsNone(result['view']['id'])

    def test_21_no_notice_for_zero_selected_with_known_raw_history(self):
        self.raw(recorded_at='2026-01-01'); self.mode='unavailable'
        result=self.failure(); self.assertNotIn('answer_metadata',result)
        self.assertFalse(result['answer_accepted'])

    def test_22_bounded_envelope_pages_optional_audit_before_empty_answer_evidence(self):
        from dataclasses import asdict
        from prototypes.assertion_memory.service import _fits
        self.mode='unavailable'; result=self.failure()
        version=self.engine.get_version(result['view']['id'])
        # Exercise the actual byte boundary with a complete machine notice and
        # an otherwise pageable immutable audit, without raising transport caps.
        for size in range(0,16000,100):
            candidate={**result,'diagnostic_padding':'x'*size}
            if not _fits(candidate): break
        response=self.service._query_response(candidate,version,'s')
        self.assertEqual(response['view']['evidence'],[])
        self.assertEqual(response['view']['summary'],'')
        self.assertFalse(response['answer_payload_complete']); self.assertFalse(response['answer_accepted'])
        self.assertTrue(response['answer_metadata']['notice']['notice_payload_complete'])
        self.assertFalse(response['view']['overview_review_inline'])
        self.assertEqual(response['view']['overview_review_continuation']['tool'],'memory_history')
        self.assertTrue(_fits(response))

    def test_23_consumer_contract_explains_notice_is_not_domain_answer_or_missing_page(self):
        from pathlib import Path
        source=Path(__file__).parents[1].joinpath('mcp_server.py').read_text()
        self.assertIn('knowledge_limit_notice',source)
        self.assertIn('notice_payload_complete',source)

    def test_24_rehashed_malformed_machine_fields_are_not_valid_certificates(self):
        from prototypes.assertion_memory.recorded_history import digest,valid
        proof=self.certificate()
        for changes in ({'scopes':'s'},{'scopes':[7]},{'source_revision_watermark':-1},
                        {'relation_processing_frontier':None},{'effective_recorded_as_of':'2026-03-11'},
                        {'authorization_sha256':'g'*64}):
            bad={**proof,**changes}; bad['proof_sha256']=digest({k:v for k,v in bad.items() if k!='proof_sha256'})
            with self.subTest(changes=changes): self.assertFalse(valid(bad))


    def _repeat_query(self,*,historical=False,cache_path='normal',refuse=False):
        import copy
        from contextlib import ExitStack
        cutoffs={'recorded_as_of':EARLY} if historical else {}
        first=self.service.query(self.spec.query,scope='s',**cutoffs)
        self.assertTrue(first['answer_accepted'])
        view_id=first['view']['view_id']
        prior=copy.deepcopy(self.engine.view_checks(view_id)[-1])
        old_view=self.engine.get_version(first['view']['id'])
        if historical: self.raw()  # Later base intake changes the frozen raw watermark only.
        self.requests.clear()
        if refuse: self.mode='unavailable'
        with ExitStack() as stack:
            if cache_path!='normal':
                stack.enter_context(patch('prototypes.assertion_memory.build_pipeline._cached',return_value=None))
            if cache_path=='previous':
                stack.enter_context(patch('prototypes.assertion_memory.semantic_reuse.revalidate',
                    side_effect=lambda engine,spec,snapshot,evidence,links,*args:(None,None,evidence,links)))
            second=self.service.query(self.spec.query,scope='s',**cutoffs)
        self.assertEqual(self.engine.view_checks(view_id)[0],prior)
        self.assertEqual(self.engine.get_version(first['view']['id']),old_view)
        if refuse:
            self.assertFalse(second['answer_accepted']);self.assertFalse(second['answer_payload_complete'])
            self.assertTrue(second['answer_metadata']['notice']['notice_payload_complete'])
            return
        self.assertTrue(second['answer_accepted'])
        self.assertTrue(second['answer_payload_complete'])
        proof=second['answer_metadata']['knowledge_at_cutoff']
        check=self.engine.view_checks(view_id)[-1]
        self.assertEqual(proof,check['overview_review']['query_context']['knowledge_at_cutoff'])
        self.assertEqual(proof['effective_recorded_as_of'],check['query_snapshot']['effective_recorded_as_of'])
        self.assertEqual(proof['raw_event_watermark'],check['query_snapshot']['raw_event_watermark'])
        self.assertEqual(second['view']['summary'],first['view']['summary'])
        self.assertEqual(check['overview_review']['query_context']['question'],self.spec.query)
        self.assertTrue(any(request.task.startswith('Review') for request in self.requests))

    def test_25_repeat_current_service_query_keeps_acceptance_and_current_proof(self):
        self._repeat_query()

    def test_26_repeat_historical_query_after_later_intake_keeps_acceptance_and_proof(self):
        self._repeat_query(historical=True)

    def test_27_semantic_reuse_cannot_attach_old_empty_proof_to_current_snapshot(self):
        self._repeat_query(cache_path='semantic')

    def test_28_previous_fingerprint_cannot_attach_old_empty_proof_to_current_snapshot(self):
        self._repeat_query(cache_path='previous')

    def test_29_failed_current_review_cannot_borrow_prior_empty_answer_acceptance(self):
        self._repeat_query(refuse=True)


    def test_30_freshly_reviewed_changed_wording_is_not_replaced_by_stale_same_input_winner(self):
        import copy
        first=self.service.query(self.spec.query,scope='s')
        self.assertTrue(first['answer_accepted'])
        view_id=first['view']['view_id']; old_check=copy.deepcopy(self.engine.view_checks(view_id)[0])
        self.text=LIMIT_VARIANT; self.requests.clear()
        second=self.service.query(self.spec.query,scope='s')
        self.assertTrue(second['answer_accepted'])
        self.assertEqual(second['view']['summary'],LIMIT_VARIANT)
        self.assertEqual(self.engine.view_checks(view_id)[0],old_check)
        self.assertEqual(self.engine.get_version(first['view']['id']).summary,LIMIT)
        self.assertTrue(any(request.task.startswith('Review') for request in self.requests))
