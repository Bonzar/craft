import io
import json
from unittest import TestCase
from unittest.mock import Mock
from configured_base_prod_gate import BASE,Original,Content,Promotion,intent_document
from configured_entry_binding import receive_terminal
from configured_terminal_state import initial,admit
from root_phase_binding import observe_record,document
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

P=Release('a'*40,'b'*64,'c'*64,'legacy')
N=Release('d'*40,'e'*64,'f'*64,'sidecar')
A=Admission('5','7'*64,N)
promo=Promotion(BASE,Original('assertion-memory-v0.1.40',N.revision,'2','3','4',N.archive),'a'*40,'5','6',Content(N.archive),'workflow_run')
def stream(op, trailing=b'', **fields):
    return io.BytesIO(json.dumps(dict(op=op,intent=intent_document(promo),candidate=dict(revision=N.revision,
        archive=N.archive,manifest=N.manifest,mode=N.mode),**fields)).encode()+b'\n'+trailing)

class Binding(TestCase):
    def test_protected_phase_is_pair_and_nonce_bound(self):
        raw=admit(initial(trusted_prior=P),verified_admission=A)
        for phase in ('restart','restore'):
            self.assertEqual(observe_record(document(TerminalRequest(A,P,phase)),raw),phase)
        for bad in (TerminalRequest(Admission('99',A.nonce,N),P,'restart'),
                    TerminalRequest(Admission(A.run,'8'*64,N),P,'restart'),
                    TerminalRequest(A,N,'restart')):
            with self.assertRaises(ValueError):observe_record(document(bad),raw)
        with self.assertRaises(ValueError):observe_record(document(TerminalRequest(A,P,'restart')),initial(trusted_prior=P))

    def test_protected_wire_authorizes_before_streaming_each_byte(self):
        archive=b'opaque\xff\n'
        result={'status':'ok','environment':'production','revision':N.revision,'sha256':N.archive}
        auth=Mock(return_value=result)
        calls=[]
        def activate(revision,digest,body):
            self.assertEqual(auth.call_count,1)
            calls.append((revision,digest,body.read()))
            return result
        self.assertEqual(receive_terminal(stream('terminal-confirm-v3',archive,nonce=A.nonce,token='fixture'),auth,activate),result)
        self.assertEqual(calls,[(N.revision,N.archive,archive)])
        auth.return_value={'status':'rejected'}
        with self.assertRaises(ValueError):
            receive_terminal(stream('terminal-confirm-v3',archive,nonce=A.nonce,token='fixture'),auth,activate)
        self.assertEqual(len(calls),1)
        for op in ('challenge','confirm','unknown','terminal-challenge-v3'):
            with self.assertRaises(ValueError):
                receive_terminal(stream(op,archive),auth,activate)
