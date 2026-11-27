from dataclasses import replace
from unittest.mock import Mock
import unittest

from forward_release_checkpoints import NativeAttemptCheckpoints, NativeTestReceipt, Published
from forward_release_train import FixApproval, Reject, TestRecovery
import test_forward_release_checkpoints as fixtures
from test_forward_release_train import art, cand, witness


class NativeFence(unittest.TestCase):
    def fixture(self):
        f=fixtures.CheckpointTests()
        f.setUp()
        return f,NativeAttemptCheckpoints(f.q.ledger,f.q.lock)

    def test_same_digest_and_generation_do_not_accept_wrong_attempt(self):
        f,q=self.fixture()
        latest=q.test(f.a,verified_release=f.lookup,live_prod=lambda _:f.base,verify_fix=None,
                      activate_and_probe=lambda a,n:replace(witness('test',a),native_attempt=n))
        effect=Mock(side_effect=lambda a:witness('prod',a))
        receipt=NativeTestReceipt(f.a,latest.generation,latest.native_attempt)
        def promote(chosen, running):
            return q.production(f.a,verified_release=f.lookup,approve=lambda _:True,
                  actual_test=lambda _:running,activate_and_probe=effect,verified_receipt=chosen)
        for chosen,running in [(None,latest),(replace(receipt,attempt_id='b'*64),latest),
                               (receipt,replace(latest,native_attempt='c'*64)),
                               (receipt,replace(latest,native_attempt=None)),
                               (replace(receipt,generation='d'*64),latest)]:
            with self.assertRaises(Reject):
                promote(chosen,running)
        effect.assert_not_called()
        self.assertEqual(promote(receipt,latest).artifact,f.a)
        effect.assert_called_once()

    def test_new_native_test_success_retires_old_receipt(self):
        f,q=self.fixture()
        old=q.test(f.a,verified_release=f.lookup,live_prod=lambda _:f.base,verify_fix=None,
                   activate_and_probe=lambda a,n:replace(witness('test',a),native_attempt=n))
        c=cand('1.1.0','f'); a=art(c,12)
        newer=q.test(a,verified_release=lambda _:Published(c,12,True),live_prod=None,
                     verify_fix=lambda _:FixApproval(f.a,c,10,11,'b'*64,'open_major_defect_only'),
                     activate_and_probe=lambda a,n:replace(witness('test',a),native_attempt=n))
        self.assertNotEqual(old.native_attempt,newer.native_attempt)
        effect=Mock()
        with self.assertRaises(Reject):
            q.production(f.a,verified_release=f.lookup,approve=lambda _:True,actual_test=lambda _:old,
                 activate_and_probe=effect,verified_receipt=NativeTestReceipt(f.a,old.generation,old.native_attempt))
        effect.assert_not_called()

    def test_one_protected_continuation_rotates_attempt_and_fences_stale_witness(self):
        f,q=self.fixture()
        interrupted=Mock(side_effect=OSError('stopped before native effect'))
        with self.assertRaises(OSError):
            q.test(f.a,verified_release=f.lookup,live_prod=lambda _:f.base,verify_fix=None,activate_and_probe=interrupted)
        old=q.ledger.exact_attempt_id(f.a)
        decision=TestRecovery(f.a,old,'b'*64,'verified_actuator_never_entered')
        end=q.recover_test(f.a,verified_release=f.lookup,observe_test=lambda _:None,
                  verify_recovery=lambda *_:decision,
                  activate_and_probe=lambda a,n:replace(witness('test',a),native_attempt=n))
        self.assertNotEqual(end.native_attempt,old)
        effect=Mock()
        with self.assertRaises(Reject):
            q.test(f.a,verified_release=f.lookup,live_prod=None,verify_fix=None,activate_and_probe=effect,
                   observe_test=lambda _:replace(end,native_attempt=old))
        effect.assert_not_called()

    def test_receiver_binds_marker_around_switch_and_no_fallback_without_recorder(self):
        from contextlib import contextmanager
        from pathlib import Path
        from forward_release_receiver import Request,protected_activation
        f,q=self.fixture()
        r=Request(f.a,'test',123,Path('/owned'))
        events=[]
        token=[]
        @contextmanager
        def recorder(prepared,n):
            self.assertEqual(prepared,'prepared')
            token.append(n)
            events.append('bind')
            yield
            events.append('fsync')
        def switch():
            self.assertEqual(events,['bind'])
            events.append('switch')
        def probe(stage,a):
            self.assertEqual(events,['bind','switch','fsync'])
            return replace(witness(stage,a),native_attempt=token[0])
        def hook(binder):
            return protected_activation(q,r,receiver_stage='test',verify_dispatch=lambda req,_:req==r,
                          verified_test_receipt=None,identify_prepared=lambda _:f.a,probe=probe,
                          verify_release=f.lookup,live_prod=lambda _:f.base,bind_native_attempt=binder)
        with self.assertRaises(Reject):
            hook(None)('prepared',switch)
        self.assertFalse(q.ledger.is_reserved(f.c))
        self.assertEqual(hook(recorder)('prepared',switch).native_attempt,token[0])
