from dataclasses import replace
from unittest import TestCase
from configured_terminal_state import initial,admit,pending_request,commit,acknowledge_exact,read_exact
from root_terminal_publisher import canonical_terminal
from test_terminal_verifier import Verify

class DurableShape(TestCase):
    def test_exact_candidate_and_rollback_are_derived_from_prior_and_signed_record(self):
        f=Verify()
        prior=initial(trusted_prior=f.old)
        raw=admit(prior,verified_admission=f.admission)
        with self.assertRaises(ValueError):
            admit(raw,verified_admission=replace(f.admission,run='44'))
        for phase,identity in (('restart',f.new),('restore',f.old)):
            req=pending_request(raw,verified_root_phase=phase)
            receipt=canonical_terminal(req,identity)
            result=commit(raw,proven_root_request=req,terminal_receipt=receipt)
            self.assertEqual(read_exact(result)[0],identity)
            self.assertEqual(len(acknowledge_exact(result,expected_receipt=receipt)),64)
            with self.assertRaises(ValueError):
                acknowledge_exact(raw,expected_receipt=receipt)
            with self.assertRaises(ValueError):
                admit(result,verified_admission=f.admission)
            with self.assertRaises(ValueError):
                commit(raw,proven_root_request=replace(req,predecessor=f.new),terminal_receipt=receipt)
