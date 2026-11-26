from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock,patch
from release_core import ActivationWitness,activate,recover_activation,PENDING
from native_checkpoint import _before_effect
from native_boundary import PROD_ROOT
from root_phase_binding import document
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

old=Release('a'*40,'b'*64,'c'*64,'legacy')
new=Release('d'*40,'e'*64,'f'*64,'sidecar')
signed=Admission('55','9'*64,new)
previous=PROD_ROOT/'releases'/(old.revision+'-'+old.archive)
candidate=PROD_ROOT/'releases'/(new.revision+'-'+new.archive)

class Checkpoint(TestCase):
    def test_native_checkpoint_rereads_root_receipt_and_exact_physical(self):
        expected=TerminalRequest(signed,old,'restart')
        forward=document(expected)
        back=document(TerminalRequest(signed,old,'restore'))
        receipt=Mock(side_effect=[forward,back,back])
        _before_effect(expected,root=PROD_ROOT,phase='restore',old=previous,candidate=candidate,
            root_receipt=receipt,native_observer=lambda req:(old,(signed.run,signed.nonce,old,new)),
            clock=lambda:0,sleep=lambda delay:None,timeout=3)
        self.assertEqual(receipt.call_count,3)
        with self.assertRaises(ValueError):
            _before_effect(expected,root=PROD_ROOT,phase='restart',old=previous,candidate=candidate,
                root_receipt=lambda:back,native_observer=Mock(),clock=lambda:0,sleep=Mock(),timeout=3)

    def test_real_release_core_places_checkpoint_after_swap_and_before_hook(self):
        events=[]
        hook=lambda script,phase,path:events.append((phase,path))
        witness=ActivationWitness(Mock(),Mock(),Mock(),
            before_effect=lambda root,phase,old,new:events.append(('proof',phase,old,new)))
        def swap(root,selected):events.append(('swap',selected))
        with patch('release_core.current_target',return_value=str(previous)), patch('release_core.swap',side_effect=swap), patch('release_core.record_activation'), patch('release_core.sync_dir'), patch('release_core._finish_activation_journal'):
            activate(PROD_ROOT,candidate,None,hook,witness=witness)
        self.assertEqual(events[:3],[('swap',candidate),('proof','restart',previous,candidate),('restart',candidate)])
        events.clear()
        native_journal='{"old":"'+previous.name+'","new":"'+candidate.name+'"}'
        with patch('pathlib.Path.is_symlink',return_value=False), patch('pathlib.Path.exists',return_value=True), patch('pathlib.Path.is_file',return_value=True), patch('pathlib.Path.stat',return_value=Mock(st_size=200)), patch('pathlib.Path.read_bytes',return_value=native_journal.encode()), patch('release_core.current_target',return_value=str(candidate)), patch('release_core.checked_release',side_effect=lambda _r,name:PROD_ROOT/'releases'/name), patch('release_core.swap',side_effect=swap), patch('release_core.sync_dir'), patch('release_core._finish_activation_journal'):
            recover_activation(PROD_ROOT,None,hook,witness=witness)
        self.assertEqual(events[:3],[('swap',previous),('proof','restore',previous,candidate),('restore',previous)])
