"""Fault-injected missed canonical facet; deterministic lifecycle, not real LLM ingestion."""
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError, SemanticUnavailable
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class QuerySemanticRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.now=datetime(2026,2,1,tzinfo=timezone.utc)
        self.queue=CaptureQueue(self.store,clock=lambda:self.now)
        self.body='Mira selected the amber route. Its proposer stated that it avoids the narrow passage.'
        self.event=self.queue.capture_event('fiction','choice',self.body,actor='Mira',scope='lab',source='fixture://choice')
        quote='Mira selected the amber route.'
        self.store.append_assertion(assertion_id='choice',text=quote,actor='Mira',source=self.event.source,
            episode='fiction',scope='lab',recorded_at=self.event.recorded_at,metadata={'event_id':self.event.id,
            'excerpt':quote,'source_span':{'start':0,'end':len(quote)},'interpretation':{'status':'model_verified'},
            'evidence_refs':[{'event_id':self.event.id,'start':0,'end':len(quote),'role':'user','relation':'statement'}]})
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (self.event.id,'injected-old-miss',self.queue._time(),json.dumps({'assertion_ids':['choice']})))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',extracted_ids='[\"choice\"]'")
        self.calls=[]; self.recover=False; self.declare=True; self.fail_review=False
        self.engine=MemoryEngine(self.store,model=self.provider,verify_overviews=True)
        # Explicitly injected fault: current same-protocol coverage missed a material facet.
        self.worker=LinkWorker(self.queue,self.engine,self.provider)
        for _ in range(5):
            if self.worker.run_once() is None: break
        self.before=self.queue.freeze_input(self.event.id)
        self.old_history=json.dumps(self.queue.extraction_history(self.event.id),sort_keys=True)
        self.spec=ViewSpec('origin','Origin','What reason did the route proposer state?',scopes=('lab',),root_ids=('choice',))
        self.engine.define_view(self.spec)

    def provider(self,request):
        self.calls.append(request)
        if request.task=='Plan memory retrieval for a question':
            return json.dumps({'reuse_view_id':None,'related_view_ids':[],'subquestions':[],'reason':'Direct question.'})
        if request.task=='Review source material coverage':
            interpretations=[r['text'] for r in request.records if r.get('role')=='interpretation']
            missing=self.recover and not any('proposer stated' in t for t in interpretations)
            value={'status':'incomplete' if missing else 'accepted','reason':'Independently checked originals.',
                'omissions':[{'quote':self.body,'reason':'The attributed proposer reason is omitted.'}] if missing else []}
            critique=[r for r in request.records if r.get('role')=='source_omission_hypothesis']
            if critique:
                value['gap_dispositions']=[{'request_id':r['request_id'],'disposition':'repaired' if not missing else 'unresolved',
                    'reason':'Compared the exact original and accepted interpretations.'} for r in critique]
            return json.dumps({'source_coverage':value})
        if request.task=='Repair omitted source meanings':
            return json.dumps({'assertions':[{'text':'The route proposer stated that the amber route avoids the narrow passage.',
                'quote':self.body}]})
        if request.task.startswith('Verify contextual'):
            return json.dumps({'verdicts':[{'index':r['index'],'status':'accepted','reason':'Exact raw statement verified.'}
                for r in request.records if r.get('role')=='interpretation']})
        if request.task.startswith('Propose evidence links'): return json.dumps({'links':[]})
        if request.task.startswith('Review grounded'):
            proof=next((r for r in request.records if r.get('role')=='source_evidence'),None)
            draft=next(r['text'] for r in request.records if r.get('role')=='overview_draft')
            if not proof:
                return json.dumps({'status':'revise','coverage':'partial','reason':'Exact original is needed.','issues':[],
                    'context_requests':[{'operation':'read','source_ids':['choice'],'include_source_evidence':True,'gap_id':'reason'}],
                    'unknown_needs':[]})
            canonical=[r.get('text','') for r in request.records if r.get('disposition')=='assertion']
            recovered=any('proposer stated' in t for t in canonical)
            need={'basis':'searched_unresolved','gap_id':'reason','quote':'The proposer reason awaits accepted interpretation.',
                'reason':'The complete original states an attributed reason absent from the canonical choice.',
                'source_ids':['choice'],'include_source_evidence':True}
            if self.declare:
                body=proof['bodies'][0]
                need['source_omission']={'canonical_id':'choice','proof_id':body['proof_id'],'start':len('Mira selected the amber route. '),'end':len(self.body)}
            return json.dumps({'status':'revise' if self.fail_review else 'accepted','coverage':'all_claims',
                'reason':'Qualified pending independent interpretation.' if not recovered else 'Recovered attributed meaning verified.',
                'issues':[],'unknown_needs':[] if recovered else [need]})
        if request.task.startswith(('Write','Draft','Compose','Synthesize')) or any(r.get('role')=='query_context' for r in request.records):
            facts=[r for r in request.records if r.get('disposition')=='assertion']
            recovered=next((r for r in facts if 'proposer stated' in r.get('text','')),None)
            return ('The proposer stated that the route avoids the narrow passage. [source:'+recovered['id']+']') if recovered else 'The proposer reason awaits accepted interpretation. [source:choice]'
        if request.task=='Propose assertion links' or request.task.startswith('Propose evidence links'): return json.dumps({'links':[]})
        raise AssertionError(request.task)

    def query(self):
        return self.engine.build_view('origin')

    def requests(self):
        with self.store.transaction() as db:
            return [dict(r) for r in db.execute('SELECT * FROM memory_source_audit_requests')]

    def test_same_protocol_query_persists_exactly_one_atomic_recovery_request(self):
        result=self.query()
        self.assertEqual(len(self.requests()),1,'A reviewed positive omission must schedule autonomous reprocessing.')
        request=self.requests()[0]
        self.assertEqual(request['status'],'pending')
        evidence=json.loads(request['evidence_json'])
        self.assertEqual(evidence['event_id'],self.event.id)
        self.assertEqual(evidence['input_fingerprint'],self.before.fingerprint)
        self.assertEqual(evidence['origins'][0]['view_id'],'origin')
        self.assertEqual(evidence['origins'][0]['question'],self.spec.query)
        self.assertTrue(evidence['review_request_sha256'])
        self.assertIn('awaits accepted',result.summary)
        self.assertEqual(self.queue.freeze_input(self.event.id).fingerprint,self.before.fingerprint)

    def test_failure_check_retains_and_schedules_independently_validated_omission(self):
        self.fail_review=True
        with self.assertRaises(ModelError): self.query()
        self.assertEqual(len(self.requests()),1)
        with self.store.transaction() as db:
            check=db.execute('SELECT * FROM memory_view_checks ORDER BY id DESC LIMIT 1').fetchone()
            self.assertTrue(json.loads(check['overview_review'])['source_omissions'])
            self.assertEqual(json.loads(self.requests()[0]['evidence_json'])['check_id'],check['id'])

    def test_genuine_unknown_does_not_schedule(self):
        self.declare=False; self.query(); self.assertEqual(self.requests(),[])

    def test_worker_alone_repairs_and_refreshes_pinned_origin_without_second_query(self):
        old=self.query(); old_bytes=json.dumps(self.engine.get_version(old.id).__dict__,sort_keys=True)
        self.recover=True
        for _ in range(30):
            if self.worker.run_once() is None: break
        self.assertEqual(self.requests()[0]['status'],'completed')
        current=self.engine.get_version(self.engine.inspect_view('origin')['current_id'])
        self.assertIn('avoids the narrow passage',current.summary)
        self.assertEqual(json.dumps(self.engine.get_version(old.id).__dict__,sort_keys=True),old_bytes)
        self.assertEqual(json.dumps(self.queue.extraction_history(self.event.id)[:2],sort_keys=True),self.old_history)
        self.assertEqual(self.queue.freeze_input(self.event.id).fingerprint,self.before.fingerprint)
        self.assertTrue(any(r.get('role')=='source_omission_hypothesis' for c in self.calls for r in c.records))

    def test_pending_request_is_visible_before_tick_with_scoped_discovery_time(self):
        self.query(); service=MemoryService(self.store,self.engine)
        self.assertTrue(service._query_processing_gaps('lab',None,{}))
        self.assertFalse(service._query_processing_gaps('elsewhere',None,{}))
        self.assertFalse(service._query_processing_gaps('lab','2026-01-31',{}))
        self.assertEqual(self.worker.seconds_until_work(with_model=True),0)

    def test_repeated_same_omission_deduplicates(self):
        self.query(); self.query(); self.assertEqual(len(self.requests()),1)

    def test_cap_does_not_report_ghost_id_as_scheduled(self):
        for index in range(2): self.queue.request_source_audit(self.event.id,'unrelated-'+str(index))
        result=self.query()
        self.assertEqual(len(self.requests()),2)
        check=self.engine.view_checks('origin')[-1]
        receipts=check['overview_review']['source_audit_admissions']
        self.assertEqual(receipts[0]['status'],'cap_exhausted')
        self.assertFalse(receipts[0]['scheduled'])

    def override_verdict(self,mutate):
        provider=self.provider
        def wrapped(request):
            value=provider(request)
            if request.task.startswith('Review grounded'):
                parsed=json.loads(value)
                if any('source_omission' in n for n in parsed.get('unknown_needs',[])):
                    mutate(parsed,request)
                return json.dumps(parsed)
            return value
        self.engine.model=wrapped

    def test_forged_ranges_ids_and_writer_only_requests_fail_closed(self):
        for change in ({'canonical_id':'hidden'},{'proof_id':'forged'},{'start':True},
                       {'start':-1},{'end':9000},{'event_id':'arbitrary'}):
            with self.subTest(change=change):
                self.override_verdict(lambda value,request:value['unknown_needs'][0]['source_omission'].update(change))
                with self.assertRaises(ModelError):self.query()
                self.assertEqual(self.requests(),[])

    def test_structural_repair_cannot_silently_drop_validated_omission(self):
        def mutate(value,request):
            if 'Repair' in request.task:
                value['unknown_needs'][0].pop('source_omission')
            else:value['coverage']='bad-format'
        self.override_verdict(mutate)
        with self.assertRaises(ModelError):self.query()
        self.assertEqual(len(self.requests()),1,'Valid original independent declaration must survive a failed structural repair.')

    def test_transaction_rollback_persists_neither_check_nor_audit_request(self):
        from prototypes.assertion_memory import source_omission
        original=source_omission.persist
        def failed(*args):
            original(*args)
            raise RuntimeError('injected transaction loss')
        with patch.object(source_omission,'persist',failed):
            with self.assertRaisesRegex(RuntimeError,'transaction loss'):self.query()
        self.assertEqual(self.requests(),[])
        self.assertEqual(self.engine.view_checks('origin'),[])

    def test_stale_revision_at_check_commit_is_unscheduled(self):
        publish=self.engine._publish
        def changed(*args,**kwargs):
            with self.store.transaction(write=True) as db:
                db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                    (self.event.id,'concurrent-revision',self.queue._time(),json.dumps({'assertion_ids':['choice']})))
            return publish(*args,**kwargs)
        with patch.object(self.engine,'_publish',changed):self.query()
        self.assertEqual(self.requests(),[])
        self.assertEqual(self.engine.view_checks('origin')[-1]['overview_review']['source_audit_admissions'][0]['status'],'stale')

    def test_independent_audit_cannot_accept_without_explicit_gap_disposition(self):
        self.query()
        base=self.provider
        def omission_ignored(request):
            response=base(request)
            if request.task=='Review source material coverage':
                value=json.loads(response);value['source_coverage'].pop('gap_dispositions',None)
                return json.dumps(value)
            return response
        self.worker.model=omission_ignored
        self.worker.run_once()
        result=self.queue.extraction_history(self.event.id)[-1]
        self.assertNotEqual(result['source_revision']['status'],'accepted_complete')
        self.assertEqual(set(result['assertion_ids']),{'choice'})
        self.assertTrue(any(row.get('operation')=='query_source_omission' and row['status']=='unresolved'
                            for row in result['context_recovery']))

    def test_late_request_during_audit_is_not_marked_consumed(self):
        self.query();self.queue._maintenance_tick()
        claim=self.queue._claim(with_model=True,maintenance_only=True)
        frozen=self.queue._freeze_claim_input(claim)
        late=self.queue.request_source_audit(self.event.id,'late-independent-gap')
        from prototypes.assertion_memory.source_reprocessing import audit
        result=audit(self.provider,self.queue,frozen)
        self.queue._finish_extraction(claim,frozen.event,result)
        by_id={r['id']:r['status'] for r in self.requests()}
        self.assertEqual(by_id[late],'pending')
        self.assertEqual(by_id[frozen.requests[0]['request_id']],'refresh')

    def test_origin_refresh_survives_more_than_eight_unrelated_views_and_no_impact_nomination(self):
        self.query();self.recover=True
        for index in range(12):self.engine.define_view(ViewSpec('unrelated-'+str(index),'Unrelated','Another question '+str(index),scopes=('lab',)))
        with patch.object(self.engine,'_impact_selection',return_value=([],{})):
            for _ in range(12):
                if self.worker.run_once() is None:break
        current=self.engine.get_version(self.engine.inspect_view('origin')['current_id'])
        self.assertIn('avoids the narrow passage',current.summary)
        self.assertEqual(len(self.requests()),1)

    def test_wrong_purpose_origin_is_rejected_without_rewriting_view(self):
        from dataclasses import replace
        from prototypes.assertion_memory import source_omission
        original=source_omission._origin
        with patch.object(source_omission,'_origin',side_effect=lambda engine,spec,snapshot,audit,check_id:
                original(engine,replace(spec,query='A different purpose'),snapshot,audit,check_id)):
            self.query()
        self.assertEqual(self.requests(),[])
        self.assertEqual(self.engine._spec('origin').query,self.spec.query)
        self.assertEqual(self.engine.view_checks('origin')[-1]['overview_review']['source_audit_admissions'][0]['status'],'unavailable_origin')

    def test_new_interpretation_known_only_after_audit_completion(self):
        self.query();self.now=datetime(2026,2,3,tzinfo=timezone.utc);self.recover=True
        for _ in range(15):
            if self.worker.run_once() is None:break
        old=self.engine.capture_snapshot(recorded_as_of='2026-02-02')
        new=self.engine.capture_snapshot(recorded_as_of='2026-02-04')
        self.assertEqual(set(old.assertions),{'choice'})
        self.assertEqual(len(new.assertions),2)
        added=next(r for k,r in new.assertions.items() if k!='choice')
        self.assertEqual(added['recorded_at'],self.event.recorded_at)
        self.assertEqual(self.queue.extraction_history(self.event.id)[-1]['completed_at'],self.queue._time())

    def test_changed_origin_definition_is_preserved_and_receipted_stale(self):
        from dataclasses import replace
        self.query();self.worker.run_once()
        original=self.engine._spec
        with patch.object(self.engine,'_spec',side_effect=lambda key:replace(self.spec,query='A changed purpose') if key=='origin' else original(key)):
            self.worker.run_once()
        self.assertEqual(self.engine._spec('origin').query,self.spec.query)
        request=self.requests()[0]
        self.assertEqual(request['status'],'completed')
        self.assertEqual(json.loads(request['evidence_json'])['refresh_outcomes'][0]['status'],'stale_origin')

    def test_failed_origin_refresh_terminates_request_and_worker_readiness(self):
        self.query();self.worker.run_once()
        def fail(*args,**kwargs):raise ModelError('injected refresh failure')
        with patch.object(self.engine,'build_view',fail):
            for _ in range(3):
                self.worker.run_once();self.now=self.now.replace(hour=self.now.hour+1)
        self.assertEqual(self.requests()[0]['status'],'failed')
        self.assertIsNone(self.worker.seconds_until_work(with_model=True))

    def test_ordinary_service_query_alone_creates_durable_recovery_intent(self):
        result=MemoryService(self.store,self.engine).query(self.spec.query,scope='lab')
        self.assertEqual(len(self.requests()),1)
        self.assertTrue(result['refresh_required'])
        self.assertEqual(result['completeness'],'incomplete')


    def test_independently_covered_sibling_is_supplied_to_origin_even_when_ranker_misses_it(self):
        self.recover=True
        self.queue.request_source_audit(self.event.id,'fixture-create-covered-sibling')
        with patch.object(self.engine,'_impact_selection',return_value=([],{})):
            for _ in range(10):
                if self.worker.run_once() is None:break
        count=len(self.store.list_assertions())
        with patch.object(self.engine.candidates,'rank',return_value=[]):
            old=self.query()
            self.assertIn('awaits accepted',old.summary)
            for _ in range(10):
                if self.worker.run_once() is None:break
        current=self.engine.get_version(self.engine.inspect_view('origin')['current_id'])
        self.assertIn('avoids the narrow passage',current.summary)
        self.assertEqual(len(self.store.list_assertions()),count,'Already covered meaning must not be duplicated.')
        self.assertEqual(len(self.requests()),2)

    def test_ignored_insert_cannot_produce_a_scheduled_receipt(self):
        with self.store.transaction(write=True) as db:
            db.execute("CREATE TRIGGER ignored_source_request BEFORE INSERT ON memory_source_audit_requests BEGIN SELECT RAISE(IGNORE); END")
        self.query()
        self.assertEqual(self.requests(),[])
        admission=self.engine.view_checks('origin')[-1]['overview_review']['source_audit_admissions'][0]
        self.assertFalse(admission['scheduled'])
        self.assertEqual(admission['status'],'unavailable')

    def test_historical_origin_refresh_never_promotes_current_or_backdates_knowledge(self):
        route=self.engine.prepare_question('Which explanation was reported for selecting amber?',scopes=('lab',),recorded_as_of='2026-02-02')
        key=route['view_id']
        old=self.engine.build_view(key,recorded_as_of='2026-02-02')
        self.assertIsNone(self.engine.inspect_view(key)['current_id'])
        self.now=datetime(2026,2,3,tzinfo=timezone.utc);self.recover=True;self.declare=False
        for _ in range(12):
            if self.worker.run_once() is None:break
        self.assertIsNone(self.engine.inspect_view(key)['current_id'])
        self.assertEqual(self.engine.get_version(old.id).summary,old.summary)
        with self.store.transaction() as db:
            snapshot=db.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',
                (json.loads(self.requests()[0]['evidence_json'])['refresh_outcomes'][0]['version_id'],)).fetchone()[0]
        self.assertNotIn('proposer stated that the amber route',snapshot)

    def test_query_after_idle_unlock_keeps_existing_worker_for_source_recovery(self):
        import fcntl,tempfile
        from prototypes.assertion_memory.mcp_server import _idle_handoff
        with tempfile.TemporaryFile() as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            def after_unlock():
                self.query()
                return self.worker.seconds_until_work(with_model=True)
            self.assertFalse(_idle_handoff(owner,after_unlock))
        self.assertEqual(self.requests()[0]['status'],'pending')

    def test_same_frozen_input_cannot_loop_past_two_admitted_attempts(self):
        for _ in range(8):
            self.query()
            for _ in range(8):
                if self.worker.run_once() is None:break
        self.assertLessEqual(len(self.requests()),2)
        self.assertEqual(self.queue.freeze_input(self.event.id).fingerprint,self.before.fingerprint)
        self.assertIsNone(self.worker.seconds_until_work(with_model=True))


    def test_ordinary_affected_refresh_honors_owned_temporal_route_without_recovery_request(self):
        self.declare=False
        route=self.engine.prepare_question('Which explanation was reported for selecting amber?',scopes=('lab',),recorded_as_of='2026-02-02')
        key=route['view_id']
        self.engine.build_view(key,recorded_as_of='2026-02-02')
        self.assertIsNone(self.engine.inspect_view(key)['current_id'])
        with patch.object(self.engine,'_impact_selection',return_value=([key],{})):
            rebuilt=self.engine.refresh_from('choice')[0]
        self.assertEqual(rebuilt.recorded_as_of,'2026-02-02T00:00:00.000000+00:00')
        self.assertIsNone(self.engine.inspect_view(key)['current_id'])
        self.assertEqual(self.requests(),[])

    def test_low_level_unbound_view_retains_current_refresh_semantics(self):
        self.declare=False
        self.engine.build_view('origin',recorded_as_of='2026-02-02')
        route=self.engine.prepare_question(self.spec.query,scopes=('lab',),recorded_as_of='2026-02-02')
        self.assertEqual(route['view_id'],'origin')
        current=self.engine.build_view('origin')
        self.assertIsNone(current.recorded_as_of)
        self.assertEqual(self.engine.inspect_view('origin')['current_id'],current.id)


    def test_provider_failure_before_coverage_retains_explicit_gap_outcome(self):
        self.query()
        def failed(request):raise TimeoutError('injected source provider loss')
        self.worker.model=failed;self.worker.run_once()
        result=self.queue.extraction_history(self.event.id)[-1]
        outcomes=[r for r in result['context_recovery'] if r.get('operation')=='query_source_omission']
        self.assertEqual(len(outcomes),1)
        self.assertEqual(outcomes[0]['dispositions'][0]['disposition'],'unresolved')
        self.assertEqual(outcomes[0]['dispositions'][0]['request_id'],self.requests()[0]['id'])

    def test_equivalent_alias_origins_share_one_admitted_gap(self):
        from dataclasses import replace
        self.query();self.engine.define_view(replace(self.spec,id='alias'));self.engine.build_view('alias')
        self.assertEqual(len(self.requests()),1)
        self.assertEqual({r['view_id'] for r in json.loads(self.requests()[0]['evidence_json'])['origins']},{'origin','alias'})

    def test_fixed_answer_omission_survives_materialized_fallback_without_rediscovery(self):
        self.declare=False;old=self.query();old_check=self.engine.inspect_view('origin')['current_check_id']
        question='What reason did the route proposer state about the amber route?'
        base=self.provider
        def alias_provider(request):
            if request.task=='Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id':'origin','related_view_ids':[],'subquestions':[], 'reason':'Equivalent qualified answer nominated.'})
            fixed=any(r.get('role')=='fixed_answer_applicability' for r in request.records)
            self.declare=fixed
            return base(request)
        self.engine.model=alias_provider;self.worker.model=alias_provider
        result=MemoryService(self.store,self.engine).query(question,scope='lab')
        self.assertEqual(len(self.requests()),1,'Fixed-answer witness must reach actual fallback even when no later reviewer repeats it.')
        evidence=json.loads(self.requests()[0]['evidence_json'])
        origin=evidence['origins'][0]
        self.assertNotEqual(origin['view_id'],'origin')
        self.assertEqual(origin['question'],question)
        self.assertTrue(result['refresh_required'])
        self.recover=True
        with patch.object(self.engine,'_impact_selection',return_value=([],{})):
            for _ in range(16):
                if self.worker.run_once() is None:break
        current=self.engine.get_version(self.engine.inspect_view(origin['view_id'])['current_id'])
        self.assertIn('avoids the narrow passage',current.summary)
        self.assertEqual(self.engine.inspect_view('origin')['current_id'],old.id)
        self.assertEqual(self.engine.inspect_view('origin')['current_check_id'],old_check)
        self.assertEqual(self.engine.get_version(old.id).summary,old.summary)


    def test_legacy_request_schema_is_migrated_before_read_only_health_probe(self):
        self.queue.request_source_audit(self.event.id,'legacy-gap')
        with self.store.transaction(write=True) as db:db.execute('ALTER TABLE memory_source_audit_requests DROP COLUMN evidence_json')
        restarted=CaptureQueue(self.store,clock=lambda:self.now)
        with self.store.transaction() as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(memory_source_audit_requests)')}
        self.assertIn('evidence_json',columns)
        self.assertTrue(MemoryService(self.store,self.engine)._query_processing_gaps('lab',None,{}))
        self.assertEqual(len(self.requests()),1)

    def test_mcp_query_wakes_source_worker_when_no_editorial_work_exists(self):
        import asyncio
        from unittest.mock import Mock
        from prototypes.assertion_memory import mcp_server
        self.query()
        class MCP:
            def __init__(self):self.handlers={}
            def tool(self):
                def register(function):self.handlers[function.__name__]=function;return function
                return register
        mcp=MCP();launcher=Mock()
        mcp_server._register_read_tools(mcp,MemoryService(self.store,self.engine),None,launcher)
        with patch.object(mcp_server,'_query_with_connection',return_value={'ok':True}):
            asyncio.run(mcp.handlers['memory_query'](self.spec.query,scope='lab'))
        launcher.start.assert_called_once()


    def test_pending_recovery_is_counted_and_paginated_before_tick_without_zero_field_bloat(self):
        service=MemoryService(self.store,self.engine)
        self.assertNotIn('source_audit_request_counts',service.status(scope='lab'))
        self.query()
        state=service.status(scope='lab',limit=1)
        self.assertEqual(state['source_audit_request_counts'],{'pending':1})
        self.assertEqual(state['source_audit_requests'][0]['id'],self.requests()[0]['id'])
        self.assertEqual(state['pagination']['source_audit_requests']['total'],1)
        self.assertNotIn('source_audit_request_counts',service.status(scope='elsewhere'))
        response=service.query(self.spec.query,scope='lab')
        self.assertEqual(response['processing_state'],'pending')

    def test_recovery_diagnostic_pages_retain_exact_counts_and_continuations(self):
        service=MemoryService(self.store,self.engine)
        for index in range(5):
            event=self.queue.capture_event('other-fiction',str(index),'An attributed planning report.',scope='lab',actor='Mira',source='fixture://diagnostic')
            with self.store.transaction(write=True) as db:
                db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                    (event.id,'diagnostic-fixture',self.queue._time(),json.dumps({'assertion_ids':[]})))
                db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done' WHERE event_id=?",(event.id,))
            self.queue.request_source_audit(event.id,'diagnostic-gap')
        page=service.status(scope='lab',limit=2)
        self.assertEqual(page['source_audit_request_counts'],{'pending':5})
        self.assertEqual(len(page['source_audit_requests']),2)
        self.assertEqual(page['pagination']['source_audit_requests']['next_offset'],2)
        self.assertFalse(page['response_complete'])
        self.assertTrue(page['continuations'])
        self.assertEqual(len(service.status(scope='lab',offset=4,limit=2)['source_audit_requests']),1)


    def test_exhausted_recovery_budget_retains_explicit_answer_limitation(self):
        for index in range(2):self.queue.request_source_audit(self.event.id,'prior-finite-attempt-'+str(index))
        self.worker.run_once()
        result=MemoryService(self.store,self.engine).query(self.spec.query,scope='lab')
        self.assertEqual(result['completeness'],'incomplete')
        recovery=result['source_status']['interpretation_recovery']
        self.assertEqual(recovery['admissions'][0]['status'],'cap_exhausted')
        self.assertFalse(recovery['admissions'][0]['scheduled'])
        self.assertEqual(len(self.requests()),2)


    def alias_failure_case(self,failure):
        self.declare=False;old=self.query();old_check=self.engine.inspect_view('origin')['current_check_id']
        question='What reason did the route proposer state about the amber route?'
        base=self.provider;declared=[]
        def provider(request):
            if request.task=='Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id':'origin','related_view_ids':[],'subquestions':[],
                    'reason':'Equivalent qualified candidate.'})
            fixed=any(r.get('role')=='fixed_answer_applicability' for r in request.records)
            repair=any(r.get('role')=='review_contract_feedback' for r in request.records)
            self.declare=fixed
            if fixed and declared and not repair and failure=='later_review':
                raise SemanticUnavailable('injected later independent semantic rejection')
            if fixed and repair:
                if failure=='provider':raise ModelError('injected later provider failure')
                if failure=='timeout':raise TimeoutError('injected later transport timeout')
                if failure=='format':return 'not valid review JSON'
            value=base(request)
            if fixed and request.task.startswith('Review grounded'):
                parsed=json.loads(value)
                if any('source_omission' in n for n in parsed.get('unknown_needs',[])):
                    if repair:parsed['unknown_needs'][0].pop('source_omission')
                    else:
                        declared.append(True)
                        if failure=='later_review':
                            parsed.update(status='revise',coverage='partial',context_requests=[
                                {'operation':'search','gap_id':'related-reason','query':'route proposer reason','limit':1}])
                        else:parsed['coverage']='invalid-coverage'
                    return json.dumps(parsed)
            return value
        self.engine.model=provider;self.worker.model=provider
        result=MemoryService(self.store,self.engine).query(question,scope='lab')
        self.assertEqual(len(declared),1)
        self.assertEqual(len(self.requests()),1,'A validated declaration survives every later fixed-review failure.')
        origin=json.loads(self.requests()[0]['evidence_json'])['origins'][0]
        self.assertNotEqual(origin['view_id'],'origin');self.assertEqual(origin['question'],question)
        self.assertEqual(result['completeness'],'incomplete')
        self.assertEqual(self.engine.inspect_view('origin')['current_id'],old.id)
        self.assertEqual(self.engine.inspect_view('origin')['current_check_id'],old_check)
        self.recover=True
        with patch.object(self.engine,'_impact_selection',return_value=([],{})):
            for _ in range(16):
                if self.worker.run_once() is None:break
        refreshed=self.engine.get_version(self.engine.inspect_view(origin['view_id'])['current_id'])
        self.assertIn('avoids the narrow passage',refreshed.summary)
        self.assertEqual(len(self.requests()),1)

    def test_alias_witness_survives_structural_repair_dropping_declaration(self):
        self.alias_failure_case('drop')

    def test_alias_witness_survives_later_provider_failure(self):
        self.operational_alias_case(ModelError)

    def test_alias_witness_survives_later_invalid_json(self):
        self.alias_failure_case('format')

    def test_alias_witness_survives_later_transport_timeout(self):
        self.operational_alias_case(TimeoutError)

    def test_normal_review_retains_validated_witness_across_transport_failure(self):
        base=self.provider
        def provider(request):
            if any(r.get('role')=='review_contract_feedback' for r in request.records):
                raise TimeoutError('injected transport loss after validated omission')
            value=base(request)
            if request.task.startswith('Review grounded'):
                parsed=json.loads(value)
                if any('source_omission' in n for n in parsed.get('unknown_needs',[])):
                    parsed['coverage']='invalid-coverage'
                return json.dumps(parsed)
            return value
        self.engine.model=provider
        with self.assertRaisesRegex(ModelError,'transport loss'):self.query()
        self.assertEqual(len(self.requests()),1)
        self.assertTrue(self.engine.view_checks('origin')[-1]['overview_review']['source_omissions'])


    def terminal_coverage_case(self,grounded,malformed=False):
        from dataclasses import replace
        from prototypes.assertion_memory.answer_source_gaps import semantic_terminal
        if grounded:self.query()
        else:self.queue.request_source_audit(self.event.id,'legacy-terminal-control')
        base=self.provider
        def provider(request):
            if request.task!='Review source material coverage':return base(request)
            gaps=[r for r in request.records if r.get('role')=='source_omission_hypothesis']
            coverage={'status':'unresolved','reason':'The additional attributed meaning remains ambiguous.','omissions':[]}
            if gaps:
                coverage['gap_dispositions']=[{'request_id':r['request_id'],'disposition':'unresolved',
                    'reason':'The exact original leaves this attributed facet ambiguous.'} for r in gaps]
            if malformed:coverage['status']='unsupported-new-status'
            return json.dumps({'source_coverage':coverage})
        self.worker.model=provider;self.worker.run_once()
        result=self.queue.extraction_history(self.event.id)[-1]
        if grounded:
            original=self.engine._spec
            with patch.object(self.engine,'_spec',side_effect=lambda key:replace(self.spec,query='Changed purpose') if key=='origin' else original(key)):
                self.worker.run_once()
        self.assertEqual(self.requests()[0]['status'],'scheduled' if malformed else 'completed')
        if malformed:
            self.assertGreater(self.worker.seconds_until_work(with_model=True),0)
            self.assertEqual(self.queue.get(self.event.id).status,'pending')
        else:self.assertIsNone(self.worker.seconds_until_work(with_model=True))
        self.assertEqual(semantic_terminal(result),not malformed)
        service=MemoryService(self.store,self.engine)
        self.assertEqual(service._query_processing_gaps('lab',None,{},freshness_only=True),malformed)
        self.assertTrue(service._query_processing_gaps('lab',None,{}),'Semantic ambiguity remains a source limitation.')
        if not malformed:
            receipts=[r for r in result['context_recovery'] if r.get('operation')=='source_coverage']
            self.assertEqual(len(receipts),1)
            self.assertEqual(receipts[0]['attempt'],0);self.assertEqual(receipts[0]['omissions'],[])
        return result

    def test_grounded_parsed_unresolved_coverage_remains_terminal_after_request_finishes(self):
        self.terminal_coverage_case(True)

    def test_legacy_parsed_unresolved_coverage_keeps_existing_terminal_semantics(self):
        self.terminal_coverage_case(False)

    def test_malformed_coverage_status_remains_operationally_nonterminal(self):
        self.terminal_coverage_case(True,malformed=True)


    def delayed_origin_refresh(self):
        self.query();self.worker.run_once()
        with patch.object(self.engine,'build_view',side_effect=ModelError('temporary origin refresh failure')):
            self.worker.run_once()
        self.assertEqual(self.requests()[0]['status'],'refresh')
        self.assertEqual(self.queue.get(self.event.id).status,'pending')

    def test_origin_refresh_retry_readiness_respects_positive_backoff(self):
        self.delayed_origin_refresh()
        self.assertEqual(self.worker.seconds_until_work(with_model=True),2)
        self.assertIsNone(self.worker.run_once())
        self.assertEqual(MemoryService(self.store,self.engine).status(scope='lab')['source_audit_request_counts'],{'refresh':1})

    def test_pending_intent_on_live_audit_lease_uses_lease_deadline(self):
        self.queue.request_source_audit(self.event.id,'existing-audit')
        self.queue._maintenance_tick();claim=self.queue._claim(with_model=True,maintenance_only=True)
        self.assertIsNotNone(claim)
        self.query()
        self.assertTrue(any(row['status']=='pending' for row in self.requests()))
        self.assertEqual(self.worker.seconds_until_work(with_model=True),self.queue._lease_seconds)
        self.assertIsNone(self.worker.run_once())

    def test_other_ready_source_still_wakes_worker_during_refresh_backoff(self):
        self.delayed_origin_refresh()
        self.queue.capture_event('fiction','new-turn','A new attributed report.',actor='Mira',scope='lab',source='fixture://ready')
        self.assertEqual(self.worker.seconds_until_work(with_model=True),0)
        self.assertEqual(self.worker.seconds_until_work(with_model=False),2)

    def test_unscheduled_source_intent_requires_model_for_immediate_readiness(self):
        self.query()
        self.assertEqual(self.worker.seconds_until_work(with_model=True),0)
        self.assertIsNone(self.worker.seconds_until_work(with_model=False))

    def test_drain_sleeps_through_recovery_backoff_instead_of_busy_polling(self):
        from datetime import timedelta
        self.delayed_origin_refresh();run=self.worker.run_once;calls=[];sleeps=[]
        def bounded_run():
            calls.append(True)
            if len(calls)>3:raise AssertionError('Recovery retry busy-polled instead of sleeping.')
            return run()
        def advance(seconds):
            sleeps.append(seconds);self.now+=timedelta(seconds=seconds)
        with patch.object(self.worker,'run_once',side_effect=bounded_run),patch('prototypes.assertion_memory.worker.sleep',side_effect=advance):
            results=self.worker.drain(limit=1,wait_for_retries=True)
        self.assertEqual(len(results),1)
        self.assertEqual(sleeps,[1.0,1.0])
        self.assertEqual(len(calls),3)


    def test_alias_witness_survives_a_later_independent_review_round_failure(self):
        self.alias_failure_case('later_review')

    def test_alias_failure_without_actual_original_delivery_cannot_schedule_audit(self):
        self.declare=False;self.query();base=self.provider
        def provider(request):
            if request.task=='Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id':'origin','related_view_ids':[],'subquestions':[],
                    'reason':'Equivalent qualified candidate.'})
            fixed=any(r.get('role')=='fixed_answer_applicability' for r in request.records)
            if fixed and any(r.get('role')=='review_contract_feedback' for r in request.records):
                raise ModelError('injected later failure after forged declaration')
            value=base(request)
            if fixed and request.task.startswith('Review grounded'):
                parsed=json.loads(value)
                parsed['unknown_needs']=[{'basis':'source_specific_question','gap_id':'unread-original',
                    'quote':'The proposer reason awaits accepted interpretation.','reason':'Unverified response claim.',
                    'source_ids':['choice'],'include_source_evidence':True,
                    'question_quote':'reason','source_omission':{'canonical_id':'choice','proof_id':'forged-delivery',
                        'start':0,'end':len(self.body)}}]
                return json.dumps(parsed)
            return value
        self.engine.model=provider
        with self.assertRaisesRegex(ModelError,'injected later failure after forged declaration'):
            MemoryService(self.store,self.engine).query('What reason did the route proposer state about the amber route?',scope='lab')
        self.assertEqual(self.requests(),[])
        self.assertFalse(any(check['overview_review'].get('source_omissions') for check in self.engine.view_checks('origin')))


    def operational_alias_case(self,failure_type,*,delivered=True):
        self.declare=False;old=self.query();old_check=self.engine.inspect_view('origin')['current_check_id']
        question='What reason did the route proposer state about the amber route?'
        base=self.provider;observed=[];later=[];failed=[]
        original=failure_type('injected operational failure after valid omission')
        def provider(request):
            if failed:later.append(request.task)
            if request.task=='Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id':'origin','related_view_ids':[],'subquestions':[],
                    'reason':'Equivalent qualified candidate.'})
            fixed=any(r.get('role')=='fixed_answer_applicability' for r in request.records)
            repair=any(r.get('role')=='review_contract_feedback' for r in request.records)
            self.declare=fixed and delivered
            if fixed and (repair or not delivered):
                failed.append(True);raise original
            value=base(request)
            if fixed and request.task.startswith('Review grounded'):
                parsed=json.loads(value)
                if any('source_omission' in n for n in parsed.get('unknown_needs',[])):
                    observed.append(True);parsed['coverage']='invalid-coverage'
                return json.dumps(parsed)
            return value
        self.engine.model=provider;self.worker.model=provider
        with self.assertRaises(failure_type) as escaped:
            MemoryService(self.store,self.engine).query(question,scope='lab')
        self.assertIs(escaped.exception,original)
        self.assertEqual(later,[],'Operational failure cannot authorize another foreground model call.')
        self.assertEqual(len(observed),int(delivered));self.assertEqual(len(self.requests()),int(delivered))
        self.assertEqual(self.engine.inspect_view('origin')['current_id'],old.id)
        self.assertEqual(self.engine.inspect_view('origin')['current_check_id'],old_check)
        self.assertEqual(self.queue.get_event(self.event.id).prompt,self.body)
        self.assertEqual(json.dumps(self.queue.extraction_history(self.event.id),sort_keys=True),self.old_history)
        if not delivered:return
        evidence=json.loads(self.requests()[0]['evidence_json']);origin=evidence['origins'][0]
        self.assertNotEqual(origin['view_id'],'origin');self.assertEqual(origin['question'],question)
        check=self.engine.view_checks(origin['view_id'])[-1]
        self.assertEqual(check['overview_review']['status'],'unresolved')
        self.assertEqual(check['overview_review']['generation_error'],str(original))
        self.assertEqual(check['overview_review']['failure_type'],failure_type.__name__)
        self.assertEqual(check['overview_review']['failure_classification'],'operational')
        self.assertIsNone(self.engine.inspect_view(origin['view_id'])['current_id'])
        self.recover=True
        with patch.object(self.engine,'_impact_selection',return_value=([],{})):
            for _ in range(16):
                if self.worker.run_once() is None:break
        refreshed=self.engine.get_version(self.engine.inspect_view(origin['view_id'])['current_id'])
        self.assertIn('avoids the narrow passage',refreshed.summary)
        self.assertEqual(self.requests()[0]['status'],'completed')
        self.assertEqual(self.engine.get_version(old.id).summary,old.summary)

    def test_alias_permission_failure_retains_witness_without_foreground_fallback(self):
        self.operational_alias_case(PermissionError)

    def test_alias_sqlite_failure_retains_witness_without_foreground_fallback(self):
        self.operational_alias_case(sqlite3.OperationalError)

    def test_operational_failure_without_original_delivery_never_falls_back_or_schedules(self):
        for failure_type in (TimeoutError,PermissionError,sqlite3.OperationalError,ModelError):
            with self.subTest(failure=failure_type.__name__):self.operational_alias_case(failure_type,delivered=False)

    def cached_operational_case(self,failure_type):
        from prototypes.assertion_memory import question_routes
        self.declare=False;self.query();base=self.provider
        question='What reason did the route proposer state about the amber route?'
        original=failure_type('injected operational failure before retarget commit')
        def provider(request):
            if request.task=='Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id':'origin','related_view_ids':[],'subquestions':[],
                    'reason':'Equivalent qualified candidate.'})
            fixed=any(r.get('role')=='fixed_answer_applicability' for r in request.records)
            self.declare=fixed
            if fixed and any(r.get('role')=='review_contract_feedback' for r in request.records):raise original
            value=base(request)
            if fixed and request.task.startswith('Review grounded'):
                parsed=json.loads(value)
                if any('source_omission' in n for n in parsed.get('unknown_needs',[])):parsed['coverage']='invalid-coverage'
                return json.dumps(parsed)
            return value
        self.engine.model=provider
        with patch.object(question_routes,'materialize_after_reuse_rejection',side_effect=ModelError('injected controller commit loss')):
            with self.assertRaises(failure_type) as escaped:MemoryService(self.store,self.engine).query(question,scope='lab')
        self.assertIs(escaped.exception,original);self.assertEqual(self.requests(),[])
        self.engine.model=lambda request:self.fail('Cached operational failure must not invoke a model.')
        with self.assertRaisesRegex(failure_type,'injected operational failure before retarget commit'):
            MemoryService(self.store,self.engine).query(question,scope='lab')
        self.assertEqual(len(self.requests()),1)
        origin=json.loads(self.requests()[0]['evidence_json'])['origins'][0]
        self.assertEqual(origin['question'],question)
        self.assertEqual(self.engine.view_checks(origin['view_id'])[-1]['overview_review']['failure_classification'],'operational')


    def test_cached_operational_witness_restores_error_without_model_calls(self):
        self.cached_operational_case(TimeoutError)

    def test_cached_permission_witness_restores_error_without_model_calls(self):
        self.cached_operational_case(PermissionError)

    def test_cached_sqlite_witness_restores_error_without_model_calls(self):
        self.cached_operational_case(sqlite3.OperationalError)

    def test_cached_provider_model_error_remains_operational_without_model_calls(self):
        self.cached_operational_case(ModelError)

    def test_cooperative_refresh_yield_is_not_consumed_as_a_semantic_rejection(self):
        from prototypes.assertion_memory.worker import _RefreshYield
        self.declare=False;self.query();base=self.provider;original=_RefreshYield();later=[];failed=[]
        def provider(request):
            if failed:later.append(request.task)
            if request.task=='Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id':'origin','related_view_ids':[],'subquestions':[],
                    'reason':'Equivalent qualified candidate.'})
            fixed=any(r.get('role')=='fixed_answer_applicability' for r in request.records)
            self.declare=fixed
            if fixed and any(r.get('role')=='review_contract_feedback' for r in request.records):
                failed.append(True);raise original
            value=base(request)
            if fixed and request.task.startswith('Review grounded'):
                parsed=json.loads(value)
                if any('source_omission' in n for n in parsed.get('unknown_needs',[])):parsed['coverage']='invalid-coverage'
                return json.dumps(parsed)
            return value
        self.engine.model=provider
        with self.assertRaises(_RefreshYield) as escaped:
            MemoryService(self.store,self.engine).query('What reason did the route proposer state about the amber route?',scope='lab')
        self.assertIs(escaped.exception,original);self.assertEqual(later,[]);self.assertEqual(self.requests(),[])



if __name__=='__main__': unittest.main()
