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
