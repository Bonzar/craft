from contextlib import contextmanager
from dataclasses import replace
from unittest import TestCase

from terminal_lifecycle import ProtectedTerminalLifecycle,TerminalRequest,VerifiedPrecommit
from test_terminal_verifier import Verify

class Lifecycle(TestCase):
    def check(self,phase,damage=None):
        sample=Verify().fixture(phase)
        seq=[]
        chosen=TerminalRequest(sample['admission'],sample['predecessor'],phase)
        observations=[sample['before'],sample['before'],sample['after']]
        if damage=='mid-change':
            observations[1]=replace(observations[1],listeners=())
        if damage=='post-change':
            observations[2]=replace(observations[2],listeners=())
        if damage in ('recovery','recovery-missing'):
            observations=[sample['after']]
        @contextmanager
        def lease():
            seq.append('lease')
            try:
                yield
            finally:
                seq.append('release')
        def observe():
            seq.append('observe')
            return observations.pop(0)
        def smoke():
            seq.append('smoke')
            return sample['smoke']
        binding=ProtectedTerminalLifecycle(service_uid=986,release_lease=lease,
           read_pending=lambda:chosen,snapshot=observe,smoke=smoke,
           finish_native_release=lambda req:seq.append(('native-finish',req.phase)),
           read_precommit=lambda req:(VerifiedPrecommit(chosen,sample['before'],sample['smoke'])
                                     if damage=='recovery' else None),
           record_precommit=lambda prep:seq.append(('record',prep.request.phase)),
           settle_exact_authorizer=lambda req,release:seq.append(('settle',req.phase,release.mode)))
        return binding,seq

    def test_fixed_exact_candidate_and_recovery(self):
        for phase, mode in (('restart','sidecar'),('restore','legacy')):
            with self.subTest(phase=phase):
                b,seq=self.check(phase)
                self.assertEqual(b.verify_terminal().mode,mode)
                self.assertEqual(seq,['lease','observe','smoke','observe',('record',phase),('native-finish',phase),
                    'observe',('settle',phase,mode),'release'])

    def test_bad_before_clear_is_non_effectful(self):
        b,seq=self.check('restart','mid-change')
        with self.assertRaises(ValueError):
            b.verify_terminal()
        self.assertEqual(seq,['lease','observe','smoke','observe','release'])

    def test_bad_terminal_after_clear_never_settles(self):
        b,seq=self.check('restore','post-change')
        with self.assertRaises(ValueError):
            b.verify_terminal()
        self.assertNotIn(('settle','restore','legacy'),seq)
        self.assertEqual(seq[-2:],['observe','release'])


    def test_native_finish_not_repeated_after_crash(self):
        b,seq=self.check('restore','recovery')
        self.assertEqual(b.verify_terminal().mode,'legacy')
        self.assertEqual(seq,['lease','observe',('settle','restore','legacy'),'release'])
        b,seq=self.check('restore','recovery-missing')
        with self.assertRaises(ValueError):
            b.verify_terminal()
        self.assertEqual(seq,['lease','observe','release'])
