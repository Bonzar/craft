from dataclasses import replace
from hashlib import sha256
from unittest import TestCase
from unittest.mock import patch
from configured_terminal_state import initial,admit
from guard_lookup import respond,REQUEST
from settled_guard_binding import _mode,restart_selected,BASE,GRAPH
from test_terminal_verifier import Verify

class GuardBinding(TestCase):
    def test_exact_candidate_and_old_archive_under_one_authorized_pair(self):
        f=Verify(); mark=b'assertion-memory-base-browser-listener-v1\n'
        new=sha256(mark).hexdigest().encode()+b'  ./BASE_BROWSER_LISTENER_CONTRACT\n'
        old=b'prior exact manifest\n'
        before=replace(f.old,manifest=sha256(old).hexdigest())
        candidate=replace(f.new,manifest=sha256(new).hexdigest())
        signed=replace(f.admission,candidate=candidate)
        proof=respond(raw_request=REQUEST,verified_peer_uid=986,read_authorizer_state=lambda:
             admit(initial(trusted_prior=before),verified_admission=signed),
             independently_observed_phase=lambda:'restart')
        self.assertEqual(_mode(proof,active_directory=candidate.revision+'-'+candidate.archive,
                               raw_manifest=new,marker=mark),'sidecar')
        inverse=respond(raw_request=REQUEST,verified_peer_uid=986,read_authorizer_state=lambda:
             admit(initial(trusted_prior=before),verified_admission=signed),
             independently_observed_phase=lambda:'restore')
        self.assertEqual(_mode(inverse,active_directory=before.revision+'-'+before.archive,
                               raw_manifest=old,marker=None),'legacy')
        with self.assertRaises(ValueError):
            _mode(proof,active_directory=candidate.revision+'-'+candidate.archive,raw_manifest=new,marker=None)

    def test_control_commands_remain_exact_same_user(self):
        for mode, expected in (('legacy',[('restart',BASE,GRAPH)]),('sidecar',[('stop',GRAPH),('restart',BASE)])):
            effects=[]
            with patch('settled_guard_binding.verified_settled_mode',return_value=mode):
                restart_selected(run=lambda x,timeout:effects.append(tuple(x[2:])),
                      userctl=lambda _:['/usr/bin/systemctl','--user'],active=lambda _:False)
                self.assertEqual(effects,expected)
