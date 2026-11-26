from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
from configured_authorizer_binding import serve_exact
from configured_terminal_state import initial,admit
from guard_lookup import REQUEST
from test_terminal_verifier import Verify

class Channel:
    def __init__(self,wire):
        self.wire=wire; self.sent=[]
    def settimeout(self,_): pass
    def makefile(self,_): return BytesIO(self.wire)
    def sendall(self,data): self.sent.append(data)

class MixedSocket(TestCase):
    def route(self, wire, marker, expect_legacy=False):
        f=Verify()
        record=admit(initial(trusted_prior=f.old),verified_admission=f.admission)
        peer=Channel(wire)
        seen=[]
        def normal(channel,*,prefetched_frame):
            self.assertIs(channel,peer)
            seen.append(prefetched_frame)
        def dispatch(channel, **kw): kw['normal'](channel)
        with (patch('configured_authorizer_binding.dispatch_existing_authorizer',side_effect=dispatch),
              patch('configured_authorizer_binding.read_owned_record',return_value=record)):
            serve_exact(peer,normal=normal,independently_observed_phase=lambda:'restore',
                       protected_marker_active=lambda:marker)
        self.assertEqual(bool(seen),expect_legacy)
        return peer.sent
    def test_exact_one_line_demux_and_independent_marker(self):
        legacy=b'{"op":"challenge","intent":{}}\n'
        self.assertEqual(self.route(legacy,False,True),[])
        self.assertEqual(self.route(legacy,True),[b'{"status":"rejected"}\n'])
        self.assertEqual(self.route(REQUEST,False),[b'{"status":"rejected"}\n'])
        self.assertIn(b'"pending":',self.route(REQUEST,True)[0])
        self.assertEqual(self.route(REQUEST+b'fake',True),[b'{"status":"rejected"}\n'])
