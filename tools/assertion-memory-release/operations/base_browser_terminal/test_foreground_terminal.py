from dataclasses import replace
from unittest import TestCase
from unittest.mock import Mock
from foreground_terminal import observe_before_unlink,observe_after_unlink,precommit_wire,restore_precommit
from terminal_verifier import Snapshot,Process,BASE,GRAPH
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

old=Release('a'*40,'b'*64,'c'*64,'legacy')
new=Release('d'*40,'e'*64,'f'*64,'sidecar')
signed=Admission('55','9'*64,new)
boot='12345678-abcd-abcd-abcd-1234567890ab'
journal=(signed.run,signed.nonce,old,new)

def pair(phase):
    request=TerminalRequest(signed,old,phase)
    release=new if phase=='restart' else old
    a=Process(BASE,32,88,986,release)
    b=a if phase=='restart' else Process(GRAPH,33,89,986,release)
    port=8902 if phase=='restart' else 8901
    return request,Snapshot(release,a,b,((port,b.pid),(8903,a.pid)),journal,boot,(15,33))

class ForegroundTerminal(TestCase):
    def test_forward_and_inverse_use_fsync_before_exact_publication(self):
        for phase in ('restart','restore'):
            request,before=pair(phase)
            pre=observe_before_unlink(request,lambda _:before)
            self.assertEqual(restore_precommit(precommit_wire(pre),request),pre)
            after=replace(before,journal=None)
            fsync=Mock()
            def publish(**kwargs):
                fsync.assert_called_once_with()
                self.assertEqual(kwargs,dict(controller_pending=request,before=before,after=after))
                return 'settled'
            result,_=observe_after_unlink(request,pre,lambda _:after,fsync,publish)
            self.assertEqual(result,'settled')

    def test_fsync_failure_or_pid_change_cannot_publish(self):
        request,before=pair('restart')
        pre=observe_before_unlink(request,lambda _:before)
        after=replace(before,journal=None)
        publish=Mock()
        fsync=Mock(side_effect=OSError('durability'))
        with self.assertRaises(OSError):observe_after_unlink(request,pre,lambda _:after,fsync,publish)
        change=replace(after,private=replace(after.private,start_ticks=86),graph=replace(after.graph,start_ticks=86))
        with self.assertRaises(ValueError):
            observe_after_unlink(request,pre,Mock(side_effect=[after,after,change]),Mock(),publish)
        with self.assertRaises(ValueError):restore_precommit(precommit_wire(pre),pair('restore')[0])
        publish.assert_not_called()
