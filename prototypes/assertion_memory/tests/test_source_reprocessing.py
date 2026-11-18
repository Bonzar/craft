"""Invented sources and deterministic reviewers exercise lifecycle, not LLM quality."""
import json
import unittest
from dataclasses import asdict
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory.query_snapshot import capture, _restore_frozen


class SourceReprocessingTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)
        self.engine = MemoryEngine(self.store)
        self.calls = []

    def event(self, text, turn='one', **kwargs):
        return self.queue.capture_event('invented',turn,text,source='fixture://'+turn,
            actor=kwargs.pop('actor','Mira'),scope='lab',**kwargs)

    def seed(self, event, text, key='old', quote=None):
        quote = quote or event.prompt
        start = event.prompt.index(quote)
        self.store.append_assertion(assertion_id=key,text=text,actor=event.actor,
            source=event.source,episode=event.session_id,scope=event.scope,recorded_at=event.recorded_at,
            metadata={'excerpt':quote,'source_span':{'start':start,'end':start+len(quote)},
                'evidence_refs':[{'event_id':event.id,'start':start,'end':start+len(quote)}]})
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (event.id,key,self.queue._time(),json.dumps({'assertion_ids':[key]})))
            db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done',extracted_ids=? WHERE event_id=?",
                (json.dumps([key]),event.id))
        return key

    def provider(self, request):
        self.calls.append(request)
        if request.task == 'Review source material coverage':
            meanings=[row['text'] for row in request.records if row.get('role')=='interpretation']
            missing=self.repair is not None and self.repair['text'] not in meanings
            return json.dumps({'source_coverage':{'status':'incomplete' if missing else 'accepted',
                'reason':'The exact source meaning is checked.', 'omissions':[
                    {'quote':self.repair['quote'],'reason':'The sourced immediate sequence is missing.'}] if missing else []}})
        if request.task=='Repair omitted source meanings':
            return json.dumps({'assertions':[self.repair]})
        if request.task=='Verify source interpretation withdrawals':
            return json.dumps({'withdrawals':[{'assertion_id':row['assertion_id'],'status':'accepted',
                'reason':'Raw target positively disproves this attributed choice.',
                'evidence':[{'event_id':self.target.id,'quote':self.target.prompt}]}
                for row in request.records if row.get('role')=='withdrawal_hypothesis']})
        if request.task.startswith('Verify'):
            return json.dumps({'verdicts':[{'index':row['index'],
                'status':'unsupported' if row['text']=='Noor chose blue.' else 'accepted',
                'reason':'The exact supplied raw source was independently checked.'}
                for row in request.records if row.get('role')=='interpretation']})
        if request.task.startswith('Extract'):
            return json.dumps({'assertions':[]})
        raise AssertionError(request.task)

    def tick(self, limit=12):
        worker=LinkWorker(self.queue,self.engine,self.provider)
        for _ in range(limit):
            if worker.run_once() is None: break

    def keys(self): return set(capture(self.engine).assertions)

    def test_protocol_audit_repairs_missing_sequence_without_regenerating_true_prior(self):
        self.target=self.event('The inspection ended at 14:00. The cleanup began immediately afterward and lasted twenty minutes.')
        self.seed(self.target,'Cleanup lasted twenty minutes.')
        self.repair={'text':'Cleanup began immediately after the inspection ended.', 'quote':self.target.prompt}
        self.tick()
        self.assertEqual(len(self.keys()),2)
        self.assertIn('old',self.keys())
        self.assertTrue(any(self.store.get_assertion(key).text==self.repair['text'] for key in self.keys()))
        self.assertFalse(any(call.task.startswith('Extract') for call in self.calls))
        with self.store.transaction() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM memory_derived_work WHERE scope='lab'").fetchone())
        count=len(self.calls); self.tick(); self.assertEqual(len(self.calls),count)

    def test_adequate_legacy_is_attested_without_duplicate_or_extraction(self):
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.')
        self.repair=None; self.tick()
        self.assertEqual(self.keys(),{'old'})
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'accepted_complete')
        self.assertFalse(any(call.task.startswith(('Extract','Repair')) for call in self.calls))

    def test_frozen_input_detects_same_position_origin_confirmation_but_not_future_append(self):
        hidden=self.event('Use the green alternative.',turn='earlier',position=1,actor='assistant',await_origin=True)
        target=self.event('Yes.',turn='reply',position=2)
        before=self.queue.freeze_input(target.id)
        self.event('Unrelated later fact.',turn='future',position=3)
        self.assertEqual(before.fingerprint,self.queue.freeze_input(target.id).fingerprint)
        self.event(hidden.prompt,turn='earlier',position=1,actor='assistant')
        self.assertNotEqual(before.fingerprint,self.queue.freeze_input(target.id).fingerprint)
        self.assertFalse(any(row.prompt==hidden.prompt for row in before.events))

    def test_running_extraction_is_fenced_without_stealing_lease(self):
        hidden=self.event('Use green.',turn='earlier',position=1,actor='assistant',await_origin=True)
        target=self.event('Yes.',turn='reply',position=2)
        claim=self.queue._claim(with_model=True)
        frozen=self.queue.freeze_input(target.id)
        self.event(hidden.prompt,turn='earlier',position=1,actor='assistant')
        self.queue.renew_claim(claim)
        result={'assertions':[],'reviews':[],'context_event_ids':[target.id], 'context_truncated':False,
            'context_recovery':[{'operation':'source_coverage','status':'accepted','reason':'Checked.'}],
            'input_fingerprint':frozen.fingerprint}
        self.queue._finish_extraction(claim,target,result)
        self.assertEqual(self.queue.extraction_history(target.id),[])
        self.assertEqual(self.queue.get(target.id).phase,'extract')
        self.assertNotEqual(self.queue.get(target.id).status,'completed')

    def test_new_capture_empty_coverage_publishes_and_dirties_without_assertion_insert(self):
        self.target=self.event('What shape is the lamp?'); self.repair=None
        before=self.store.version; self.tick()
        self.assertEqual(self.keys(),set()); self.assertGreater(self.store.version,before)
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'accepted_complete')

    def test_failed_audit_preserves_prior_and_does_not_retry_every_tick(self):
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.')
        self.repair=None
        def failed(request): self.calls.append(request); raise TimeoutError('fixture timeout')
        worker=LinkWorker(self.queue,self.engine,failed)
        for _ in range(20): worker.run_once()
        self.assertEqual(self.keys(),{'old'}); self.assertEqual(len(self.calls),1)
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'failed')

    def test_partial_coverage_does_not_erase_valid_prior(self):
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.')
        self.repair=None
        def partial(request):
            if request.task=='Review source material coverage': return '{}'
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,partial)
        for _ in range(12): worker.run_once()
        self.assertEqual(self.keys(),{'old'})
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'unresolved')

    def test_positive_single_withdrawal_survives_incomplete_repair_and_keeps_other_fact(self):
        self.target=self.event('The assistant suggested blue. Noor said no and selected green. The lamp is round.')
        self.seed(self.target,'Noor chose blue.','wrong'); self.seed(self.target,'The lamp is round.','valid')
        self.repair=None
        def unresolved(request):
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'Other meaning remains unresolved.','omissions':[]}})
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,unresolved)
        before=capture(self.engine)
        for _ in range(12): worker.run_once()
        self.assertEqual(self.keys(),{'valid'})
        self.assertEqual(asdict(_restore_frozen(self.engine,before.descriptor())),asdict(before))
        self.assertIsNotNone(self.store.get_assertion('wrong'))

    def test_malformed_withdrawal_proof_never_removes_prior(self):
        self.target=self.event('The assistant suggested blue. Noor selected green.')
        self.seed(self.target,'Noor chose blue.','wrong'); self.repair=None
        def malformed(request):
            if request.task=='Verify source interpretation withdrawals':
                return json.dumps({'withdrawals':[{'assertion_id':'wrong','status':'accepted',
                    'reason':'Incorrect', 'evidence':[{'event_id':self.target.id,'quote':'Invented source.'}]}]})
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,malformed)
        for _ in range(12): worker.run_once()
        self.assertEqual(self.keys(),{'wrong'})

    def test_same_words_changed_kind_or_proof_cannot_reuse_immutable_payload(self):
        self.target=self.event('The lamp is round.'); self.repair=None
        claim=self.queue._claim(with_model=True); frozen=self.queue.freeze_input(self.target.id)
        from prototypes.assertion_memory.dialogue import extract_dialogue
        def generated(request):
            if request.task.startswith('Extract'): return json.dumps({'assertions':[{'text':self.target.prompt,'quote':self.target.prompt,'kind':'observation'}]})
            return self.provider(request)
        result=extract_dialogue(generated,self.target,list(frozen.events)); result['input_fingerprint']=frozen.fingerprint
        self.queue._finish_extraction(claim,self.target,result)
        first=next(iter(self.keys()))
        result['assertions'][0]['kind']='assertion'
        with self.store.transaction(write=True) as db:
            second=self.queue._publish_interpretation(db,self.target,result['assertions'][0],result,frozen.order,self.queue._time())
        self.assertNotEqual(second,first)
        self.assertEqual(self.store.get_assertion(first).metadata['kind'],'observation')

    def test_partial_first_capture_publishes_independently_verified_truth_with_gap(self):
        self.target=self.event('The lamp is round. Other details were not recorded.'); self.repair=None
        def partial(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions':[{'text':'The lamp is round.','quote':'The lamp is round.'}]})
            if request.task=='Review source material coverage': return '{}'
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,partial)
        for _ in range(4): worker.run_once()
        self.assertEqual([self.store.get_assertion(key).text for key in self.keys()],['The lamp is round.'])
        run=self.queue.extraction_history(self.target.id)[-1]['source_revision']
        self.assertEqual(run['status'],'unresolved')
        self.assertEqual(set(run['assertion_ids']),self.keys())

    def test_same_position_origin_confirmation_reaudits_accepted_nonempty_prior(self):
        hidden=self.event('The green alternative is the intended one.',turn='hidden',position=1,
            actor='assistant',await_origin=True)
        self.target=self.event('Yes, that option.',turn='reply',position=2)
        self.seed(self.target,'Mira affirmed an option.'); self.repair=None
        self.tick(); before=len(self.calls)
        before_input=self.queue.freeze_input(self.target.id).fingerprint
        self.event(hidden.prompt,turn='hidden',position=1,actor='assistant')
        self.tick()
        self.assertGreater(len(self.calls),before)
        self.assertEqual(self.keys(),{'old'})
        runs=self.queue.extraction_history(self.target.id)
        # No extra package-triggered historical audit; only the real origin change.
        self.assertEqual(len(runs),2)
        self.assertNotEqual(runs[-1]['source_revision']['input_fingerprint'],before_input)
        before=len(runs); self.event('A future note.',turn='future',position=3,actor='assistant')
        self.tick(); self.assertEqual(len(self.queue.extraction_history(self.target.id)),before)

    def test_running_audit_completion_is_fenced_on_origin_confirmation(self):
        hidden=self.event('The green alternative.',turn='hidden',position=1,actor='assistant',await_origin=True)
        self.target=self.event('Yes.',turn='reply',position=2)
        self.seed(self.target,'Mira affirmed an option.'); self.repair=None
        def race(request):
            if len(self.calls)==0:
                self.event(hidden.prompt,turn='hidden',position=1,actor='assistant')
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,race)
        worker.run_once()
        self.assertEqual(len(self.queue.extraction_history(self.target.id)),1)
        self.assertEqual(self.keys(),{'old'})
        self.assertEqual(self.queue.get(self.target.id).phase,'audit')
        self.tick(); self.assertEqual(len(self.queue.extraction_history(self.target.id)),2)

    def test_positive_withdrawal_later_complete_revision_installs_green(self):
        hidden=self.event('Speaker identity: Noor.',turn='hidden',position=1,actor='assistant',await_origin=True)
        self.target=self.event('The assistant suggested blue. Noor selected green. The lamp is round.',position=2)
        self.seed(self.target,'Noor chose blue.','wrong'); self.seed(self.target,'The lamp is round.','valid')
        self.repair=None
        def unresolved(request):
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'Need more context.','omissions':[]}})
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,unresolved)
        for _ in range(3): worker.run_once()
        self.assertEqual(self.keys(),{'valid'})
        self.event(hidden.prompt,turn='hidden',position=1,actor='assistant')
        self.repair={'text':'Noor selected green.','quote':'Noor selected green.'}
        self.tick()
        self.assertNotIn('wrong',self.keys()); self.assertIn('valid',self.keys())
        self.assertEqual({self.store.get_assertion(key).text for key in self.keys()},
            {'The lamp is round.','Noor selected green.'})
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'accepted_complete')
        self.assertFalse(any(edge.kind=='supersedes' for edge in self.store.list_edges()))

    def test_audit_maintenance_has_finite_opportunities_during_continuous_intake(self):
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.'); self.repair=None
        worker=LinkWorker(self.queue,self.engine,self.provider,source_first=True,source_batch_limit=2)
        for index in range(6):
            self.event('What else?',turn='stream-'+str(index))
            worker.run_once()
        self.assertTrue(any('source_revision' in row for row in self.queue.extraction_history(self.target.id)))

    def test_automatic_repair_reaches_background_root_without_query_or_build(self):
        from prototypes.assertion_memory.tests.automatic_overview_provider import respond
        self.target=self.event('The inspection ended. Cleanup began immediately afterward and lasted twenty minutes.')
        self.seed(self.target,'Cleanup lasted twenty minutes.'); self.repair={
            'text':'Cleanup began immediately after the inspection ended.','quote':self.target.prompt}
        def combined(request):
            if request.task in ('Review source material coverage','Repair omitted source meanings') or request.task.startswith('Verify contextual'):
                return self.provider(request)
            self.calls.append(request)
            if request.task == 'Discover reusable memory themes':
                return json.dumps({'themes': [{'question': 'What cleanup chronology is reported?',
                    'source_ids': [r['id'] for r in request.records if r.get('role') == 'source']}],
                    'unassigned_source_ids': []})
            result=respond({'task':request.task,'records':request.records})
            return result if isinstance(result,str) else json.dumps(result)
        self.engine=MemoryEngine(self.store,model=combined,verify_overviews=True)
        worker=LinkWorker(self.queue,self.engine,combined,source_first=True)
        for _ in range(40):
            if worker.run_once() is None: break
        with self.store.transaction() as db:
            row=db.execute("SELECT result FROM memory_derived_work WHERE scope='lab'").fetchone()
            self.assertIsNotNone(row)
            result=json.loads(row['result'])
        root=self.engine.get_version(result['version_id'])
        self.assertEqual(root.overview_review['assembly_coverage'], 'navigation_index')
        self.assertIn(self.repair['text'], '\n'.join(self.engine.get_version(key).summary for key in root.upstream.values()))
        self.assertEqual({row['id'] for row in root.evidence},self.keys())
        for call in self.calls:
            if call.task.startswith(('Write','Compose','Build')):
                self.assertFalse(any(row.get('id')==self.target.id for row in call.records))

    def test_pending_context_marker_is_visible_before_cursor_scan_and_respects_time(self):
        from prototypes.assertion_memory.service import MemoryService
        from datetime import datetime, timezone
        now=[datetime(2026,1,1,tzinfo=timezone.utc)]
        self.queue._clock=lambda:now[0]
        hidden=self.event('The intended alternative is green.',turn='hidden',position=1,
            actor='assistant',await_origin=True,recorded_at='2026-01-01')
        self.target=self.event('Yes.',turn='reply',position=2,recorded_at='2026-01-01')
        self.seed(self.target,'Mira affirmed an option.'); self.repair=None; self.tick()
        service=MemoryService(self.store,self.engine)
        now[0]=datetime(2026,1,3,tzinfo=timezone.utc)
        self.event(hidden.prompt,turn='hidden',position=1,actor='assistant',recorded_at='2026-01-01')
        self.assertTrue(service._query_processing_gaps('lab',None,{}))
        response=service.query('What option did Mira affirm?',scope='lab')
        self.assertTrue(response['refresh_required'])
        self.assertEqual(response['completeness'],'incomplete')
        self.assertTrue(response['source_status']['processing_gaps_apply_to_query'])
        self.assertFalse(service._query_processing_gaps('lab','2026-01-02',{}))
        self.assertFalse(service._query_processing_gaps('elsewhere',None,{}))
        self.queue._maintenance_tick()
        self.assertFalse(service._query_processing_gaps('lab','2026-01-02',{}),
            'worker scheduling must not retroactively mark earlier knowledge as pending')
        self.tick(); self.assertFalse(service._query_processing_gaps('lab',None,{}))
        now[0]=datetime(2026,1,4,tzinfo=timezone.utc)
        self.event('A future assistant note.',turn='future',position=3,actor='assistant')
        self.assertFalse(service._query_processing_gaps('lab',None,{}))

    def test_protocol_cursor_is_bounded_and_does_not_read_raw_bodies_or_assertions(self):
        import sqlite3
        for index in range(40):
            event=self.event('Invented lamp '+str(index),turn='legacy-'+str(index))
            self.seed(event,event.prompt,'legacy-'+str(index))
        # Isolate the protocol cursor; context marker scans have their own page.
        with self.store.transaction(write=True) as db:
            db.execute('DELETE FROM memory_source_context_changes')
        def authorizer(operation,table,column,*_):
            if operation==sqlite3.SQLITE_READ and (table=='memory_assertions' or
                    (table=='memory_raw_events' and column=='prompt')):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store._connection.set_authorizer(authorizer)
        try: self.queue._maintenance_tick()
        finally: self.store._connection.set_authorizer(None)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_source_audits').fetchone()[0],16)

    def test_missing_authenticated_positions_are_explicit_unavailable_context(self):
        first=self.event('An older alternative.',turn='older',position=1,actor='assistant')
        self.target=self.event('Yes.',turn='reply',position=3)
        frozen=self.queue.freeze_input(self.target.id)
        self.repair=None
        def provider(request):
            if request.task.startswith('Extract'):
                gaps=[row for row in request.records if row.get('context_unavailable')]
                self.assertTrue(gaps,'unknown intervening transcript positions must remain explicit')
                target=request.records[0]
                self.assertNotEqual(target.get('previous_event_id'),first.id)
            return self.provider(request)
        LinkWorker(self.queue,self.engine,provider).run_once()
        self.assertEqual(self.queue.get(self.target.id).status,'completed')

    def test_material_gap_requests_are_async_coalesced_and_independently_audited(self):
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.')
        self.repair=None; self.tick(); before=len(self.calls)
        first=self.queue.request_source_audit(self.target.id,'invented-material-gap')
        repeated=self.queue.request_source_audit(self.target.id,'invented-material-gap')
        self.assertEqual(first,repeated)
        self.assertEqual(len(self.calls),before)
        self.tick(); self.assertGreater(len(self.calls),before)
        self.assertFalse(any('invented-material-gap' in json.dumps(call.records) for call in self.calls))
        after=len(self.calls)
        self.queue.request_source_audit(self.target.id,'invented-material-gap'); self.tick()
        self.assertEqual(len(self.calls),after)

    def test_unresolved_repair_can_add_verified_fact_without_replacing_prior(self):
        self.target=self.event('The inspection ended. Cleanup began immediately afterward and lasted twenty minutes.')
        self.seed(self.target,'Cleanup lasted twenty minutes.'); self.repair={
            'text':'Cleanup began immediately after the inspection ended.','quote':self.target.prompt}
        def partial(request):
            if request.task=='Review source material coverage' and any(
                    row.get('text')==self.repair['text'] for row in request.records):
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'Other coverage remains open.','omissions':[]}})
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,partial)
        for _ in range(6): worker.run_once()
        self.assertIn('old',self.keys()); self.assertEqual(len(self.keys()),2)
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'unresolved')

    def test_audit_fairness_survives_continuous_intake_with_live_derived_work(self):
        from prototypes.assertion_memory.tests.automatic_overview_provider import respond
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.'); self.repair=None
        def combined(request):
            if request.task.startswith(('Extract','Verify contextual')) or request.task=='Review source material coverage':
                return self.provider(request)
            result=respond({'task':request.task,'records':request.records})
            return result if isinstance(result,str) else json.dumps(result)
        self.engine=MemoryEngine(self.store,model=combined,verify_overviews=True)
        worker=LinkWorker(self.queue,self.engine,combined,source_first=True,source_batch_limit=2)
        for index in range(14):
            self.event('What other details?',turn='intake-'+str(index))
            worker.run_once()
        self.assertTrue(any('source_revision' in row for row in self.queue.extraction_history(self.target.id)),
            'derived generations and continuous new input must not starve maintenance')

    def test_distinct_gap_requests_have_a_finite_per_input_budget(self):
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.')
        self.repair=None; self.tick()
        for index in range(20):
            self.queue.request_source_audit(self.target.id,'unproved-suspicion-'+str(index))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_source_audit_requests').fetchone()[0],2)
        self.tick(); after=len(self.calls)
        for index in range(20): self.queue.request_source_audit(self.target.id,'unproved-suspicion-'+str(index))
        self.tick(); self.assertEqual(len(self.calls),after)

    def test_later_unresolved_audit_does_not_retroactively_add_historical_gap(self):
        from prototypes.assertion_memory.service import MemoryService
        from datetime import datetime, timezone
        now=[datetime(2026,1,1,tzinfo=timezone.utc)]; self.queue._clock=lambda:now[0]
        hidden=self.event('The intended option is green.',turn='hidden',position=1,actor='assistant',await_origin=True)
        self.target=self.event('Yes.',turn='reply',position=2)
        self.seed(self.target,'Mira affirmed an option.'); self.repair=None; self.tick()
        now[0]=datetime(2026,1,3,tzinfo=timezone.utc)
        self.event(hidden.prompt,turn='hidden',position=1,actor='assistant')
        def unresolved(request):
            if request.task=='Review source material coverage': return '{}'
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,unresolved)
        for _ in range(4): worker.run_once()
        service=MemoryService(self.store,self.engine)
        self.assertTrue(service._query_processing_gaps('lab',None,{}))
        self.assertFalse(service._query_processing_gaps('lab','2026-01-02',{}))
        self.assertTrue(service._query_processing_gaps('lab','2026-01-04',{}))

    def test_worker_records_actual_frozen_desired_input_before_any_model_call(self):
        self.target=self.event('The lamp is round.'); self.seed(self.target,'The lamp is round.'); self.repair=None
        fingerprint=self.queue.freeze_input(self.target.id).fingerprint
        def inspected(request):
            with self.store.transaction() as db:
                row=db.execute('SELECT desired_input,status FROM memory_source_audits WHERE event_id=?',
                    (self.target.id,)).fetchone()
                self.assertEqual(row['desired_input'],fingerprint)
                self.assertEqual(row['status'],'running')
            return self.provider(request)
        LinkWorker(self.queue,self.engine,inspected).run_once()
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'accepted_complete')

    def test_prior_attribution_is_a_checked_hypothesis_and_repair_gets_new_id(self):
        from dataclasses import replace
        self.target=self.event('The lamp is round.')
        self.seed(replace(self.target,actor='Noor'),'The lamp is round.','misattributed')
        self.repair={'text':'The lamp is round.','quote':self.target.prompt}
        def attributed(request):
            if request.task=='Verify contextual assertion interpretations' and any(
                    row.get('actor')=='Noor' for row in request.records if row.get('role')=='interpretation'):
                return json.dumps({'verdicts':[{'index':row['index'],'status':'unsupported',
                    "reason":"The immutable prior attributes Mira’s raw report to Noor."}
                    for row in request.records if row.get('role')=='interpretation']})
            if request.task=='Verify source interpretation withdrawals':
                self.assertTrue(any(row.get('actor')=='Noor' for row in request.records
                                    if row.get('role')=='withdrawal_hypothesis'))
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,attributed)
        for _ in range(8): worker.run_once()
        self.assertNotIn('misattributed',self.keys())
        self.assertEqual(len(self.keys()),1)
        repaired=self.store.get_assertion(next(iter(self.keys())))
        self.assertEqual(repaired.actor,'Mira')
        self.assertEqual(self.store.get_assertion('misattributed').actor,'Noor')

    def test_unresolved_prior_reconciliation_keeps_fact_and_exposes_current_gap(self):
        from prototypes.assertion_memory.service import MemoryService
        self.target=self.event('The assistant suggested blue. Noor selected green.')
        self.seed(self.target,'Noor chose blue.','wrong'); self.repair=None
        def ambiguous(request):
            if request.task=='Verify source interpretation withdrawals':
                return json.dumps({'withdrawals':[{'assertion_id':'wrong','status':'unresolved','reason':'Attribution remains unresolved.'}]})
            return self.provider(request)
        worker=LinkWorker(self.queue,self.engine,ambiguous)
        for _ in range(4): worker.run_once()
        self.assertEqual(self.keys(),{'wrong'})
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'unresolved')
        self.assertTrue(MemoryService(self.store,self.engine)._query_processing_gaps('lab',None,{}))

    def long_withdrawal_fixture(self, *, qualifier=False, failure=False, oversized=False):
        opening='The assistant suggested blue. The lamp is round. '
        proof='Noor rejected blue and selected green.'
        padding='The unrelated instrument log records a steady display. '* (4000 if oversized else 780)
        if qualifier:
            text=opening+proof+' '+padding+'The green selection was hypothetical; Noor actually selected blue.'
        else:
            text=opening+padding+proof
        self.target=self.event(text)
        self.seed(self.target,'Noor chose blue.','wrong',quote='The assistant suggested blue.')
        self.seed(self.target,'The lamp is round.','valid',quote='The lamp is round.')
        self.repair=None; checks=[]; failed=[]
        def provider(request):
            if request.task=='Verify source interpretation withdrawals':
                start=text.index(proof); end=start+len(proof)
                if not any(row.get('id')==self.target.id and row.get('source_span',{}).get('start',-1)<=start
                           and row.get('source_span',{}).get('end',-1)>=end for row in request.records):
                    return json.dumps({'context_requests':[{'operation':'read','event_id':self.target.id,'start':start,'end':end}]})
                return json.dumps({'withdrawals':[{'assertion_id':'wrong','status':'accepted',
                    'reason':'The actual supplied target attributes the rejected alternative to the assistant and green to Noor.',
                    'evidence':[{'event_id':self.target.id,'quote':proof,'start':start}]}]})
            if request.task=='Check source withdrawals against full-source coverage':
                chunks=[row for row in request.records if row.get('coverage_scan')]
                self.assertEqual(len(chunks),1)
                checks.append(chunks[0]['source_span'])
                hypotheses=[row for row in request.records if row.get('role')=='verified_withdrawal']
                self.assertTrue(hypotheses)
                for hypothesis in hypotheses:
                    self.assertEqual(hypothesis['verification']['evidence_refs'],
                        [{'event_id':self.target.id,'start':text.index(proof),'end':text.index(proof)+len(proof)}])
                self.assertTrue(any(row.get('id')==self.target.id and proof in row.get('text','')
                                    and not row.get('coverage_scan') for row in request.records),
                    'Every chunk check keeps the actual supplied independent target proof in its base.')
                if failure and len(checks)==2:
                    failed.append(True); raise TimeoutError('One full-source chunk is unavailable.')
                undermines='green selection was hypothetical' in chunks[0].get('text','')
                return json.dumps({'withdrawals':[{'assertion_id':row['assertion_id'],
                    'status':'unresolved' if undermines else 'accepted',
                    'reason':'This chunk qualifies the supposed rejection.' if undermines else 'This chunk does not undermine the independently established target proof.'}
                    for row in hypotheses]})
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'Other source meaning is still being audited.','omissions':[]}})
            return self.provider(request)
        before=capture(self.engine)
        old_history=json.dumps(self.queue.extraction_history(self.target.id),sort_keys=True)
        worker=LinkWorker(self.queue,self.engine,provider)
        for _ in range(4): worker.run_once()
        self.assertEqual(self.queue.get_event(self.target.id).prompt,text)
        self.assertEqual(json.dumps(self.queue.extraction_history(self.target.id)[:2],sort_keys=True),old_history)
        self.assertEqual(asdict(_restore_frozen(self.engine,before.descriptor())),asdict(before))
        self.assertIn('valid',self.keys())
        return checks,failed

    def test_long_source_positive_tail_proof_withdraws_only_after_all_coverage_chunks(self):
        checks,_=self.long_withdrawal_fixture()
        self.assertGreater(len(checks),1); self.assertLessEqual(len(checks),32)
        self.assertEqual(self.keys(),{'valid'})

    def test_hidden_later_qualifier_blocks_long_source_withdrawal(self):
        checks,_=self.long_withdrawal_fixture(qualifier=True)
        self.assertGreater(len(checks),1)
        self.assertEqual(self.keys(),{'wrong','valid'})

    def test_failure_on_any_full_source_chunk_preserves_wrong_pending_and_other_valid_fact(self):
        checks,failed=self.long_withdrawal_fixture(failure=True)
        self.assertTrue(failed); self.assertEqual(len(checks),2)
        self.assertEqual(self.keys(),{'wrong','valid'})

    def test_source_over_32_chunk_budget_remains_unresolved_without_withdrawal(self):
        checks,_=self.long_withdrawal_fixture(oversized=True)
        self.assertEqual(checks,[])
        self.assertEqual(self.keys(),{'wrong','valid'})
        self.assertEqual(self.queue.extraction_history(self.target.id)[-1]['source_revision']['status'],'failed')


if __name__=='__main__': unittest.main()
