"""Fault probes in disposable paths using the checked-in release controller."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import release_core as core
import test_native_boundary as fixture
from native_boundary import _boundary
from terminal_lifecycle import TerminalRequest

class Attestor:
    """Test-only in-memory CAS; not a production authorizer."""
    def __init__(self, old, candidate, admission):
        self.old,self.candidate,self.admission=old,candidate,admission
        self.pre=None
        self.final=None
        self.failure=None

    def relation(self, root, phase, old, candidate):
        request=TerminalRequest(self.admission,self.old,phase)
        if (old.name!=self.old.revision+'-'+self.old.archive
                or candidate.name!=self.candidate.revision+'-'+self.candidate.archive):
            raise ValueError('foreign release')
        return request,_boundary(root,request)

    def before(self,root,phase,old,candidate):
        key,(_,journal)=self.relation(root,phase,old,candidate)
        if journal is None or self.final is not None:
            raise ValueError('no matching pending transaction')
        if self.failure=='deny-restart' and phase=='restart':
            raise ValueError('unverified candidate')
        if self.pre is not None and self.pre!=key:
            if not (self.pre.phase=='restart' and phase=='restore'
                    and self.pre.admission==key.admission
                    and self.pre.predecessor==key.predecessor):
                raise ValueError('compare-and-set denied')
        self.pre=key

    def publish(self, root, key):
        exact,journal=_boundary(root,key)
        if journal is not None or self.pre!=key or self.final is not None:
            raise ValueError('publication CAS denied')
        if self.failure=='after-fsync':
            raise RuntimeError('attestor disconnected')
        self.final=exact

    def after(self,root,phase,old,candidate):
        key,_=self.relation(root,phase,old,candidate)
        self.publish(root,key)

    def recover(self,root):
        if self.pre is not None and self.final is None:
            self.publish(root,self.pre)

    def witness(self):
        return core.ActivationWitness(self.before,self.after,self.recover)

class NativeWitness(TestCase):
    def prepare(self,root):
        old,new,admit,candidate=fixture.NativeBoundary().fixture(root)
        return Attestor(old,new,admit),candidate

    def test_native_after_fsync_fault_recovers_only_cas_on_fresh_observation(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            a,candidate=self.prepare(root)
            a.failure='after-fsync'
            hooks=[]
            def hook(_,phase,loc):
                hooks.append((phase,loc))
            with self.assertRaises(core.ActivationWitnessPublicationPending):
                core.activate(root,candidate,None,hook,witness=a.witness())
            self.assertEqual([x[0] for x in hooks],['restart','smoke'])
            self.assertFalse((root/core.PENDING).exists())
            self.assertEqual(core.current_target(root),str(candidate))
            a.failure=None
            core.recover_activation(root,None,hook,witness=a.witness())
            self.assertEqual(a.final,a.candidate)
            self.assertEqual([x[0] for x in hooks],['restart','smoke'])
            with self.assertRaises(ValueError):
                a.publish(root,a.pre)

    def test_preunlink_candidate_verification_failure_uses_original_restore(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            a,candidate=self.prepare(root)
            a.failure='deny-restart'
            hooks=[]
            with self.assertRaisesRegex(ValueError,'unverified candidate'):
                core.activate(root,candidate,None,lambda _,phase,loc:hooks.append(phase),
                              witness=a.witness())
            self.assertEqual(hooks,['restart','smoke','restore','smoke'])
            self.assertEqual(a.final,a.old)
            self.assertFalse((root/core.PENDING).exists())

    def test_absent_journal_alone_has_no_authority_or_native_effects(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            a,_=self.prepare(root)
            def unexpected(*args):
                self.fail('native mutation repeated')
            core.recover_activation(root,None,unexpected,witness=a.witness())
            self.assertIsNone(a.final)

    def test_restore_supersedes_uncommitted_candidate_after_exact_native_pointer_swap(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            a,candidate=self.prepare(root)
            old=Path(core.current_target(root))
            core.record_activation(root,str(old),candidate)
            core.swap(root,candidate)
            a.before(root,'restart',old,candidate)
            hooks=[]
            core.recover_activation(root,None,lambda _,phase,loc:hooks.append((phase,loc)),
                                    witness=a.witness())
            self.assertEqual(hooks,[('restore',old),('smoke',old)])
            self.assertEqual(a.final,a.old)
            self.assertEqual(a.pre.phase,'restore')
