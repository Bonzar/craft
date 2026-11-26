from unittest import TestCase
from contextlib import contextmanager
from unittest.mock import Mock
from dataclasses import replace
from native_projection_guard import use_guard_projection,fallback_before_first
from terminal_verifier import Snapshot,Process,BASE,GRAPH
from terminal_projection import PUBLISH,READ,dispatch
from configured_terminal_state import admit,initial,encode,commit,pending_request
from root_terminal_publisher import canonical_terminal
from transition_witness import Admission,Release

prior=Release('a'*40,'b'*64,'c'*64,'legacy')
candidate=Release('d'*40,'e'*64,'f'*64,'sidecar')
signed=Admission('55','9'*64,candidate)
query=dict(op=READ,run=signed.run,nonce=signed.nonce)

class Projection(TestCase):
    def setUp(self):
        self.state=admit(initial(trusted_prior=prior),verified_admission=signed)
        self.current=None
        self.replays=[]
    def read(self,run,nonce):
        return self.current
    def persist(self,*,expected_admission,expected,replacement,identical):
        self.assertEqual(expected_admission,self.state)
        self.assertEqual(expected,self.current)
        self.current=replacement
        self.replays.append(identical)
    def call(self,doc,uid,active=True):
        return dispatch(encode(doc)+b'\n',verified_peer_uid=uid,active=active,
            read_admission=lambda:self.state,read_projection=self.read,persist_projection=self.persist,
            read_locked_view=lambda run,nonce:(self.state,self.current))
    def published(self,phase='restart',kind='phase',seal='1'):
        return dict(query,op=PUBLISH,phase=phase,kind=kind,proof=seal*64)
    def test_narrow_peers_and_same_run_monotonic_restore_continuation(self):
        self.assertEqual(self.call(query,986),{'status':'waiting'})
        start=self.published()
        for doc,uid,active in [(start,986,True),(query,0,True),(start,0,False),
                               (dict(start,snapshot={}),0,True),
                               (dict(start,nonce='8'*64),0,True),
                               (self.published(kind='precommit'),0,True)]:
            with self.assertRaises(ValueError):self.call(doc,uid,active)
        first=self.call(start,0)
        self.assertEqual(self.call(start,0),first)
        self.assertEqual(self.replays,[False,True])
        self.assertEqual(set(self.call(query,986)['event']),{'run','nonce','phase','kind','proof'})
        self.call(self.published(kind='precommit',seal='2'),0)
        self.call(self.published(phase='restore',seal='3'),0)
        with self.assertRaises(ValueError):self.call(start,0)
        self.call(self.published(phase='restore',kind='precommit',seal='4'),0)

    def test_no_foreign_terminal_and_exact_settled_receipt_only(self):
        req=pending_request(self.state,verified_root_phase='restart')
        receipt=canonical_terminal(req,candidate)
        self.state=commit(self.state,proven_root_request=req,terminal_receipt=receipt)
        result=self.call(query,986)
        self.assertEqual((result['status'],result['phase']),('settled','restart'))
        self.assertEqual(set(result),{'status','phase','proof'})
        with self.assertRaises(ValueError):self.call(dict(query,run='8'),986)
        with self.assertRaises(ValueError):self.call(self.published(),0)

    def test_settle_race_observed_reply_cannot_authorize_after_guard_fence(self):
        self.call(self.published(),0)
        old=self.call(query,986)
        self.assertEqual(old['status'],'ok')
        req=pending_request(self.state,verified_root_phase='restart')
        receipt=canonical_terminal(req,candidate)
        trace=[]
        @contextmanager
        def lease():
            trace.append('acquired')
            # Simulate exact settle winning the transition fence after an old
            # read but before the guard. The guard must ask again inside it.
            self.state=commit(self.state,proven_root_request=req,terminal_receipt=receipt)
            try:yield
            finally:trace.append('released')
        effect=Mock()
        with self.assertRaises(ValueError):
            use_guard_projection(req,enter_existing_fence=lease,
                read_fresh=lambda run,nonce:self.call(query,986),
                verify_physical=Mock(return_value=True),effect=effect)
        self.assertEqual(trace,['acquired','released'])
        effect.assert_not_called()

    def test_lost_ack_revalidates_same_digest_and_rejects_same_phase_with_different_digest(self):
        before=self.published()
        first=self.call(before,0)
        # Caller lost first response. Its exact replay gets the same hash
        # and the persistence contract re-fsyncs without a second replace.
        self.assertEqual(self.call(before,0),first)
        with self.assertRaises(ValueError):self.call(self.published(seal='2'),0)
        self.assertEqual(self.replays,[False,True])

    def test_failure_before_first_effect_only_recovers_unchanged_incumbent(self):
        req=pending_request(self.state,verified_root_phase='restore')
        base=Process(BASE,40,5,986,prior)
        graph=Process(GRAPH,41,10,986,prior)
        before=Snapshot(prior,base,graph,((8901,41),(8903,40)),None,
            '12345678-abcd-abcd-abcd-1234567890ab',(16,500))
        observed=replace(before,journal=(signed.run,signed.nonce,prior,candidate))
        with self.assertRaises(ValueError):
            fallback_before_first(req,baseline=before,
                observed=replace(observed,private=replace(base,start_ticks=6)))
        event=fallback_before_first(req,baseline=before,observed=observed)
        self.call(dict(event,op=PUBLISH),0)
        effect=Mock()
        from contextlib import nullcontext
        result=use_guard_projection(req,enter_existing_fence=nullcontext,
            read_fresh=lambda *_:self.call(query,986),
            verify_physical=lambda _:True,effect=effect)
        self.assertEqual(result,'already-physical-legacy')
        effect.assert_not_called()
        with self.assertRaises(ValueError):self.call(self.published(),0)
