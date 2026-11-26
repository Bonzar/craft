from unittest import TestCase
from unittest.mock import Mock,patch
from native_boundary import PROD_ROOT
from protected_native_activation import witness_for,_paths,activate_terminal
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

prior=Release('a'*40,'b'*64,'c'*64,'legacy')
candidate=Release('d'*40,'e'*64,'f'*64,'sidecar')
admission=Admission('55','9'*64,candidate)
req=TerminalRequest(admission,prior,'restart')
old,new=_paths(req)

class NativeBinding(TestCase):
    def test_full_transaction_native_callbacks_selected_exact_pair(self):
        with patch('protected_native_activation.checkpoint_for',return_value=Mock()) as checkpoint, patch('protected_native_activation._active_precommit') as pre, patch('protected_native_activation._settled') as settle:
            witness=witness_for(admission,prior)
            checkpoint.assert_called_once_with(req)
            for phase in ('restart','restore'):
                witness.before_effect(PROD_ROOT,phase,old,new)
                witness.before_unlink(PROD_ROOT,phase,old,new)
                witness.after_fsync(PROD_ROOT,phase,old,new)
                expected=TerminalRequest(admission,prior,phase)
                pre.assert_called_with(expected)
                settle.assert_called_with(expected)
            calls=pre.call_count
            with self.assertRaises(ValueError):witness.before_unlink(PROD_ROOT,'restart',new,old)
            self.assertEqual(pre.call_count,calls)
    def test_receiver_refuses_stock_activation_and_preserves_exact_stream(self):
        stream=Mock()
        with patch('protected_native_activation.os.getuid',return_value=986), patch('protected_native_activation.os.geteuid',return_value=986), patch('protected_native_activation._approved',return_value=prior), patch('protected_native_activation.base_payload.deploy') as deploy:
            activate_terminal(candidate.revision,candidate.archive,stream,admission=admission)
            self.assertEqual(deploy.call_args.args,(stream,candidate.revision,candidate.archive))
            self.assertTrue(callable(deploy.call_args.kwargs['witness_factory']))
            with self.assertRaises(ValueError):activate_terminal(prior.revision,prior.archive,stream,admission=admission)
            self.assertEqual(deploy.call_count,1)
