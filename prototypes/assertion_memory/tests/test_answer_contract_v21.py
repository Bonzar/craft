"""Synthetic deterministic oracles for query-local limitations and answer delivery."""
import hashlib
import json
import unittest
from dataclasses import replace

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen
from prototypes.assertion_memory.service import MemoryService, _fits
from prototypes.assertion_memory.storage import MemoryStore


class AnswerContractTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.requests=[]; self.disposition='nonmaterial'; self.omit=False; self.qualified=True
        self.proof_ids=['serial']; self.summary='Reported printer serial: PX-41. [source:serial]'
        self.reclassify=False
        self.engine=MemoryEngine(self.store,model=self.provider,verify_overviews=True)
        self.service=MemoryService(self.store,self.engine)
        for key,text in [('serial','Reported printer serial: PX-41.'),('garden','A reported garden condition.'),('permit','A reported conditional permit.')]:
            self.store.append_assertion(assertion_id=key,text=text,actor='reporter',source='fixture://'+key,
                episode=key,scope='s',recorded_at='2026-01-01')
        self.spec=ViewSpec('serial-view','Serial','What is the reported printer serial?',scopes=('s',),root_ids=('serial',))
        self.engine.define_view(self.spec)

    def provider(self,request):
        self.requests.append(request)
        if request.task.startswith('Review'):
            value={'status':'accepted','coverage':'all_claims','reason':'Synthetic independent semantic oracle.',
                'issues':[],'context_requests':[],'unknown_needs':[]}
            inventory=next((r for r in request.records if r.get('role')=='relation_gap_inventory'),None)
            if inventory and not self.omit:
                repaired=self.reclassify and any(r.get('role')=='review_contract_feedback' for r in request.records)
                value['relation_gap_dispositions']=[{'proposal_id':r['proposal_id'],'revision':r['revision'],
                    'disposition':'nonmaterial' if repaired else self.disposition,
                    'reason':'The garden condition does not affect the independently reported serial.',
                    'source_ids':self.proof_ids,'answer_qualified':self.qualified,
                    'qualification_quote':self.summary.split(' [source:')[0]} for r in inventory['gaps']]
                value['unassessed_relation_gap_qualification']={'answer_qualified':self.qualified,
                    'quote':self.summary.split(' [source:')[0],
                    'reason':'Unassessed conditions prevent any exhaustive or current permission conclusion.'}
            return json.dumps(value)
        return self.summary

    def relation(self,index=0,**changes):
        proposal={'kind':'depends_on','from_id':'permit','to_id':'garden','status':'unresolved',
            'condition':'Actual acceptance remains required.','reason':'Identity of reported objects remains uncertain.',
            'verification':{'status':'unresolved','reason':'No verified equivalence.'},**changes}
        job='relation-'+str(index)
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,proposals,updated_at,available_at) '
                'VALUES(?,?,?,?,?,?,?)',(job,'permit','completed','done',json.dumps([proposal]),'2026-01-02','2026-01-02'))
        return job

    def build(self):
        receipts={}; view=self.engine.build_view(self.spec.id,_receipts=receipts)
        return view,receipts[self.spec.id]

    def result(self,view=None,check=None):
        if view is None: view,check=self.build()
        return self.service._query_result(view,{},self.service.status(scope='s'),check,'s',None)

    def test_complete_terminal_relation_inventory_receives_independent_query_local_review(self):
        for i in range(3): self.relation(i)
        before=self.service.status(scope='s')['unresolved']
        view,check=self.build(); result=self.result(view,check)
        self.assertEqual(result['unresolved_count'],3)
        self.assertFalse(result['refresh_required'])
        self.assertFalse(result['source_status']['processing_gaps_apply_to_query'])
        audit=check['overview_review']['relation_gap_review']
        self.assertTrue(audit['inventory']['complete'])
        self.assertEqual(len(audit['dispositions']),3)
        self.assertEqual(audit['final_text_sha256'],hashlib.sha256(view.summary.encode()).hexdigest())
        self.assertEqual(self.service.status(scope='s')['unresolved'],before)
        self.assertEqual(self.store.list_edges(),[])
        writers=[r for r in self.requests if not r.task.startswith('Review')]
        self.assertTrue(all(not any(row.get('role')=='relation_gap_inventory' for row in r.records) for r in writers))

    def test_missing_review_or_invalid_canonical_ids_never_waive_relation(self):
        self.relation(); self.omit=True
        with self.assertRaises(ModelError): self.build()
        self.omit=False; self.proof_ids=['fabricated']
        with self.assertRaises(ModelError): self.build()
        self.proof_ids=[]
        with self.assertRaises(ModelError): self.build()

    def test_material_relation_requires_real_action_qualification(self):
        self.relation(); self.disposition='material'; self.qualified=False
        self.summary='The condition is unknown, but use is permitted. [source:serial]'
        with self.assertRaises(ModelError): self.build()
        self.qualified=True; self.summary='Permission cannot be established while the condition remains unresolved. [source:serial]'
        view,check=self.build(); result=self.result(view,check)
        self.assertTrue(result['source_status']['processing_gaps_apply_to_query'])
        self.assertEqual(result['completeness'],'incomplete')
        self.assertTrue(result['answer_payload_complete'])
        self.assertTrue(result['answer_accepted'])

    def test_contract_repair_cannot_weaken_material_relation(self):
        self.relation(); self.disposition='material'; self.qualified=False; self.reclassify=True
        with self.assertRaises(ModelError): self.build()

    def test_changed_proposal_reason_status_or_frontier_invalidates_old_local_receipt(self):
        job=self.relation(); view,check=self.build()
        self.assertFalse(self.result(view,check)['refresh_required'])
        with self.store.transaction(write=True) as db:
            proposal=json.loads(db.execute('SELECT proposals FROM memory_capture_jobs WHERE id=?',(job,)).fetchone()[0])
            proposal[0]['verification']['reason']='A newly discovered ambiguity.'
            db.execute('UPDATE memory_capture_jobs SET proposals=? WHERE id=?',(json.dumps(proposal),job))
        changed=self.result(view,check)
        self.assertTrue(changed['refresh_required'])
        self.assertIsNone(changed['source_status']['processing_gaps_apply_to_query'])
        fresh=capture(self.engine)
        self.assertNotEqual(fresh.relation_gaps(('s',))['frontier_sha256'],check['overview_review']['relation_gap_review']['inventory']['frontier_sha256'])

    def test_new_raw_or_operational_error_still_blocks_local_relation_clearance(self):
        self.relation(); view,check=self.build()
        self.service.queue.capture_event('s','pending','A potentially material unread condition.',source='fixture://pending',scope='s')
        result=self.result(view,check)
        self.assertTrue(result['refresh_required'])
        self.assertTrue(result['source_status']['processing_gaps_apply_to_query'])

    def test_omitted_relation_inventory_cannot_clear_potential_materiality(self):
        for i in range(18): self.relation(i)
        self.summary='Reported serial PX-41; unassessed limitations prevent exhaustive conclusions or permission. [source:serial]'
        view,check=self.build(); result=self.result(view,check)
        audit=check['overview_review']['relation_gap_review']
        self.assertFalse(audit['inventory']['complete'])
        self.assertEqual(audit['inventory']['total'],18)
        self.assertGreater(audit['inventory']['unassessed_count'],0)
        self.assertIsNone(result['source_status']['processing_gaps_apply_to_query'])
        self.assertEqual(result['completeness'],'incomplete')
        self.assertTrue(result['answer_payload_complete'])
        self.assertTrue(result['answer_accepted'])

    def test_exact_answer_question_and_frontier_bind_local_receipt(self):
        self.relation(); view,check=self.build()
        changed=replace(view,summary='A different permission conclusion.')
        self.assertTrue(self.result(changed,check)['refresh_required'])
        changed_check={**check,'overview_review':{**check['overview_review'],'query_context':{**check['overview_review']['query_context'],'question':'Different question'}}}
        self.assertTrue(self.result(view,changed_check)['refresh_required'])

    def test_complete_inline_answer_with_large_optional_proof_needs_no_answer_pages(self):
        view,check=self.build()
        large=replace(view,evidence=[{'id':'serial','text':'proof '*20000}])
        result=self.result(large,check)
        self.assertFalse(result['response_complete'])
        self.assertTrue(result['answer_payload_complete'])
        self.assertTrue(result['answer_accepted'])
        self.assertFalse(result['proof_complete'])
        self.assertEqual(result['answer_continuations'],[])
        self.assertEqual(result['view']['summary'],view.summary)
        self.assertTrue(_fits(result))
        self.assertTrue(any(c['arguments'].get('section')=='evidence' for c in result['continuations']))

    def test_large_summary_requires_every_pinned_answer_page_including_final_condition(self):
        view,check=self.build()
        summary=('Attributed serial report. '*600)+' Required last condition: no permission is established.'
        view=replace(view,summary=summary)
        check={**check,'overview_review':{**check['overview_review'],'final_text_sha256':hashlib.sha256(summary.encode()).hexdigest()}}
        result=self.result(view,check)
        self.assertFalse(result['answer_payload_complete'])
        self.assertEqual([c['arguments']['section'] for c in result['answer_continuations']],['summary'])
        self.assertEqual(result['answer_continuations'][0]['arguments']['version_id'],view.id)
        self.assertNotIn('summary',result['view'])
        # History is authoritative and pinned, so use a persisted genuinely large summary too.
        with self.store.transaction(write=True) as db:
            row=db.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(view.id,)).fetchone()
            saved=json.loads(row[0]); saved['summary']=summary
            db.execute('UPDATE memory_view_versions SET snapshot=? WHERE id=?',(json.dumps(saved),view.id))
        cursor=result['answer_continuations'][0]; pages=[]
        while cursor:
            page=self.service.history(**cursor['arguments']); pages.append(page['content']); cursor=page.get('continuation')
        self.assertGreater(len(pages),1)
        self.assertEqual(''.join(pages),summary)
        self.assertIn('Required last condition',pages[-1])

    def test_small_answer_has_priority_over_large_pinned_freshness_audit(self):
        view,check=self.build()
        check={**check,'freshness':{**check['freshness'],'retrieval_dependencies':[{'detail':'audit '*20000}]}}
        result=self.result(view,check)
        self.assertEqual(result['view'].get('summary'),view.summary)
        self.assertTrue(result['answer_payload_complete'])
        self.assertTrue(_fits(result))
        self.assertNotIn('retrieval_dependencies',result['freshness_check']['freshness'])
        self.assertEqual(result['freshness_check']['audit']['arguments']['snapshot_id'],check['id'])

    def test_frozen_relation_inventory_is_separate_from_mutable_current_frontier(self):
        job=self.relation(); snapshot=capture(self.engine)
        inventory=snapshot.relation_gaps(('s',))
        descriptor=snapshot.descriptor()
        self.assertNotIn('Actual acceptance',json.dumps(descriptor))
        self.assertEqual(_restore_frozen(self.engine,descriptor).relation_gaps(('s',)),inventory)
        with self.store.transaction(write=True) as db:
            proposals=json.loads(db.execute('SELECT proposals FROM memory_capture_jobs WHERE id=?',(job,)).fetchone()[0])
            proposals[0]['condition']='A changed mandatory condition.'
            db.execute('UPDATE memory_capture_jobs SET proposals=? WHERE id=?',(json.dumps(proposals),job))
        self.assertEqual(snapshot.relation_gaps(('s',)),inventory)
        with self.assertRaisesRegex(ValueError,'frontier changed'):
            _restore_frozen(self.engine,descriptor)

    def test_generation_failure_and_unreviewed_summary_are_not_complete_answers(self):
        view,check=self.build()
        failed=self.service._query_result(view,{},self.service.status(scope='s'),check,'s',None,{'message':'failed'})
        self.assertFalse(failed['answer_payload_complete'])
        self.assertFalse(failed['answer_accepted'])
        check={**check,'overview_review':{**check['overview_review'],'status':'unresolved'}}
        unreviewed=self.result(view,check)
        self.assertFalse(unreviewed['answer_accepted'])

    def test_canonical_only_engine_does_not_require_optional_capture_queue_schema(self):
        with MemoryStore(':memory:') as store:
            engine=MemoryEngine(store)
            snapshot=capture(engine)
            self.assertEqual(snapshot.relation_gaps(('s',))['total'],0)

    def test_unapproved_candidate_cannot_disclose_cross_scope_endpoint_body(self):
        self.store.append_assertion(assertion_id='private-endpoint',text='Private unique endpoint body.',
            actor='other',source='fixture://private',episode='private',scope='other',recorded_at='2026-01-01')
        self.relation(to_id='private-endpoint')
        inventory=capture(self.engine).relation_gaps(('s',))
        self.assertNotIn('Private unique endpoint body',json.dumps(inventory))
        with self.assertRaises(ModelError): self.build()

    def test_legacy_snapshot_cannot_acquire_new_mutable_relation_inventory(self):
        self.relation(); descriptor=capture(self.engine).descriptor()
        descriptor.pop('relation_processing_frontier')
        self.assertEqual(_restore_frozen(self.engine,descriptor).relation_gaps(('s',))['total'],0)

    def test_changed_relation_revision_cannot_reuse_old_semantic_applicability(self):
        job=self.relation(); _,before=self.build(); self.requests=[]
        with self.store.transaction(write=True) as db:
            proposals=json.loads(db.execute('SELECT proposals FROM memory_capture_jobs WHERE id=?',(job,)).fetchone()[0])
            proposals[0]['reason']='A newly reviewed ambiguity about a different object.'
            db.execute('UPDATE memory_capture_jobs SET proposals=? WHERE id=?',(json.dumps(proposals),job))
        view,after=self.build()
        self.assertTrue(any(r.task=='Review grounded memory overview' for r in self.requests))
        self.assertNotEqual(before['overview_review']['relation_gap_review']['inventory']['frontier_sha256'],
            after['overview_review']['relation_gap_review']['inventory']['frontier_sha256'])
        self.assertFalse(self.result(view,after)['refresh_required'])

    def test_source_frontier_change_invalidates_relation_applicability_receipt(self):
        self.relation(); view,check=self.build()
        event=self.service.queue.capture_event('new-source','known-empty','No assertion about the asked serial.',
            source='fixture://known-empty',scope='s',recorded_at='2026-01-02')
        result={'reviews':[],'source_revision':{'schema':'source-interpretation/v1','event_id':event.id,
            'protocol':'fixture-v1','input_fingerprint':'new-input','status':'accepted_complete',
            'assertion_ids':[],'reconciliation':[]}}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,'empty-source','2026-01-03',json.dumps(result)))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',proposals='[]' WHERE id=?",(event.id,))
        self.assertFalse(self.service._query_processing_gaps('s',None,{},freshness_only=True,
            relation_exclusions=[{'job_id':'relation-0','proposal_index':0}]))
        result=self.result(view,check)
        self.assertTrue(result['refresh_required'])
        self.assertIsNone(result['source_status']['processing_gaps_apply_to_query'])

    def test_malformed_relation_proposal_stays_operational_without_forcing_semantic_review(self):
        self.relation(to_id=None); self.omit=True
        view,check=self.build(); result=self.result(view,check)
        self.assertEqual(result['processing_state'],'needs_review')
        self.assertTrue(result['refresh_required'])
        self.assertTrue(result['source_status']['processing_gaps_apply_to_query'])
        self.assertNotIn('relation_gap_review',check['overview_review'])

    def alias_query(self):
        original=self.engine.model
        def provider(request):
            if request.task=='Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id':self.spec.id,'related_view_ids':[],
                    'subquestions':[],'reason':'The fixture planner judges these questions semantically equivalent.'})
            return original(request)
        self.engine.model=provider; self.requests=[]
        result=self.service.query('Which serial was reported for the printer?',scope='s')
        self.assertEqual(sum(r.task=='Review grounded memory overview' for r in self.requests),1)
        return result

    def test_equivalent_question_reuse_gets_independent_exact_new_question_review(self):
        view,check=self.build(); result=self.alias_query()
        self.assertEqual(result['view']['id'],view.id)
        self.assertEqual(result['freshness_check']['review_status'],'accepted')
        self.assertTrue(result['answer_payload_complete'])
        self.assertTrue(result['answer_accepted'])
        certificate=self.engine.view_checks(self.spec.id)[-1]
        self.assertEqual(certificate['overview_review']['query_context']['question'],'Which serial was reported for the printer?')
        self.assertEqual(certificate['overview_review']['certificate']['kind'],'answer_applicability')
        self.assertEqual(self.engine.latest_check(view)['id'],check['id'])

    def test_alias_obtains_fresh_question_local_relation_clearance(self):
        self.relation(); self.build(); result=self.alias_query()
        self.assertTrue(result['answer_accepted'])
        self.assertFalse(result['refresh_required'])
        self.assertFalse(result['source_status']['processing_gaps_apply_to_query'])
        certificate=self.engine.view_checks(self.spec.id)[-1]
        self.assertEqual(certificate['overview_review']['query_context']['question'],'Which serial was reported for the printer?')
        self.assertIn('relation_gap_review',certificate['overview_review'])

    def test_mcp_instructions_separate_required_answer_pages_from_optional_proof(self):
        from pathlib import Path
        text=(Path(__file__).parents[1]/'mcp_server.py').read_text()
        self.assertNotIn('When response_complete is false, follow every continuation',text)
        self.assertIn('answer_continuations',text)
        self.assertIn('optional',text)
        self.assertIn('qualifications',text)


if __name__=='__main__': unittest.main()
