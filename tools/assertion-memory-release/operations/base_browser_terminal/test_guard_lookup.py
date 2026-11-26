from dataclasses import replace
from unittest import TestCase
from configured_terminal_state import initial,admit
from guard_lookup import REQUEST,respond,mode_for_existing_guard
from test_terminal_verifier import Verify

class Guard(TestCase):
    def test_only_same_uid_pending_exact_forward_inverse(self):
        f=Verify()
        opened=initial(trusted_prior=f.old)
        record=admit(opened,verified_admission=f.admission)
        lookup=lambda:record
        proof=respond(raw_request=REQUEST,verified_peer_uid=986,read_authorizer_state=lookup)
        self.assertEqual(mode_for_existing_guard(proof,physical_identity=f.new),'sidecar')
        self.assertEqual(mode_for_existing_guard(proof,physical_identity=f.old),'legacy')
        for variant in (replace(f.new,manifest='a'*64),replace(f.old,mode='sidecar')):
            with self.assertRaises(ValueError):
                mode_for_existing_guard(proof,physical_identity=variant)
        with self.assertRaises(ValueError):
            respond(raw_request=REQUEST,verified_peer_uid=980,read_authorizer_state=lookup)
        with self.assertRaises(ValueError):
            respond(raw_request=b'{"op":"conditional-reset"}\n',verified_peer_uid=986,read_authorizer_state=lookup)
        response=respond(raw_request=REQUEST,verified_peer_uid=986,read_authorizer_state=lambda:opened)
        with self.assertRaises(ValueError):
            mode_for_existing_guard(response,physical_identity=f.old)
