from unittest import TestCase
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
            read_admission=lambda:self.state,read_projection=self.read,persist_projection=self.persist)
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
