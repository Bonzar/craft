from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from native_boundary import _boundary
from release_core import PENDING, activate, swap
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission, Release

class NativeBoundary(TestCase):
    def fixture(self,root):
        manifest=b'fictional sealed manifest'
        old=Release('1'*40,'2'*64,sha256(manifest).hexdigest(),'legacy')
        new=Release('4'*40,'5'*64,sha256(manifest).hexdigest(),'sidecar')
        paths=[]
        for release in (old,new):
            item=root/'releases'/(release.revision+'-'+release.archive)
            item.mkdir(parents=True)
            (item/'.prepared-v1').write_bytes(b'ok\n')
            (item/'SHA256SUMS').write_bytes(manifest)
            paths.append(item)
        swap(root,paths[0])
        return old,new,Admission('42','7'*64,new),paths[1]

    def test_actual_native_success_observed_before_and_after_unlink(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            old,new,admit,candidate=self.fixture(root)
            req=TerminalRequest(admit,old,'restart')
            seen=[]
            activate(root,candidate,None,lambda _,action,path:seen.append((action,_boundary(root,req))))
            self.assertEqual([(action,s[1] is not None) for action,s in seen],
                             [('restart',True),('smoke',True)])
            self.assertEqual(_boundary(root,req),(new,None))

    def test_native_rollback_selects_old_only_and_rejects_tampered_manifest(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            old,new,admit,candidate=self.fixture(root)
            req=TerminalRequest(admit,old,'restore'); seen=[]
            def fail(_,phase,path):
                if phase=='restart':
                    raise RuntimeError('disposable fixture')
                seen.append((phase,_boundary(root,req)))
            with self.assertRaises(RuntimeError):
                activate(root,candidate,None,fail)
            self.assertEqual([p for p,_ in seen],['restore','smoke'])
            self.assertEqual(_boundary(root,req),(old,None))
            (root/'current'/'SHA256SUMS').write_bytes(b'forged')
            with self.assertRaises(ValueError):
                _boundary(root,req)

    def test_symlink_or_wrong_journal_never_counts_as_absent(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            old,new,admit,_=self.fixture(root)
            req=TerminalRequest(admit,old,'restore')
            pend=root/PENDING
            pend.symlink_to(root/'nonexistent')
            with self.assertRaises(OSError):
                _boundary(root,req)
            pend.unlink()
            pend.write_text('{"old":null,"new":"spoof"}')
            with self.assertRaises(ValueError):
                _boundary(root,req)
