from contextlib import nullcontext
import sqlite3
import unittest
from unittest.mock import Mock

from test_forward_release_train import art, cand, witness
from forward_release_train import FixApproval, Ledger, Reject
from forward_release_checkpoints import Checkpoints, Published, SupervisedLegacy


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.base = witness('prod',art(cand('0.1.36','a'),1))
        self.c = cand('1.0.0','b')
        self.p = Published(self.c,10,True)
        self.a = art(self.c,10)
        self.lookup = lambda _: self.p
        self.q = Checkpoints(Ledger(sqlite3.connect(':memory:'),lambda:1000), lambda _: nullcontext())
        self.q.ledger.initialize(self.base)

    def test_build_can_exist_but_deployment_is_gated(self):
        next_candidate = cand('2.0.0','c')
        b=art(next_candidate,11)
        next_p=Published(next_candidate,11,True)
        execute=Mock(side_effect=lambda a:witness('test',a))
        with self.assertRaises(Reject):
            self.q.test(b,verified_release=lambda _:next_p,live_prod=lambda _:self.base,
                        verify_fix=Mock(),activate_and_probe=execute)
        execute.assert_not_called()
        test=self.q.test(self.a,verified_release=self.lookup,live_prod=lambda _:self.base,
                         verify_fix=Mock(),activate_and_probe=execute)
        live=self.q.production(self.a,verified_release=self.lookup,approve=lambda _:True,
                               actual_test=lambda _:test,activate_and_probe=lambda a:witness('prod',a))
        self.q.test(b,verified_release=lambda _:next_p,live_prod=lambda _:live,
                    verify_fix=Mock(),activate_and_probe=execute)

    def test_wrong_approval_or_wrong_service_never_closes_train(self):
        test=self.q.test(self.a,verified_release=self.lookup,live_prod=lambda _:self.base,
                         verify_fix=Mock(),activate_and_probe=lambda a:witness('test',a))
        execute=Mock(return_value=witness('prod',art(cand('1.0.0','e'),10)))
        for i in range(2):
            with self.assertRaises(Reject):
                self.q.production(self.a,verified_release=self.lookup,approve=lambda _:bool(i),
                                  actual_test=lambda _:test,activate_and_probe=execute)
        execute.assert_called_once()

    def test_supervised_restore_never_seeds_train(self):
        legacy = Published(self.base.artifact.candidate,1,True)
        self.q.supervised_legacy(SupervisedLegacy('historical_rollback',legacy,'evidence'),
                                 verify=lambda _:True,perform_and_probe=lambda _:self.base)
        self.assertFalse(self.q.ledger.is_reserved(self.c))

    def test_new_admitted_test_retires_old_after_switch_attempt_even_on_failure(self):
        test=self.q.test(self.a,verified_release=self.lookup,live_prod=lambda _:self.base,
                         verify_fix=Mock(),activate_and_probe=lambda a:witness('test',a))
        f=cand('1.1.0','e')
        a=art(f,12)
        okay=FixApproval(self.a,f,777,888,'c'*64,'open_major_defect_only')
        switch=Mock(side_effect=RuntimeError('TEST provider lost'))
        pub=lambda _:Published(f,12,True)
        with self.assertRaises(Reject):
            self.q.test(a,verified_release=pub,live_prod=Mock(),verify_fix=lambda _:None,activate_and_probe=switch)
        switch.assert_not_called()
        self.assertTrue(self.q.ledger.is_reserved(self.c))
        # Trusted new failing check may invalidate a formerly passing TEST.
        with self.assertRaises(RuntimeError):
            self.q.test(a,verified_release=pub,live_prod=Mock(),verify_fix=lambda _:okay,activate_and_probe=switch)
        self.assertTrue(self.q.ledger.is_reserved(f))
        self.assertFalse(self.q.ledger.is_reserved(self.c))
        effect=Mock()
        with self.assertRaises(Reject):
            self.q.production(self.a,verified_release=self.lookup,approve=lambda _:True,
                              actual_test=lambda _:test,activate_and_probe=effect)
        effect.assert_not_called()

    def test_lost_test_receipt_reconciles_only_against_actual_exact_service(self):
        from dataclasses import replace
        persisted=witness('test',self.a,g='c')
        effects=[]
        def lost(a):
            effects.append(a)
            # Native switch succeeded, but process died before receipt.
            raise OSError('receipt lost after native switch')
        def test(effect, probe=None):
            return self.q.test(self.a,verified_release=self.lookup,live_prod=lambda _:self.base,verify_fix=Mock(),
                               activate_and_probe=effect,observe_test=probe)
        with self.assertRaises(OSError):
            test(lost)
        unwanted=Mock()
        wwrong=replace(persisted,artifact=art(cand('1.0.0','e'),10))
        for observed in (None,wwrong,replace(persisted,observed_at=879),replace(persisted,origin='rollback')):
            with self.assertRaises(Reject):
                test(unwanted,lambda _:observed)
        with self.assertRaises(Reject):
            test(unwanted)
        self.assertEqual(test(unwanted,lambda _:persisted),persisted)
        # After proof, a different active generation must not rewrite receipt.
        with self.assertRaises(Reject):
            test(unwanted,lambda _:replace(persisted,generation='d'*64))
        self.assertEqual(effects,[self.a])
        unwanted.assert_not_called()
        self.q.production(self.a,verified_release=self.lookup,approve=lambda _:True,
                          actual_test=lambda _:persisted,activate_and_probe=lambda a:witness('prod',a))

    def test_pending_attempt_is_durable_across_receiver_restart(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            db=sqlite3.connect(f.name)
            l=Ledger(db,lambda:1000)
            l.initialize(self.base)
            l.reserve(self.c,self.base,artifact=self.a)
            self.assertTrue(l.begin_test_activation(self.a))
            db.close()
            db=sqlite3.connect(f.name)
            l=Ledger(db,lambda:1000)
            self.assertFalse(l.begin_test_activation(self.a))
            l.reconcile_test_activation(self.a,witness('test',self.a))
            self.assertFalse(l.begin_test_activation(self.a))
            db.close()

    def test_pre_switch_crash_needs_exact_protected_scope_not_baseline_observation(self):
        from forward_release_train import TestRecovery
        self.q.ledger.reserve(self.c,self.base,artifact=self.a)
        self.assertTrue(self.q.ledger.begin_test_activation(self.a))
        attempt_id=self.q.ledger.db.execute('SELECT attempt_id FROM forward_test_attempt').fetchone()[0]
        try_switch=Mock(side_effect=OSError('No durable proof of outcome'))
        baseline=witness('test',self.base.artifact)
        recover=lambda proof, w=baseline:self.q.recover_test(self.a,verified_release=self.lookup,
            observe_test=lambda _:w,verify_recovery=lambda *_:proof,activate_and_probe=try_switch)
        with self.assertRaises(Reject):
            recover(None)
        for bad in (
                TestRecovery(self.a,attempt_id,'a'*64,'baseline_only'),
                TestRecovery(self.a,'1'*64,'a'*64,'verified_actuator_never_entered'),
                TestRecovery(art(cand('1.0.0','e'),10),attempt_id,'a'*64,'protected_manual_recovery')):
            with self.assertRaises(Reject):
                recover(bad)
        try_switch.assert_not_called()
        okay=TestRecovery(self.a,attempt_id,'a'*64,'verified_actuator_never_entered')
        with self.assertRaises(OSError):
            recover(okay)
        with self.assertRaises(Reject):
            recover(okay)
        self.assertEqual(recover(None,witness('test',self.a)),witness('test',self.a))
        try_switch.assert_called_once_with(self.a)
        self.assertEqual(self.q.ledger.db.execute('SELECT recovery_decision_id FROM forward_test_attempt').fetchone()[0],'a'*64)
