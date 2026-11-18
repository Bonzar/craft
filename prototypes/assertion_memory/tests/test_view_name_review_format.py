"""Strict editorial review format recovery without changing semantic attempts."""
import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from prototypes.assertion_memory.model import ModelError, format_prompt
from prototypes.assertion_memory.tests.test_view_name_work import NameWorkTest
from prototypes.assertion_memory.tests.test_view_names import NamingModel, ViewNamesTest

# Exact fictional reviewer failures from the real-provider forensic sidecar.
COMBINED = '{"accepted":true,"reason":"Название сохраняет обе темы и уточнение о вечерних встречах, не утверждая конкретное расписание или правила бронирования. Ссылки для учёта не supplied.".replace?}'
LESSONS = '{"accepted":true,"reason":"Название сохраняет тему расписания по средам и выходным и причин выбора времени, не утверждая конкретное расписание или факт согласования. Ссылки не supplied.".replace}'


class ReviewFormatWorkerTest(NameWorkTest):
    # Reuse fixture helpers without rerunning the parent test class in this module.
    def checkpoint(self):
        row=self.store._connection.execute('SELECT * FROM memory_view_name_work WHERE view_id=?',(self.spec.id,)).fetchone()
        return dict(row),json.loads(row['checkpoint'])

    def scripted(self, reviews, *, revise_title=False):
        normal=NamingModel(); calls=[]; values=iter(reviews)
        def model(request):
            calls.append(request)
            if revise_title and request.task=='Name memory view' and len(calls)>1:
                normal.title='Weekday valve inspection conditions'
            output=normal(request)
            if request.task=='Review memory view name':
                value=next(values)
                if isinstance(value,Exception):raise value
                return value(request,output) if callable(value) else output if value is None else value
            return output
        self.engine.naming_model=model
        return calls

    def test_malformed_review_keeps_same_candidate_and_new_cache_key_after_restart(self):
        calls=self.scripted([COMBINED,None])
        self.enqueue();self.worker().run_once();self.worker().run_once()
        row,cp=self.checkpoint()
        self.assertEqual(('pending','reviewer',0),(row['status'],row['phase'],row['attempts']))
        saved=cp['candidate'];before=list(self.store._connection.execute('SELECT * FROM memory_refresh_generations'))
        self.worker().run_once()
        self.assertEqual(['Name memory view','Review memory view name','Review memory view name'],[r.task for r in calls])
        self.assertEqual(calls[1].records,calls[2].records)
        self.assertNotEqual(format_prompt(calls[1]),format_prompt(calls[2]))
        self.assertNotIn(COMBINED,format_prompt(calls[2]))
        self.assertEqual(saved['title'],self.engine.resolve_view_name(self.spec.id)['display_title'])
        after=list(self.store._connection.execute('SELECT * FROM memory_refresh_generations'))
        self.assertTrue(all(tuple(r) in [tuple(v) for v in after] for r in before))
        record=self.store._connection.execute('SELECT review FROM memory_view_names').fetchone()
        audit=json.loads(record[0])['format_repairs']
        self.assertEqual(hashlib.sha256(COMBINED.encode()).hexdigest(),audit[0]['response_sha256'])

    def test_bound_semantic_rejection_revises_writer_then_repairs_second_review(self):
        def reject(request,output):
            value=json.loads(output);value.update(accepted=False,reason='Missing distinguishing weekday condition.')
            return json.dumps(value)
        calls=self.scripted([reject,LESSONS,None],revise_title=True)
        self.enqueue();worker=self.worker()
        for _ in range(7):worker.run_once()
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])
        self.assertEqual(['Name memory view','Review memory view name','Name memory view','Review memory view name','Review memory view name'],[r.task for r in calls])
        self.assertIn('weekday',calls[2].records[0]['feedback'])
        row,_=self.checkpoint();self.assertEqual(1,row['attempts'])

    def test_second_contract_failure_is_terminal_without_writer_or_third_review(self):
        calls=self.scripted([COMBINED,LESSONS])
        self.enqueue();worker=self.worker()
        for _ in range(9):worker.run_once()
        row,cp=self.checkpoint()
        self.assertEqual(('unresolved','reviewer',0),(row['status'],row['phase'],row['attempts']))
        self.assertEqual('review_contract_exhausted',row['error_kind'])
        self.assertIn('candidate',cp);self.assertEqual(2,len(cp['review_audit']))
        self.assertEqual(3,len(calls));self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])

    def test_two_distinct_candidates_share_two_format_repairs_and_six_call_bound(self):
        def reject(request,output):
            value=json.loads(output);value.update(accepted=False,reason='Missing weekday condition.')
            return json.dumps(value)
        calls=self.scripted([COMBINED,reject,LESSONS,None],revise_title=True)
        self.enqueue();worker=self.worker()
        for _ in range(10):
            before=len(calls);worker.run_once();self.assertLessEqual(len(calls)-before,1)
        self.assertEqual(6,len(calls))
        self.assertEqual(2,len([r for r in calls if r.task=='Name memory view']))
        review=json.loads(self.store._connection.execute('SELECT review FROM memory_view_names').fetchone()[0])
        self.assertEqual(2,len({a['candidate_key'] for a in review['format_repairs']}))
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])

    def test_reviewer_preflight_budget_preserves_candidate_and_semantic_attempts(self):
        from prototypes.assertion_memory.view_names import _context,review_request
        self.enqueue();worker=self.worker();worker.run_once();_,cp=self.checkpoint()
        candidate={**cp['candidate'],'context':_context(self.engine,self.engine._spec(self.spec.id),
            ('fiction',),'automatic-fixture-v1')}
        normal=review_request(candidate)
        with patch('prototypes.assertion_memory.view_names.MAX_MODEL_CHARACTERS',len(format_prompt(normal))):
            worker.run_once()
        row,after=self.checkpoint()
        self.assertEqual(('unresolved','reviewer',0),(row['status'],row['phase'],row['attempts']))
        self.assertEqual('review_budget',row['error_kind']);self.assertEqual(cp,after)
        self.assertEqual(1,len(self.model.requests))

    def test_transport_outage_during_repair_preserves_diagnostic_and_candidate(self):
        calls=self.scripted([COMBINED,ModelError('temporary review transport outage'),None])
        self.enqueue();worker=self.worker();worker.run_once();worker.run_once()
        _,before=self.checkpoint();worker.run_once();row,after=self.checkpoint()
        self.assertEqual('retry',row['status']);self.assertEqual(before,after)
        self.now+=timedelta(seconds=600);self.worker().run_once()
        self.assertEqual(format_prompt(calls[2]),format_prompt(calls[3]))
        self.assertEqual(1,len([r for r in calls if r.task=='Name memory view']))
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])

    def test_provider_exception_with_decoder_wording_is_operational_not_format_failure(self):
        self.enqueue();worker=self.worker();worker.run_once();_,before=self.checkpoint()
        def unavailable(request):raise ModelError('model must return a JSON object')
        self.engine.naming_model=unavailable;worker.run_once();row,after=self.checkpoint()
        self.assertEqual(('retry','reviewer',0),(row['status'],row['phase'],row['attempts']))
        self.assertEqual(before,after);self.assertNotIn('review_formats',after)

    def test_failed_append_reuses_exact_cached_repair_and_audit(self):
        calls=self.scripted([COMBINED,None]);self.enqueue();worker=self.worker()
        worker.run_once();worker.run_once()
        with self.store.transaction(write=True) as c:
            c.execute("CREATE TRIGGER fixture_repair_crash BEFORE UPDATE ON memory_view_name_work WHEN new.status='completed' BEGIN SELECT RAISE(ABORT,'synthetic crash'); END")
        worker.run_once();self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])
        with self.store.transaction(write=True) as c:c.execute('DROP TRIGGER fixture_repair_crash')
        self.now+=timedelta(seconds=600);self.worker().run_once()
        self.assertEqual(3,len(calls));self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])

    def test_repair_lease_loser_cannot_publish_or_erase_candidate(self):
        calls=self.scripted([COMBINED,None,None]);self.enqueue();worker=self.worker()
        worker.run_once();worker.run_once();_,before=self.checkpoint()
        normal=self.engine.naming_model
        def lose(request):
            output=normal(request);self.now+=timedelta(seconds=20)
            contender=worker.names.claim(self.engine,with_model=True);worker.names.release(contender)
            return output
        self.engine.naming_model=lose;worker.run_once()
        _,after=self.checkpoint();self.assertEqual(before,after)
        self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])
        self.engine.naming_model=normal;self.worker().run_once()
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])

    def test_desired_revision_during_repair_cannot_accept_old_candidate(self):
        calls=self.scripted([COMBINED,None]);self.enqueue();worker=self.worker()
        worker.run_once();worker.run_once();normal=self.engine.naming_model
        def supersede(request):
            output=normal(request)
            worker.names.enqueue(self.engine,self.engine._spec(self.spec.id),authorized_scopes=('fiction',),operation='new-explicit-operation')
            return output
        self.engine.naming_model=supersede;worker.run_once()
        row,cp=self.checkpoint()
        self.assertEqual(('pending','writer',0),(row['status'],row['phase'],row['attempts']))
        self.assertEqual({},cp);self.assertEqual(0,self.engine.capture_presentation_snapshot()['name_watermark'])

    def legacy(self,*,candidate=False,semantic=False):
        self.enqueue();worker=self.worker()
        if candidate:worker.run_once()
        row,cp=self.checkpoint();request=json.loads(row['request']);request.pop('review_format_policy',None)
        error='naming_unresolved: independent name review rejected: Need weekday.' if semantic else 'model must return a JSON object'
        if not candidate:cp={'feedback':error}
        with self.store.transaction(write=True) as c:
            c.execute("UPDATE memory_view_name_work SET request=?,claimed_request=?,status='unresolved',attempts=2,error_kind='deterministic',error=?,checkpoint=?",(json.dumps(request),json.dumps(request),error,json.dumps(cp)))
        return worker,request,error

    def test_legacy_discarded_candidate_gets_one_explicit_new_operation_with_failure_audit(self):
        worker,previous,error=self.legacy()
        # Finish the old definition cursor; policy activation must be independent.
        worker.names.reconcile_page(self.engine)
        row,cp=self.checkpoint();request=json.loads(row['request'])
        self.assertEqual(('pending','writer',0),(row['status'],row['phase'],row['attempts']))
        self.assertNotEqual(previous['operation'],request['operation'])
        recovery=cp['recovery'];self.assertEqual('legacy_format_recovery',recovery['kind'])
        self.assertEqual(2,recovery['previous_attempts']);self.assertEqual(error,recovery['previous_error'])
        self.assertEqual('writer',recovery['previous_state_phase'])
        self.assertEqual('unknown',recovery['previous_failure_phase'])
        self.assertEqual('unavailable',recovery['candidate_provenance'])
        self.assertEqual(previous,recovery['previous_request'])
        worker.names.reconcile_page(self.engine);self.assertEqual((row,cp),self.checkpoint())
        # Repeated old producer intent cannot erase the explicit recovery lineage.
        self.enqueue();self.assertEqual((row,cp),self.checkpoint())
        worker.run_once();worker.run_once()
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])
        review=json.loads(self.store._connection.execute('SELECT review FROM memory_view_names').fetchone()[0])
        self.assertEqual(recovery,review['recovery'])

    def test_legacy_saved_candidate_resumes_reviewer_without_resetting_attempts(self):
        worker,previous,_=self.legacy(candidate=True)
        _,old=self.checkpoint();worker.names.reconcile_page(self.engine)
        row,cp=self.checkpoint()
        self.assertEqual(('pending','reviewer',2),(row['status'],row['phase'],row['attempts']))
        self.assertEqual(old['candidate'],cp['candidate'])
        self.assertEqual(previous['operation'],json.loads(row['request'])['operation'])
        worker.run_once();self.assertEqual(2,len(self.model.requests))
        self.assertEqual('accepted',self.engine.resolve_view_name(self.spec.id)['naming_status'])

    def test_legacy_upgrade_respects_live_lease_then_runs_after_cursor_finished(self):
        worker,_,_=self.legacy()
        with self.store.transaction(write=True) as c:
            c.execute("UPDATE memory_view_name_work SET status='running',lease_token='legacy-live',lease_until=?",
                (worker.names._time(10),))
        worker.names.reconcile_page(self.engine)
        row,_=self.checkpoint();self.assertEqual('legacy-live',row['lease_token'])
        self.now+=timedelta(seconds=20)
        # The definition cursor is already exhausted by the first startup scan.
        worker.names.reconcile_page(self.engine)
        row,cp=self.checkpoint();self.assertEqual('pending',row['status'])
        self.assertEqual('legacy_format_recovery',cp['recovery']['kind'])

    def test_accepted_legacy_names_remain_byte_identical_and_no_provider_is_called(self):
        self.enqueue();worker=self.worker();worker.run_once();worker.run_once()
        with self.store.transaction(write=True) as c:
            request=json.loads(c.execute('SELECT request FROM memory_view_name_work').fetchone()[0])
            request.pop('review_format_policy',None)
            c.execute("UPDATE memory_view_name_work SET request=?,claimed_request=?,status='unresolved',error='model must return a JSON object'",(json.dumps(request),json.dumps(request)))
        before=[tuple(row) for row in self.store._connection.execute('SELECT * FROM memory_view_names')]
        pipeline=self.engine.pipeline_key
        for _ in range(4):self.worker().run_once()
        self.assertEqual(before,[tuple(row) for row in self.store._connection.execute('SELECT * FROM memory_view_names')])
        self.assertEqual(pipeline,self.engine.pipeline_key);self.assertEqual(2,len(self.model.requests))

    def test_legacy_semantic_rejection_is_not_reopened(self):
        worker,_,_=self.legacy(semantic=True);before=self.checkpoint()
        worker.names.reconcile_page(self.engine);self.assertEqual(before,self.checkpoint())

    def test_repeated_same_candidate_cannot_reset_repair_allowance(self):
        def reject(request,output):
            value=json.loads(output);value.update(accepted=False,reason='Include the weekday condition.')
            return json.dumps(value)
        calls=self.scripted([COMBINED,reject])
        self.enqueue();worker=self.worker()
        for _ in range(10):worker.run_once()
        row,_=self.checkpoint()
        self.assertEqual('unresolved',row['status']);self.assertEqual(2,row['attempts'])
        self.assertEqual(4,len(calls)) # writer2 same candidate reuses already bound cached refusal


# unittest discovers inherited tests too; load_tests below selects new regressions.


class ReviewContractTest(ViewNamesTest):
    def test_invalid_schema_or_binding_retries_review_without_inferred_verdict(self):
        cases=[('accepted',1),('accepted','true'),('accepted',None),('reason',''),('reason',[]),
            ('title','Wrong conclusion'),('context_sha256','wrong'),('input_fingerprint','wrong'),
            ('reference_ids',['foreign']),('title',None)]
        for field,value in cases:
            with self.subTest(field=field,value=value):
                candidate=self.prepare();calls=[]
                def provider(request):
                    calls.append(request);output=json.loads(self.model(request))
                    if len(calls)==1:output[field]=value
                    return json.dumps(output)
                approved=self.engine.review_view_name(candidate,model=provider)
                self.assertTrue(approved['review']['accepted']);self.assertEqual(2,len(calls))
                self.assertEqual(calls[0].records,calls[1].records)

    def test_reference_order_and_all_missing_echo_fields_remain_structural(self):
        from prototypes.assertion_memory.view_names import NameReviewContractError,_digest
        candidate=self.prepare()
        # Explicit synthetic reference manifest: no database application is attempted.
        candidate['context']['reference_ids']=['fiction-one','fiction-two']
        candidate['input_fingerprint']=candidate['context_sha256']=_digest(candidate['context'])
        for field in ('title','context_sha256','input_fingerprint','reference_ids'):
            with self.subTest(missing=field):
                def provider(request):
                    verdict=json.loads(self.model(request));verdict.pop(field);return json.dumps(verdict)
                with self.assertRaises(NameReviewContractError):
                    self.engine.review_view_name(candidate,model=provider,format_retry=False)
        def reversed_refs(request):
            verdict=json.loads(self.model(request));verdict['reference_ids'].reverse();return json.dumps(verdict)
        with self.assertRaises(NameReviewContractError):
            self.engine.review_view_name(candidate,model=reversed_refs,format_retry=False)

    def test_false_with_missing_binding_is_contract_failure_before_semantic_rejection(self):
        from prototypes.assertion_memory.view_names import NameReviewContractError
        candidate=self.prepare()
        def provider(request):
            verdict=json.loads(self.model(request));verdict.update(accepted=False);verdict.pop('title')
            return json.dumps(verdict)
        with self.assertRaises(NameReviewContractError):
            self.engine.review_view_name(candidate,model=provider,format_retry=False)

    def test_raw_method_suffix_is_never_cleaned_or_accepted(self):
        from prototypes.assertion_memory.view_names import NameReviewContractError
        candidate=self.prepare();calls=[]
        def provider(request):calls.append(request);return LESSONS
        with self.assertRaises(NameReviewContractError):self.engine.review_view_name(candidate,model=provider)
        self.assertEqual(2,len(calls));self.assertEqual([],self.engine.view_checks(self.spec.id))

    def test_review_family_preflights_worst_case_serialized_repair_before_provider(self):
        from prototypes.assertion_memory.view_names import review_request
        candidate=self.prepare();normal=review_request(candidate)
        calls=[]
        def provider(request):calls.append(request);return self.model(request)
        # Original fits exactly; reserved repair envelope must still be checked first.
        with patch('prototypes.assertion_memory.view_names.MAX_MODEL_CHARACTERS',len(format_prompt(normal))):
            with self.assertRaisesRegex(ModelError,'budget'):
                self.engine.review_view_name(candidate,model=provider)
        self.assertEqual([],calls)

    def test_missing_review_reason_cannot_be_imported_as_approved_manifest(self):
        approved=self.accepted();approved['review'].pop('reason')
        with self.assertRaisesRegex(ValueError,'review'):self.append(approved)


def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (ReviewFormatWorkerTest,ReviewContractTest):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith('test_'))
    return suite
