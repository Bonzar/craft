"""Fictional scripted judgments prove contracts/lifecycle, not real-model semantics."""
import copy
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class ZeroCanonicalRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.now=datetime(2026,2,1,tzinfo=timezone.utc)
        self.queue=CaptureQueue(self.store,clock=lambda:self.now)
        self.body='Lio reports that the available notes do not contain the promised comparison. The year is unspecified.'
        self.event=self.queue.capture_event('fiction','report',self.body,actor='Lio',scope='lab',source='fixture://report',position=1)
        self.calls=[]; self.declare=True; self.recover=False; self.modify=None; self.failure=None
        self.engine=MemoryEngine(self.store,model=self.provider,verify_overviews=True)
        self.worker=LinkWorker(self.queue,self.engine,self.provider)
        for _ in range(5):
            if self.worker.run_once() is None:break
        self.assertEqual(self.store.list_assertions(),[])
        self.history=copy.deepcopy(self.queue.extraction_history(self.event.id))
        self.frozen=self.queue.freeze_input(self.event.id)
        self.spec=ViewSpec('origin','Origin','What did Lio report about the available notes?',scopes=('lab',))
        self.engine.define_view(self.spec)

    def provider(self,request):
        self.calls.append(request)
        if request.task.startswith('Extract'):return json.dumps({'assertions':[]})
        if request.task=='Review source material coverage':
            interpretations=[r for r in request.records if r.get('role')=='interpretation']
            missing=self.recover and not interpretations
            value={'status':'incomplete' if missing else 'accepted' if interpretations else 'unresolved',
                'reason':'Compared the attributed report with interpretations.',
                'omissions':[{'quote':self.body,'reason':'The bounded attributed report is absent.'}] if missing else []}
            critiques=[r for r in request.records if r.get('role')=='source_omission_hypothesis']
            if critiques:value['gap_dispositions']=[{'request_id':r['request_id'],
                'disposition':'repaired' if interpretations else 'unresolved','reason':'Independently compared exact source.'} for r in critiques]
            return json.dumps({'source_coverage':value})
        if request.task=='Repair omitted source meanings':
            return json.dumps({'assertions':[{'text':'Lio reports that the available notes lack the promised comparison; the year is unknown.','quote':self.body}]})
        if request.task.startswith('Verify contextual'):
            return json.dumps({'verdicts':[{'index':r['index'],'status':'accepted','reason':'Exact attributed report; no world-level absence or inferred date.'} for r in request.records if r.get('role')=='interpretation']})
        if request.task.startswith('Propose'):return json.dumps({'links':[]})
        if request.task=='Plan memory retrieval for a question':return json.dumps({'reuse_view_id':None,'related_view_ids':[],'subquestions':[],'reason':'Direct question.'})
        if request.task.startswith('Review grounded'):
            if self.failure and any(r.get('role')=='review_contract_feedback' for r in request.records):raise self.failure
            inventory=next((r for r in request.records if r.get('role')=='source_gap_inventory'),None)
            draft=next(r['text'] for r in request.records if r.get('role')=='overview_draft')
            value={'status':'accepted','coverage':'all_claims','reason':'Conservative attributed answer.','issues':[],'context_requests':[],'unknown_needs':[]}
            if inventory:
                value['source_gap_dispositions']=[{'event_id':r['event_id'],'run_id':r['run_id'],'disposition':'material',
                    'reason':'The original has a bounded attributed report omitted from accepted interpretation.',
                    'source_ids':[],'answer_qualified':True,'qualification_quote':draft.split(' [source:')[0],
                    **({'source_omission':{'start':0,'end':len(self.body)}} if self.declare else {})} for r in inventory['gaps']]
            if self.modify:self.modify(value,request)
            return json.dumps(value)
        facts=[r for r in request.records if r.get('disposition')=='assertion']
        return ('Lio reports that the available notes lack the promised comparison; the year is unknown. [source:'+facts[0]['id']+']') if facts else 'The report awaits accepted interpretation; no conclusion about the comparison is established.'

    def query(self,**kwargs):return self.engine.build_view('origin',**kwargs)
    def requests(self):
        with self.store.transaction() as db:return [dict(r) for r in db.execute('SELECT * FROM memory_source_audit_requests')]

    def test_same_protocol_zero_prior_witness_schedules_one_exact_request(self):
        self.query(); rows=self.requests()
        self.assertEqual(len(rows),1,'Exact independent gap witness must schedule with no canonical anchor.')
        evidence=json.loads(rows[0]['evidence_json'])
        self.assertEqual(evidence['anchor_kind'],'source_gap')
        self.assertNotIn('canonical_id',evidence)
        self.assertEqual(evidence['event_id'],self.event.id)
        self.assertEqual(evidence['proof_event_id'],self.event.id)
        self.assertEqual(evidence['prior_ids'],[])
        self.assertEqual(evidence['input_fingerprint'],self.frozen.fingerprint)
        for key in ('gap_entry_sha256','delivery_sha256','review_request_sha256','frame_sha256','snapshot_sha256','body_sha256','quote_sha256'):self.assertTrue(evidence[key])
        self.query();self.assertEqual(len(self.requests()),1)

    def test_worker_alone_repairs_and_refreshes_exact_origin(self):
        old=self.query(); before=copy.deepcopy(old.__dict__);self.recover=True
        with patch.object(self.engine,'_impact_selection',return_value=([],{})),patch.object(self.engine.candidates,'rank',return_value=[]):
            for _ in range(20):
                if self.worker.run_once() is None:break
        self.assertEqual(self.requests()[0]['status'],'completed')
        current=self.engine.get_version(self.engine.inspect_view('origin')['current_id'])
        self.assertIn('Lio reports',current.summary)
        self.assertEqual(self.engine.get_version(old.id).__dict__,before)
        self.assertEqual(self.queue.extraction_history(self.event.id)[:len(self.history)],self.history)
        self.assertTrue(all(r['valid_at'] is None for r in self.engine.capture_snapshot().assertions.values()))
        critique=next(r for c in self.calls for r in c.records if r.get('role')=='source_omission_hypothesis')
        self.assertNotIn('canonical_id',critique)
        self.assertEqual(critique['use'],'OPERATIONAL_CRITIQUE_NOT_SOURCE_FACT')

    def test_material_alone_does_not_admit(self):
        self.declare=False;self.query();self.assertEqual(self.requests(),[])

    def test_invalid_span_or_disposition_never_admits(self):
        for change in ({'source_omission':{'start':True,'end':3}},{'source_omission':{'start':-1,'end':3}},
                       {'source_omission':{'start':0,'end':9999}},{'source_omission':{'start':0,'end':0}},
                       {'source_omission':{'start':0,'end':2,'event_id':'forged'}},{'event_id':'forged'},
                       {'run_id':9999},{'disposition':'nonmaterial'},{'answer_qualified':False}):
            with self.subTest(change=change):
                self.modify=lambda v,r:v['source_gap_dispositions'][0].update(change)
                with self.assertRaises(ModelError):self.query()
                self.assertEqual(self.requests(),[])

    def test_valid_observation_survives_later_operational_failure(self):
        self.modify=lambda v,r:v.update(coverage='bad-format')
        self.failure=PermissionError('fictional provider transport denied')
        with self.assertRaisesRegex(ModelError,'fictional provider transport denied'):self.query()
        self.assertEqual(len(self.requests()),1)
        audit=self.engine.view_checks('origin')[-1]['overview_review']
        self.assertIn('fictional provider transport denied',audit['generation_error'])
        self.assertEqual(audit['status'],'unresolved')

    def test_repair_cannot_drop_or_change_explicit_witness(self):
        def modify(v,r):
            if any(x.get('role')=='review_contract_feedback' for x in r.records):v['source_gap_dispositions'][0].pop('source_omission')
            else:v['coverage']='bad-format'
        self.modify=modify
        with self.assertRaises(ModelError):self.query()
        self.assertEqual(len(self.requests()),1)

    def test_commit_revision_race_does_not_schedule(self):
        publish=self.engine._publish
        def stale(*args,**kwargs):
            with self.store.transaction(write=True) as db:
                row=db.execute('SELECT * FROM memory_extraction_runs ORDER BY id DESC LIMIT 1').fetchone()
                db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                    (row['event_id'],'concurrent-run',row['completed_at'],row['result']))
            return publish(*args,**kwargs)
        with patch.object(self.engine,'_publish',stale):self.query()
        self.assertEqual(self.requests(),[])
        self.assertEqual(self.engine.view_checks('origin')[-1]['overview_review']['source_audit_admissions'][0]['status'],'stale')

    def test_transaction_rollback_leaves_no_check_or_request(self):
        from prototypes.assertion_memory import source_omission
        persist=source_omission.persist
        def fail(*args):persist(*args);raise RuntimeError('fictional rollback')
        with patch.object(source_omission,'persist',fail):
            with self.assertRaisesRegex(RuntimeError,'fictional rollback'):self.query()
        self.assertEqual(self.requests(),[]);self.assertEqual(self.engine.view_checks('origin'),[])

    def declaration_fixture(self):
        from types import SimpleNamespace
        from prototypes.assertion_memory.query_snapshot import _restore_frozen
        self.query()
        check=self.engine.view_checks('origin')[-1]
        snapshot=_restore_frozen(self.engine,check['query_snapshot'])
        request=next(r for r in self.calls if r.task=='Review grounded memory overview')
        frame=next(r for r in request.records if r.get('role')=='query_context')
        inventory=next(r for r in request.records if r.get('role')=='source_gap_inventory')
        item=json.loads(self.provider(request))['source_gap_dispositions'][0]
        ledger=SimpleNamespace(source_gap_inventory=inventory,source_resolver=snapshot.source_proof_resolver(self.engine))
        return ledger,request,frame,item,snapshot

    def test_exact_delivery_guards_public_missing_truncated_substituted_and_out_of_scope(self):
        from dataclasses import replace
        from prototypes.assertion_memory.source_omission import declare_gap
        ledger,request,frame,item,snapshot=self.declaration_fixture()
        text=item['qualification_quote']
        for mode in ('absent','public','truncated','substituted','wrong_scope','wrong_run','context_only'):
            with self.subTest(mode=mode):
                inventory=copy.deepcopy(ledger.source_gap_inventory)
                gap=inventory['gaps'][0]
                if mode=='public':gap.pop('raw_source')
                if mode=='truncated':gap['raw_source']['text']=gap['raw_source']['text'][:-1]
                if mode=='substituted':gap['raw_source']['text']='z'*len(self.body)
                if mode=='wrong_scope':gap['scope']='private'
                if mode=='wrong_run':gap['run_id']+=100
                if mode=='context_only':gap['context_only']=True
                records=[r for r in request.records if r.get('role')!='source_gap_inventory']
                if mode!='absent':records.append(inventory)
                with self.assertRaises(ModelError):declare_gap(item,ledger,replace(request,records=records),frame,text)
        # Equal offered/delivered bytes still cannot rewrite frozen source authority.
        forged=copy.deepcopy(ledger.source_gap_inventory);forged['gaps'][0]['raw_source']['text']='z'*len(self.body)
        ledger.source_gap_inventory=forged
        with self.assertRaises(ModelError):declare_gap(item,ledger,replace(request,records=[
            forged if r.get('role')=='source_gap_inventory' else r for r in request.records]),frame,text)

    def test_legacy_raw_watermark_and_recorded_cutoff_cannot_grant_authority(self):
        from dataclasses import replace
        from prototypes.assertion_memory.source_omission import declare_gap
        ledger,request,frame,item,snapshot=self.declaration_fixture()
        for changed in (replace(snapshot,raw_event_watermark=None),replace(snapshot,raw_event_watermark=0),
                        replace(snapshot,effective_recorded_as_of='2025-12-31T00:00:00+00:00'),
                        replace(snapshot,source_revision_watermark=0)):
            with self.subTest(changed=changed.descriptor()):
                ledger.source_resolver=changed.source_proof_resolver(self.engine)
                with self.assertRaises(ModelError):declare_gap(item,ledger,request,frame,item['qualification_quote'])

    def test_prefix_outside_recorded_cutoff_or_changed_input_rejects_witness(self):
        from prototypes.assertion_memory.source_omission import declare_gap
        ledger,request,frame,item,snapshot=self.declaration_fixture()
        self.queue.capture_event('fiction','late-prefix','Earlier source arrived later.',actor='Lio',scope='lab',
            source='fixture://late-prefix',position=0)
        with self.assertRaises(ModelError):declare_gap(item,ledger,request,frame,item['qualification_quote'])

    def test_revalidation_rejects_modified_witness_hashes(self):
        from prototypes.assertion_memory.source_omission import _revalidate
        ledger,request,frame,item,snapshot=self.declaration_fixture()
        evidence=json.loads(self.requests()[0]['evidence_json'])
        audit=self.engine.view_checks('origin')[-1]['overview_review']
        self.assertTrue(_revalidate(self.engine,snapshot,evidence,audit,self.spec))
        for key in ('gap_entry_sha256','delivery_sha256','snapshot_sha256','body_sha256','quote_sha256','frame_sha256'):
            with self.subTest(key=key):self.assertFalse(_revalidate(self.engine,snapshot,{**evidence,key:'bad'},audit,self.spec))

    def test_attempt_cap_retains_truthful_unscheduled_receipt(self):
        for i in range(2):self.queue.request_source_audit(self.event.id,'unrelated-'+str(i))
        self.query();self.assertEqual(len(self.requests()),2)
        receipt=self.engine.view_checks('origin')[-1]['overview_review']['source_audit_admissions'][0]
        self.assertEqual(receipt['status'],'cap_exhausted');self.assertFalse(receipt['scheduled'])

    def test_unresolved_audit_keeps_zero_records_and_visible_semantic_gap(self):
        from prototypes.assertion_memory.service import MemoryService
        self.query()
        for _ in range(20):
            if self.worker.run_once() is None:break
        self.assertEqual(self.requests()[0]['status'],'completed')
        self.assertEqual(self.store.list_assertions(),[])
        self.assertEqual(self.queue.extraction_history(self.event.id)[-1]['source_revision']['status'],'unresolved')
        self.assertTrue(MemoryService(self.store,self.engine)._query_processing_gaps('lab',None,{}))
        self.assertIsNone(self.worker.seconds_until_work(with_model=True))

    def test_recorded_cutoff_excludes_newly_recovered_meaning(self):
        self.query();self.now=datetime(2026,2,3,tzinfo=timezone.utc);self.recover=True
        for _ in range(20):
            if self.worker.run_once() is None:break
        self.assertEqual(self.engine.capture_snapshot(recorded_as_of='2026-02-02').assertions,{})
        self.assertEqual(len(self.engine.capture_snapshot(recorded_as_of='2026-02-04').assertions),1)

    def test_changed_origin_is_stale_and_does_not_overwrite_old_view(self):
        from dataclasses import replace
        old=self.query();self.recover=True;self.worker.run_once()
        original=self.engine._spec
        with patch.object(self.engine,'_spec',side_effect=lambda key:replace(self.spec,query='Different purpose') if key=='origin' else original(key)):
            for _ in range(5):
                if self.worker.run_once() is None:break
        evidence=json.loads(self.requests()[0]['evidence_json'])
        self.assertEqual(evidence['refresh_outcomes'][0]['status'],'stale_origin')
        self.assertEqual(self.engine.inspect_view('origin')['current_id'],old.id)

    def test_live_audit_lease_coalesces_without_busy_loop(self):
        self.queue.request_source_audit(self.event.id,'existing')
        self.queue._maintenance_tick();claim=self.queue._claim(with_model=True,maintenance_only=True)
        self.assertIsNotNone(claim);self.query()
        self.assertEqual(self.worker.seconds_until_work(with_model=True),self.queue._lease_seconds)
        self.assertIsNone(self.worker.run_once())

    def test_empty_prior_provider_failures_remain_operational_not_semantic_terminal(self):
        import sqlite3
        from prototypes.assertion_memory.answer_source_gaps import semantic_terminal
        from prototypes.assertion_memory.source_reprocessing import audit
        for failure in (PermissionError('denied'),TimeoutError('timeout'),sqlite3.OperationalError('db'),
                        OSError('io'),ModelError('budget or schema failure')):
            with self.subTest(failure=type(failure).__name__):
                def unavailable(request):raise failure
                result=audit(unavailable,self.queue,self.frozen)
                self.assertEqual(result['outcome'],'failed')
                self.assertFalse(semantic_terminal({**result,'source_revision':{'status':'unresolved'}}))
                self.assertTrue(any(str(failure) in r['reason'] for r in result['reviews']))


    def test_late_equal_reason_model_error_is_not_a_parsed_semantic_outcome(self):
        from prototypes.assertion_memory.answer_source_gaps import semantic_terminal
        from prototypes.assertion_memory.source_reprocessing import audit
        for stage in ('repair','verify'):
            for error in (ModelError('schema failure'),ModelError('other schema failure'),PermissionError('schema failure')):
                with self.subTest(stage=stage,error=type(error).__name__,reason=str(error)):
                    def provider(request):
                        if request.task=='Review source material coverage':
                            return json.dumps({'source_coverage':{'status':'incomplete','reason':'schema failure',
                                'omissions':[{'quote':self.body,'reason':'Missing bounded attributed report.'}]}})
                        if request.task=='Repair omitted source meanings' and stage=='verify':
                            return json.dumps({'assertions':[{'text':'Lio reports that the available notes lack the promised comparison.','quote':self.body}]})
                        raise error
                    result=audit(provider,self.queue,self.frozen)
                    self.assertEqual(result['outcome'],'failed')
                    self.assertFalse(semantic_terminal({**result,'source_revision':{'status':'unresolved'}}))
                    receipts=[r for r in result['context_recovery'] if r.get('operation')=='source_coverage']
                    self.assertTrue(any('attempt' not in r for r in receipts),'A caught error must keep controller-derived unparsed provenance.')

    def test_null_optional_declaration_uses_one_bounded_format_repair(self):
        def modify(value,request):
            disposition=value['source_gap_dispositions'][0]
            if any(r.get('role')=='review_contract_feedback' for r in request.records):disposition.pop('source_omission')
            else:disposition['source_omission']=None
        self.modify=modify
        self.query();self.assertEqual(self.requests(),[])
        reviews=[r for r in self.calls if r.task=='Review grounded memory overview']
        self.assertEqual(len(reviews),2)
        self.assertTrue(any(r.get('role')=='review_contract_feedback' for r in reviews[-1].records))

    def test_persistent_null_declaration_does_not_escape_or_exceed_shared_repair_cap(self):
        self.modify=lambda value,request:value['source_gap_dispositions'][0].update(source_omission=None)
        with self.assertRaises(ModelError):self.query()
        self.assertEqual(self.requests(),[])
        self.assertEqual(sum(r.task=='Review grounded memory overview' for r in self.calls),2)

    def test_null_declaration_does_not_prevent_later_valid_witness_observation(self):
        second=self.queue.capture_event('fiction','second-report',self.body+' Another undated report.',
            actor='Lio',scope='lab',source='fixture://second-report',position=2)
        for _ in range(8):
            if self.worker.run_once() is None:break
        def modify(value,request):
            for item in value['source_gap_dispositions']:
                if item['event_id']==self.event.id:item['source_omission']=None
        self.modify=modify
        with self.assertRaises(ModelError):self.query()
        self.assertEqual([r['event_id'] for r in self.requests()],[second.id])
        self.assertEqual(sum(r.task=='Review grounded memory overview' for r in self.calls),2)



class ZeroSetCoverageContractTest(unittest.TestCase):
    def test_empty_comparison_set_keeps_bounded_attributed_meaning_obligation(self):
        from prototypes.assertion_memory.source_coverage import _COVERAGE,_REPAIR
        # Contract projection, supported by saved actual-provider research32
        # failure; this string check is not evidence of real semantic recovery.
        self.assertIn('Zero accepted interpretations is a valid comparison set',_COVERAGE)
        self.assertIn('bounded attributed report',_COVERAGE)
        self.assertIn('missing referent does not erase independently entailed',_COVERAGE)
        self.assertIn('bare ambiguous assent',_COVERAGE)
        self.assertIn('Zero accepted interpretations',_REPAIR)
