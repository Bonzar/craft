from dataclasses import replace
from unittest import TestCase
from transition_witness import Admission, Release, WitnessState

class Witness(TestCase):
    prior=Release('1'*40,'2'*64,'3'*64,'legacy')
    new=Release('4'*40,'5'*64,'6'*64,'sidecar')
    admission=Admission('42','7'*64,new)
    def setUp(self):
        self.state=WitnessState(self.prior)
        self.state.consume(self.admission)
        self.request=dict(peer_is_verified_writer=True,run='42',nonce='7'*64,phase='restart',observed=self.new)

    def test_exact_candidate_and_prior_recovery_survive_repeated_guard_check(self):
        self.assertEqual(self.state.lookup(**self.request),self.new)
        recovery=self.request|dict(phase='restore',observed=self.prior)
        self.assertEqual(self.state.lookup(**recovery),self.prior)
        self.assertEqual(self.state.lookup(**recovery),self.prior)
        self.state.settle(verified_terminal=self.prior)
        self.assertEqual(self.state.active,self.prior)
        with self.assertRaises(ValueError):
            self.state.lookup(**recovery)

    def test_cross_uid_wrong_phase_and_altered_artifact_rejected(self):
        for update in (dict(peer_is_verified_writer=False),dict(phase='prepare'),dict(run='43'),
                       dict(nonce='8'*64),dict(phase='restore'),dict(observed=self.prior),
                       dict(observed=replace(self.new,manifest='0'*64)),
                       dict(observed=replace(self.new,mode='legacy'))):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.state.lookup(**(self.request|update))

    def test_replay_and_overlap_denied(self):
        with self.assertRaises(ValueError):
            self.state.consume(replace(self.admission,run='43'))
        self.state.settle(verified_terminal=self.new)
        with self.assertRaises(ValueError):
            self.state.consume(self.admission)
        with self.assertRaises(ValueError):
            self.state.lookup(**self.request)
