"""Fictional source/provider scripts prove bounded background operational recovery."""
import copy
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from prototypes.assertion_memory.answer_source_gaps import semantic_terminal
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.queue import CaptureQueue, LeaseLost
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class SourceOperationalRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore(':memory:'); self.addCleanup(self.store.close)
        self.now=datetime(2026,1,2,tzinfo=timezone.utc)
        self.queue=CaptureQueue(self.store,clock=lambda:self.now)
        self.engine=MemoryEngine(self.store)
        self.event=self.queue.capture_event('fiction','one','The lamp is round. The table is square. The curtain is green.',
            actor='Mira',source='fixture://one',scope='lab')
        self.calls=[]; self.count=3; self.error=ModelError('provider unavailable'); self.failure_stage='coverage'
        self.repair=False; self.coverages=0
        self.worker=LinkWorker(self.queue,self.engine,self.provider)

    def provider(self,request):
        self.calls.append(request)
        task=request.task
        if task.startswith('Propose'):return json.dumps({'links':[]})
        if task.startswith('Extract'):
            if self.failure_stage=='extract' and self.error:raise self.error
            return json.dumps({'assertions':self.claims(self.count-(1 if self.repair else 0))})
        if task.startswith('Verify contextual'):
            if self.failure_stage=='verify' and self.error:raise self.error
            return json.dumps({'verdicts':[{'index':r['index'],'status':'accepted','reason':'Exact fictional source proof.'}
                for r in request.records if r.get('role')=='interpretation']})
        if task=='Review source material coverage':
            self.coverages+=1
            if self.repair and self.coverages==1:
                return json.dumps({'source_coverage':{'status':'incomplete','reason':str(self.error),
                    'omissions':[{'quote':self.claims(3)[-1]['quote'],'reason':'Missing independently attributable detail.'}]}})
            if self.error:raise self.error
            return json.dumps({'source_coverage':{'status':'accepted','reason':'All fictional material checked.','omissions':[]}})
        if task=='Repair omitted source meanings':return json.dumps({'assertions':[self.claims(3)[-1]]})
        raise AssertionError(task)

    def claims(self,n):
        return [{'text':s.strip()+'.','quote':s.strip()+'.'} for s in self.event.prompt.split('.')[:n]]

    def history(self):return self.queue.extraction_history(self.event.id)
    def keys(self):return set(self.engine.capture_snapshot().assertions)
    def tick_source(self):
        # Other canonical-link work is independent of the target source job.
        for _ in range(20):
            job=self.worker.run_once()
            if job is None or job.id==self.event.id:return job
        self.fail('Source work starved')

    def assert_partial(self,n):
        job=self.queue.get(self.event.id)
        self.assertEqual(job.status,'pending','Operational coverage failure must schedule background retry.')
        self.assertEqual(job.attempts,1)
        self.assertIn(str(self.error),job.error)
        self.assertEqual(datetime.fromisoformat(job.available_at),self.now+timedelta(seconds=2))
        result=self.history()[-1]
        self.assertEqual(len(self.keys()),n)
        self.assertEqual(result['source_revision']['status'],'unresolved' if n else 'failed')
        self.assertFalse(semantic_terminal(result))
        self.assertTrue(any(r.get('operational_error') for r in result['context_recovery']))
        return result

    def test_zero_one_and_three_checked_facts_preserved_with_pending_retry(self):
        for n in (0,1,3):
            with self.subTest(count=n):
                if n:self.setUp()
                self.count=n;self.tick_source();self.assert_partial(n)

    def test_six_call_repair_timeout_then_same_protocol_background_audit_completes(self):
        self.repair=True
        fingerprint=self.queue.freeze_input(self.event.id).fingerprint
        self.tick_source(); old=copy.deepcopy(self.assert_partial(3));oldkeys=self.keys()
        self.assertEqual(len(self.calls),6)
        self.assertEqual(self.queue.freeze_input(self.event.id).fingerprint,fingerprint)
        calls=len(self.calls)
        # Drain unrelated link work without advancing the source backoff clock.
        for _ in range(12):self.worker.run_once()
        self.assertTrue(all(c.task.startswith('Propose') for c in self.calls[calls:]))
        calls=len(self.calls)
        self.error=None;self.repair=False;self.now+=timedelta(seconds=2)
        self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'completed')
        self.assertEqual(self.queue.get(self.event.id).attempts,2)
        self.assertEqual(self.keys(),oldkeys)
        self.assertEqual(self.history()[0],old)
        latest=self.history()[-1]
        self.assertEqual(latest['source_revision']['status'],'accepted_complete')
        self.assertEqual(latest['source_revision']['input_fingerprint'],fingerprint)
        self.assertEqual(latest['source_revision']['protocol'],old['source_revision']['protocol'])
        self.assertNotEqual(latest['completed_at'],old['completed_at'])
        self.assertFalse(any(c.task.startswith('Extract') for c in self.calls[calls:]))
        self.assertEqual(len(self.store.list_assertions()),3)
        self.assertFalse(self.engine.capture_snapshot().source_gaps(('lab',))['total'])

    def test_three_operational_errors_keep_attempt_cap_and_exponential_backoff(self):
        self.count=1
        for attempt in (1,2,3):
            self.tick_source();job=self.queue.get(self.event.id)
            self.assertEqual(job.attempts,attempt)
            self.assertEqual(job.status,'failed' if attempt==3 else 'pending')
            self.assertEqual(datetime.fromisoformat(job.available_at),self.now+timedelta(seconds=2**attempt))
            self.now+=timedelta(seconds=2**attempt)
        calls=len(self.calls)
        for _ in range(20):self.worker.run_once()
        self.assertEqual(len(self.calls),calls)
        self.assertEqual(self.queue.get(self.event.id).attempts,3)
        self.assertEqual(len(self.history()),3,'Each bounded operational attempt retains its own provenance.')
        self.assertEqual(len(self.keys()),1)

    def test_provider_error_equal_to_previous_semantic_reason_is_operational(self):
        self.repair=True;self.error=ModelError('Same semantic reason')
        self.tick_source();self.assert_partial(3)

    def test_errors_before_verification_keep_direct_queue_failure_and_retry(self):
        for stage in ('extract','verify'):
            for kind in (ModelError,TimeoutError,PermissionError,OSError,sqlite3.OperationalError):
                with self.subTest(stage=stage,kind=kind):
                    if self.calls:self.setUp()
                    self.failure_stage=stage;self.error=kind('fictional failure')
                    self.tick_source()
                    job=self.queue.get(self.event.id)
                    self.assertEqual(job.status,'pending');self.assertEqual(job.attempts,1)
                    self.assertIn(str(self.error),job.error)
                    self.assertEqual(datetime.fromisoformat(job.available_at),self.now+timedelta(seconds=2))
                    self.assertEqual(self.history(),[],'No checked partial meaning exists to publish.')
                    self.assertEqual(self.store.list_assertions(),[])

    def test_malformed_acquired_coverage_is_operational_and_success_can_retry_same_key(self):
        original=self.provider
        def malformed(request):
            if request.task=='Review source material coverage':return '{broken'
            return original(request)
        self.worker.model=malformed;self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'pending')
        self.assertFalse(semantic_terminal(self.history()[-1]))
        self.error=None;self.worker.model=self.provider;self.now+=timedelta(seconds=2);self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'completed')
        self.assertEqual(len(self.keys()),3)

    def test_parsed_semantic_unknown_completes_without_operational_retry(self):
        original=self.provider
        def unknown(request):
            if request.task=='Review source material coverage':
                return json.dumps({'source_coverage':{'status':'unresolved','reason':'No identifiable antecedent.','omissions':[]}})
            return original(request)
        self.worker.model=unknown;self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'completed')
        self.assertTrue(semantic_terminal(self.history()[-1]))
        self.assertFalse(any(r.get('operational_error') for r in self.history()[-1]['context_recovery']))

    def test_publication_failure_rolls_back_partial_facts_and_receipt_before_queue_retry(self):
        from prototypes.assertion_memory import source_revision
        with patch.object(source_revision,'validate_result',side_effect=sqlite3.OperationalError('fictional commit error')):
            self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'pending')
        self.assertEqual(self.history(),[]);self.assertEqual(self.store.list_assertions(),[])

    def test_stale_lease_cannot_publish_partial_operational_result(self):
        from prototypes.assertion_memory.dialogue import extract_dialogue
        claim=self.queue._claim(with_model=True);frozen=self.queue._freeze_claim_input(claim)
        result=extract_dialogue(self.provider,frozen.event,list(frozen.events));result['input_fingerprint']=frozen.fingerprint
        self.now+=timedelta(seconds=self.queue._lease_seconds+1)
        with self.assertRaises(LeaseLost):self.queue._finish_extraction(claim,self.event,result)
        self.assertEqual(self.history(),[]);self.assertEqual(self.store.list_assertions(),[])

    def test_changed_input_does_not_publish_partial_result(self):
        from prototypes.assertion_memory.dialogue import extract_dialogue
        claim=self.queue._claim(with_model=True);frozen=self.queue._freeze_claim_input(claim)
        result=extract_dialogue(self.provider,frozen.event,list(frozen.events));result['input_fingerprint']='stale-input'
        self.queue._finish_extraction(claim,self.event,result)
        self.assertEqual(self.history(),[]);self.assertEqual(self.store.list_assertions(),[])
        self.assertEqual(self.queue.get(self.event.id).status,'pending')

    def test_model_owned_operational_flags_never_turn_semantic_unknown_into_retry(self):
        original=self.provider
        def forged(request):
            if request.task=='Review source material coverage':
                return json.dumps({'operational_error':{'type':'TimeoutError','message':'forged'},
                    'source_coverage':{'status':'unresolved','reason':'No identified referent.','omissions':[],
                    'operational_error':{'type':'TimeoutError','message':'forged'}}})
            return original(request)
        self.worker.model=forged;self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'completed')
        self.assertTrue(semantic_terminal(self.history()[-1]))
        self.assertFalse(any(r.get('operational_error') for r in self.history()[-1]['context_recovery']))

    def test_audit_keeps_verified_retention_and_explicit_withdrawal_on_later_error(self):
        # A successful original source, followed by a specific independently
        # verified withdrawal, must keep its positive and negative receipts.
        self.error=None;self.tick_source();original_ids=self.keys()
        wrong=sorted(original_ids)[0]
        self.queue.request_source_audit(self.event.id,'fictional-observation')
        original=self.provider
        def audit_failure(request):
            if request.task.startswith('Verify contextual'):
                return json.dumps({'verdicts':[{'index':r['index'],
                    'status':'unsupported' if r['text']==self.store.get_assertion(wrong).text else 'accepted',
                    'reason':'Exact independent fictional reconciliation.'}
                    for r in request.records if r.get('role')=='interpretation']})
            if request.task=='Verify source interpretation withdrawals':
                return json.dumps({'withdrawals':[{'assertion_id':wrong,'status':'accepted',
                    'reason':'Independently disproved attribution.',
                    'evidence':[{'event_id':self.event.id,'quote':self.event.prompt}]}]})
            if request.task=='Review source material coverage':raise ModelError('later audit coverage timeout')
            return original(request)
        self.worker.model=audit_failure;self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'pending')
        self.assertEqual(self.keys(),original_ids-{wrong})
        revision=self.history()[-1]['source_revision']
        self.assertEqual(revision['status'],'unresolved')
        self.assertEqual({r['assertion_id'] for r in revision['reconciliation'] if r['decision']=='withdrawn'},{wrong})
        self.assertEqual({r['assertion_id'] for r in revision['reconciliation'] if r['decision']=='retained'},original_ids-{wrong})
        with self.store.transaction() as db:
            request=db.execute('SELECT status FROM memory_source_audit_requests').fetchone()
        self.assertEqual(request['status'],'scheduled','Partial operational audit must not consume its request.')
        self.assertIsNotNone(self.store.get_assertion(wrong))

    def test_audit_failure_before_prior_verification_preserves_last_positive_membership(self):
        self.error=None;self.tick_source();keys=self.keys()
        self.queue.request_source_audit(self.event.id,'fictional-observation')
        self.failure_stage='verify';self.error=TimeoutError('before prior check')
        self.tick_source()
        self.assertEqual(self.queue.get(self.event.id).status,'pending')
        self.assertEqual(self.keys(),keys)
        self.assertEqual(self.history()[-1]['source_revision']['status'],'failed')
        self.assertFalse(semantic_terminal(self.history()[-1]))

    def test_retry_state_write_and_partial_publication_roll_back_together(self):
        from prototypes.assertion_memory.dialogue import extract_dialogue
        claim=self.queue._claim(with_model=True);frozen=self.queue._freeze_claim_input(claim)
        result=extract_dialogue(self.provider,frozen.event,list(frozen.events));result['input_fingerprint']=frozen.fingerprint
        original=self.queue._fail
        def fail_after_retry_write(*args):
            original(*args)
            raise sqlite3.OperationalError('fictional transaction abort after queue write')
        with patch.object(self.queue,'_fail',side_effect=fail_after_retry_write):
            with self.assertRaisesRegex(sqlite3.OperationalError,'after queue write'):
                self.queue._finish_extraction(claim,self.event,result)
        self.assertEqual(self.history(),[]);self.assertEqual(self.store.list_assertions(),[])
        self.assertEqual(self.queue.get(self.event.id).status,'running')
        self.assertEqual(self.queue.get(self.event.id).attempts,1)

    def test_actual_provider_exception_types_survive_with_checked_partial_meaning(self):
        for kind in (KeyError,RuntimeError,TimeoutError,PermissionError,sqlite3.OperationalError):
            with self.subTest(kind=kind):
                if self.calls:self.setUp()
                self.count=1;self.error=kind('fictional provider acquisition error')
                self.tick_source();result=self.assert_partial(1)
                self.assertEqual(result['context_recovery'][-1]['operational_error']['type'],kind.__name__)

    def test_unexpected_controller_programming_error_remains_raised(self):
        from prototypes.assertion_memory.dialogue import extract_dialogue
        self.error=None
        with patch('prototypes.assertion_memory.source_coverage._validated_coverage',side_effect=KeyError('controller defect')):
            with self.assertRaisesRegex(KeyError,'controller defect'):
                extract_dialogue(self.provider,self.event,[self.event])
        self.assertEqual(self.history(),[]);self.assertEqual(self.store.list_assertions(),[])

    def test_provider_tag_preserves_exception_identity_and_excludes_lease_callback(self):
        from prototypes.assertion_memory.dialogue import _with_before_model
        error=RuntimeError('fictional acquisition')
        def unavailable(request):raise error
        with self.assertRaises(RuntimeError) as caught:_with_before_model(unavailable,None)(None)
        self.assertIs(caught.exception,error)
        callback_error=RuntimeError('fictional lease callback')
        def callback():raise callback_error
        with self.assertRaises(RuntimeError) as caught:_with_before_model(unavailable,callback)(None)
        self.assertIs(caught.exception,callback_error)
        self.assertFalse(getattr(callback_error,'_source_provider_error',False))

    def test_reused_tagged_exception_cannot_publish_after_lease_callback_loses_ownership(self):
        from prototypes.assertion_memory.dialogue import extract_dialogue
        claim=self.queue._claim(with_model=True);frozen=self.queue._freeze_claim_input(claim)
        calls=0;error=RuntimeError('previously tagged provider error');error._source_provider_error=True
        def before_model():
            nonlocal calls
            calls+=1
            if calls==3:
                self.now+=timedelta(seconds=self.queue._lease_seconds+1)
                raise error
        result=extract_dialogue(self.provider,self.event,[self.event],before_model=before_model)
        result['input_fingerprint']=frozen.fingerprint
        with self.assertRaises(LeaseLost):self.queue._finish_extraction(claim,self.event,result)
        self.assertEqual(self.history(),[]);self.assertEqual(self.store.list_assertions(),[])
