"""Exact grounded requests survive only their own bounded operational attempts."""
import copy
import json
import sqlite3
import unittest
from datetime import timedelta
from unittest.mock import patch

from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.tests import test_query_semantic_recovery as query_fixture
from prototypes.assertion_memory.tests import test_zero_canonical_recovery as zero_fixture


class SourceAuditContinuationTest(unittest.TestCase):
    def fixture(self,zero=False):
        x=(zero_fixture.ZeroCanonicalRecoveryTest if zero else query_fixture.QuerySemanticRecoveryTest)()
        x.setUp();self.addCleanup(x.doCleanups)
        return x

    def start(self,*,zero=False,introduce=False):
        x=self.fixture(zero);old=x.query();x.recover=True
        original=copy.deepcopy(json.loads(x.requests()[0]['evidence_json']))
        base=x.provider;coverage=0;failing=True
        def provider(request):
            nonlocal coverage
            if request.task=='Review source material coverage':
                coverage+=1
                if failing and (not introduce or coverage==2):raise ModelError('fictional source operational timeout')
            return base(request)
        x.worker.model=provider
        x.worker.run_once()
        self.assertEqual(x.queue.get(x.event.id).status,'pending')
        self.assertEqual(x.requests()[0]['status'],'scheduled')
        def healthy():
            nonlocal failing
            failing=False
        return x,old,original,healthy

    def drain(self,x):
        with patch.object(x.engine,'_impact_selection',return_value=([],{})),patch.object(x.engine.candidates,'rank',return_value=[]):
            for _ in range(30):
                if x.worker.run_once() is None:return
        self.fail('Background recovery did not become idle.')

    def test_original_cold_repro_keeps_exact_hypothesis_and_refreshes_exact_origin(self):
        x,old,original,healthy=self.start()
        healthy();x.now+=timedelta(seconds=2);start=len(x.calls);self.drain(x)
        self.assertEqual(x.requests()[0]['status'],'completed')
        hypotheses=[r for c in x.calls[start:] for r in c.records if r.get('role')=='source_omission_hypothesis']
        self.assertTrue(hypotheses,'Healthy retry must receive the original independently admitted omission.')
        self.assertEqual(hypotheses[0]['request_id'],x.requests()[0]['id'])
        self.assertEqual(hypotheses[0]['start'],original['start']);self.assertEqual(hypotheses[0]['end'],original['end'])
        self.assertNotEqual(x.engine.inspect_view('origin')['current_id'],old.id)
        self.assertIn('proposer stated',x.engine.get_version(x.engine.inspect_view('origin')['current_id']).summary)
        after=json.loads(x.requests()[0]['evidence_json'])
        self.assertEqual({k:after[k] for k in original},original,'Original admission authority must never be rewritten.')
        self.assertEqual(x.engine.get_version(old.id),old)

    def test_own_partial_introductions_and_zero_source_gap_keep_original_authority(self):
        for zero in (False,True):
            with self.subTest(zero=zero):
                x,old,original,healthy=self.start(zero=zero,introduce=True)
                partial=x.queue.extraction_history(x.event.id)[-1]
                self.assertGreater(len(partial['source_revision']['assertion_ids']),len(original['prior_ids']))
                keys=set(x.engine.capture_snapshot().assertions)
                healthy();x.now+=timedelta(seconds=2);start=len(x.calls);self.drain(x)
                self.assertEqual(x.requests()[0]['status'],'completed')
                self.assertTrue(any(r.get('role')=='source_omission_hypothesis' for c in x.calls[start:] for r in c.records))
                self.assertEqual(set(x.engine.capture_snapshot().assertions),keys)
                self.assertNotEqual(x.engine.inspect_view('origin')['current_id'],old.id)
                self.assertEqual(x.queue.extraction_history(x.event.id)[-2],partial)
                self.assertEqual(json.loads(x.requests()[0]['evidence_json'])['source_revision'],original['source_revision'])

    def test_two_own_operational_attempts_then_healthy_third_do_not_reset_budget(self):
        x,old,original,healthy=self.start()
        x.now+=timedelta(seconds=2);x.worker.run_once()
        self.assertEqual(x.queue.get(x.event.id).attempts,2)
        self.assertEqual(x.requests()[0]['status'],'scheduled')
        healthy();x.now+=timedelta(seconds=4);start=len(x.calls);self.drain(x)
        self.assertEqual(x.requests()[0]['status'],'completed')
        self.assertTrue(any(r.get('role')=='source_omission_hypothesis' for c in x.calls[start:] for r in c.records))
        self.assertNotEqual(x.engine.inspect_view('origin')['current_id'],old.id)

    def test_unrelated_revision_even_copied_operational_receipt_is_stale(self):
        x,old,original,healthy=self.start(introduce=True)
        with x.store.transaction(write=True) as db:
            row=db.execute('SELECT * FROM memory_extraction_runs WHERE event_id=? ORDER BY id DESC LIMIT 1',(x.event.id,)).fetchone()
            external=json.loads(row['result'])
            for receipt in external['source_revision']['reconciliation']:
                if receipt['decision']=='introduced':receipt['decision']='retained'
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (x.event.id,'external-revision',row['completed_at'],json.dumps(external)))
        healthy();x.now+=timedelta(seconds=2);start=len(x.calls);self.drain(x)
        self.assertEqual(x.requests()[0]['status'],'stale')
        self.assertFalse(any(r.get('role')=='source_omission_hypothesis' for c in x.calls[start:] for r in c.records))
        self.assertEqual(x.engine.inspect_view('origin')['current_id'],old.id)

    def test_changed_input_protocol_and_admission_binding_are_stale(self):
        for mode in ('input','protocol','admission'):
            with self.subTest(mode=mode):
                x,old,original,healthy=self.start(introduce=True)
                with x.store.transaction(write=True) as db:
                    if mode=='input':
                        db.execute("UPDATE memory_raw_events SET metadata=json_set(metadata,'$.unavailable_context_before',json('[{\"reason\":\"new missing prefix\"}]')) WHERE id=?",(x.event.id,))
                    elif mode=='protocol':db.execute("UPDATE memory_source_audit_requests SET protocol='external-protocol'")
                    else:
                        evidence=json.loads(x.requests()[0]['evidence_json']);evidence['frame_sha256']='forged-frame'
                        db.execute('UPDATE memory_source_audit_requests SET evidence_json=?',(json.dumps(evidence),))
                healthy();x.now+=timedelta(seconds=2);start=len(x.calls);self.drain(x)
                self.assertEqual(x.requests()[0]['status'],'stale')
                self.assertFalse(any(r.get('role')=='source_omission_hypothesis' for c in x.calls[start:] for r in c.records))
                self.assertEqual(x.engine.inspect_view('origin')['current_id'],old.id)

    def test_partial_request_binding_and_retry_transition_roll_back_together(self):
        x=self.fixture();x.query();x.recover=True
        before=copy.deepcopy(x.queue.extraction_history(x.event.id));request=copy.deepcopy(x.requests()[0])
        fail=x.queue._fail
        def abort(*args):
            fail(*args)
            raise sqlite3.OperationalError('fictional abort after retry write')
        claim=None
        x.queue._maintenance_tick();claim=x.queue._claim(with_model=True,maintenance_only=True)
        frozen=x.queue._freeze_claim_input(claim)
        from prototypes.assertion_memory.source_reprocessing import audit
        def unavailable(r):
            if r.task=='Review source material coverage':raise ModelError('fictional timeout')
            return x.provider(r)
        result=audit(unavailable,x.queue,frozen)
        with patch.object(x.queue,'_fail',side_effect=abort):
            with self.assertRaisesRegex(sqlite3.OperationalError,'after retry write'):
                x.queue._finish_extraction(claim,x.event,result)
        self.assertEqual(x.queue.extraction_history(x.event.id),before)
        self.assertEqual(x.requests()[0]['evidence_json'],request['evidence_json'])
        self.assertEqual(x.queue.get(x.event.id).status,'running')

    def test_changed_continuation_owner_and_reaudit_attempt_reset_are_stale(self):
        for mode in ('owner','attempt_reset'):
            with self.subTest(mode=mode):
                x,old,original,healthy=self.start(introduce=True)
                with x.store.transaction(write=True) as db:
                    if mode=='owner':
                        row=db.execute('SELECT id,result FROM memory_extraction_runs WHERE event_id=? ORDER BY id DESC LIMIT 1',(x.event.id,)).fetchone()
                        result=json.loads(row['result'])
                        receipt=next(r for r in result['context_recovery'] if r.get('operation')=='source_audit_continuation')
                        receipt['job_id']='another-owner'
                        db.execute('UPDATE memory_extraction_runs SET result=? WHERE id=?',(json.dumps(result),row['id']))
                    else:db.execute('UPDATE memory_capture_jobs SET attempts=0 WHERE event_id=?',(x.event.id,))
                healthy();x.now+=timedelta(seconds=2);start=len(x.calls);self.drain(x)
                self.assertEqual(x.requests()[0]['status'],'stale')
                self.assertFalse(any(r.get('role')=='source_omission_hypothesis' for c in x.calls[start:] for r in c.records))
                self.assertEqual(x.engine.inspect_view('origin')['current_id'],old.id)

    def test_three_owned_operational_failures_stop_request_at_existing_budget(self):
        x,old,original,healthy=self.start()
        for delay,attempt in ((2,2),(4,3)):
            x.now+=timedelta(seconds=delay);x.worker.run_once()
            self.assertEqual(x.queue.get(x.event.id).attempts,attempt)
        self.assertEqual(x.queue.get(x.event.id).status,'failed')
        self.assertEqual(x.requests()[0]['status'],'failed')
        calls=len(x.calls);x.now+=timedelta(seconds=30);self.drain(x)
        self.assertEqual(len(x.calls),calls)
        self.assertEqual(x.queue.get(x.event.id).attempts,3)
        self.assertEqual(x.engine.inspect_view('origin')['current_id'],old.id)

    def test_changed_admission_during_owned_attempt_rolls_back_partial_binding(self):
        x=self.fixture();x.query();x.recover=True
        x.queue._maintenance_tick();claim=x.queue._claim(with_model=True,maintenance_only=True)
        frozen=x.queue._freeze_claim_input(claim)
        from prototypes.assertion_memory.source_reprocessing import audit
        calls=0
        def provider(r):
            nonlocal calls
            if r.task=='Review source material coverage':
                calls+=1
                if calls==2:raise ModelError('fictional failure after verified addition')
            return x.provider(r)
        result=audit(provider,x.queue,frozen)
        before=copy.deepcopy(x.queue.extraction_history(x.event.id));keys=set(x.engine.capture_snapshot().assertions)
        with x.store.transaction(write=True) as db:
            evidence=json.loads(x.requests()[0]['evidence_json']);evidence['frame_sha256']='changed-during-attempt'
            db.execute('UPDATE memory_source_audit_requests SET evidence_json=?',(json.dumps(evidence),))
        # This was the original claim, so the immutable admitted authority is
        # still the request evidence frozen before generation, not a new frame.
        with self.assertRaisesRegex(ValueError,'admission changed'):
            x.queue._finish_extraction(claim,x.event,result)
        self.assertEqual(x.queue.extraction_history(x.event.id),before)
        self.assertEqual(set(x.engine.capture_snapshot().assertions),keys)

    def test_changed_admission_during_healthy_attempt_cannot_complete_request(self):
        x,old,original,healthy=self.start(introduce=True)
        healthy();x.now+=timedelta(seconds=2)
        claim=x.queue._claim(with_model=True,maintenance_only=True)
        frozen=x.queue._freeze_claim_input(claim)
        from prototypes.assertion_memory.source_reprocessing import audit
        result=audit(x.provider,x.queue,frozen)
        before=copy.deepcopy(x.queue.extraction_history(x.event.id))
        with x.store.transaction(write=True) as db:
            evidence=json.loads(x.requests()[0]['evidence_json']);evidence['frame_sha256']='changed-during-healthy-attempt'
            db.execute('UPDATE memory_source_audit_requests SET evidence_json=?',(json.dumps(evidence),))
        with self.assertRaisesRegex(ValueError,'admission changed'):
            x.queue._finish_extraction(claim,x.event,result)
        self.assertEqual(x.queue.extraction_history(x.event.id),before)
        self.assertEqual(x.requests()[0]['status'],'scheduled')
        self.assertEqual(x.engine.inspect_view('origin')['current_id'],old.id)
