from unittest import TestCase
from unittest.mock import patch
from configured_authorizer_binding import admit_exact,serve_exact
from configured_terminal_state import initial,admit,pending_request,read_exact
from root_terminal_publisher import canonical_terminal
from test_terminal_verifier import Verify

class Binding(TestCase):
    def test_spend_before_cas_and_no_mutation_on_refusal(self):
        f=Verify(); state=initial(trusted_prior=f.old); order=[]
        def consumer(exact):
            self.assertEqual(exact,f.admission)
            order.append('one-use')
            return True
        def publish(**kwargs):
            self.assertEqual(kwargs['expected'],state)
            self.assertEqual(read_exact(kwargs['replacement'])[1],f.admission)
            order.append('cas')
        args=dict(token=None,issuer_keys=None,policy=None,promotion=None,nonce=None,candidate=None,now=None)
        with (patch('configured_authorizer_binding.verify_admission',return_value=f.admission),
              patch('configured_authorizer_binding.read_owned_record',return_value=state),
              patch('configured_authorizer_binding.publish',side_effect=publish)):
            self.assertEqual(admit_exact(**args,consume_authorized_run=consumer),f.admission)
            self.assertEqual(order,['one-use','cas'])
            with self.assertRaises(ValueError):
                admit_exact(**args,consume_authorized_run=lambda _:False)
            self.assertEqual(order,['one-use','cas'])

    def test_exact_ops_host_role_and_restore_phase_cas_with_full_record_ack(self):
        f=Verify()
        raw=admit(initial(trusted_prior=f.old),verified_admission=f.admission)
        state=[raw]
        req=pending_request(raw,verified_root_phase='restore')
        receipt=canonical_terminal(req,f.old)
        def dispatch(peer,*,normal,read_exact_pending,finish_cas,acknowledge_terminal):
            self.assertEqual(read_exact_pending(),(raw,req))
            finish_cas(expected=raw,replacement=receipt)
            self.assertIsNone(read_exact_pending())
            self.assertEqual(len(acknowledge_terminal(expected_receipt=receipt)),64)
        def store(*,expected,replacement):
            self.assertEqual(state[0],expected)
            state[0]=replacement
        with (patch('configured_authorizer_binding.read_owned_record',side_effect=lambda:state[0]),
              patch('configured_authorizer_binding.publish',side_effect=store),
              patch('configured_authorizer_binding.acknowledge') as synced,
              patch('configured_authorizer_binding.dispatch_existing_authorizer',side_effect=dispatch)):
            serve_exact(None,normal=None,independently_observed_phase=lambda:'restore',protected_marker_active=lambda:True)
            synced.assert_called_once_with(expected_receipt=state[0])
