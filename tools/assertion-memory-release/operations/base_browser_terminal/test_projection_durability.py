"""Exact persistence fault following replacement, independent of HTTP socket."""
import io
from unittest import TestCase
from unittest.mock import Mock, patch
from configured_terminal_state import admit,initial
from transition_witness import Admission,Release
import projection_persistence as p

class ProjectionDurability(TestCase):
    def test_directory_fsync_failure_after_replace_needs_identical_ack_without_replacing(self):
        active=Release('a'*40,'b'*64,'c'*64,'legacy')
        candidate=Release('d'*40,'e'*64,'f'*64,'sidecar')
        accepted=Admission('55','9'*64,candidate)
        state=admit(initial(trusted_prior=active),verified_admission=accepted)
        event=dict(run='55',nonce='9'*64,phase='restart',kind='phase',proof='1'*64)
        memory=[None]
        def swap(*a,**kw):memory[0]=event
        class Buffer(io.BytesIO):
            def fileno(self):return 12
        with patch.object(p,'_open',return_value=10),patch.object(p,'_regular'),patch.object(p,'_current',return_value=state),patch.object(p,'_get',side_effect=lambda *_:memory[0]),patch.object(p.os,'open',side_effect=[11,12,11]),patch.object(p.os,'close'),patch.object(p.os,'fdopen',return_value=Buffer()),patch.object(p.os,'stat'),patch.object(p.os.path,'samestat',return_value=True),patch.object(p.os,'unlink',side_effect=FileNotFoundError),patch.object(p.os,'replace',side_effect=swap) as rename,patch.object(p.os,'fsync',side_effect=[None,OSError('ACK lost between rename and directory durability'),None]) as sync,patch.object(p.fcntl,'flock'):
            with self.assertRaises(OSError):
                p.persist_projection(expected_admission=state,expected=None,replacement=event,identical=False)
            self.assertEqual(memory[0],event)
            p.persist_projection(expected_admission=state,expected=event,replacement=event,identical=True)
        self.assertEqual(rename.call_count,1)
        self.assertEqual([call.args for call in sync.call_args_list],[(12,),(10,),(10,)])
