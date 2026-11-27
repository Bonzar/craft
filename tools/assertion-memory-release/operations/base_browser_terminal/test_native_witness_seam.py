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
            key=self.pre
            _boundary(root,key) # Authenticate exact precommit context before fsync.
            return lambda:self.publish(root,key)

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

    def test_fsync_fault_after_unlink_does_not_restore_and_redrives_durability(self):
        from unittest.mock import patch
        for phase in ('restart','restore'):
            with self.subTest(phase=phase), TemporaryDirectory() as directory:
                root=Path(directory)
                a,candidate=self.prepare(root)
                hooks=[]; syncs=[]
                verified_sync=core.sync_dir
                def hook(_,action,loc):
                    hooks.append(action)
                    if action=='restart' and phase=='restore':
                        raise ValueError('native switching fixture')
                def fail_after_unlink(path):
                    if path==root and a.pre is not None and not (root/core.PENDING).exists():
                        syncs.append('failed')
                        raise OSError('fixture fsync fault')
                    return verified_sync(path)
                with patch.object(core,'sync_dir',side_effect=fail_after_unlink):
                    with self.assertRaises(core.ActivationWitnessPublicationPending):
                        core.activate(root,candidate,None,hook,witness=a.witness())
                self.assertEqual(syncs,['failed'])
                self.assertFalse((root/core.PENDING).exists())
                self.assertIsNone(a.final)
                before=hooks.copy()
                original=a.publish
                def assert_order(path,key):
                    self.assertEqual(syncs,['failed','durable'])
                    return original(path,key)
                def verify_sync(path):
                    syncs.append('durable')
                    return verified_sync(path)
                with patch.object(a,'publish',side_effect=assert_order), patch.object(core,'sync_dir',side_effect=verify_sync):
                    core.recover_activation(root,None,hook,witness=a.witness())
                self.assertEqual(hooks,before)
                self.assertEqual(a.final,a.candidate if phase=='restart' else a.old)

    def test_opt_in_denies_bootstrap_before_native_mutation(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            a,candidate=self.prepare(root)
            core.swap(root,None)
            def unexpected(*args):
                self.fail('native mutation attempted without predecessor')
            with self.assertRaisesRegex(ValueError,'requires_verified_predecessor'):
                core.activate(root,candidate,None,unexpected,witness=a.witness())
            self.assertIsNone(core.current_target(root))
            self.assertFalse((root/core.PENDING).exists())
            core.record_activation(root,None,candidate)
            with self.assertRaisesRegex(ValueError,'requires_verified_predecessor'):
                core.recover_activation(root,None,unexpected,witness=a.witness())
            self.assertTrue((root/core.PENDING).exists())

    def test_deploy_does_not_run_failed_candidate_retention_after_unlink(self):
        from unittest.mock import Mock, patch
        with TemporaryDirectory() as directory:
            root=Path(directory)
            a,candidate=self.prepare(root)
            retention=Mock(stores=['fixture'])
            actual_sync=core.sync_dir
            def broken(path):
                if path==root and a.pre is not None and not (root/core.PENDING).exists():
                    raise OSError('fixture durability failure')
                return actual_sync(path)
            hooks=[]
            with (patch.object(core,'prepare'),
                  patch.object(core,'unpack',return_value=candidate),
                  patch.object(core,'sync_dir',side_effect=broken)):
                with self.assertRaises(core.ActivationWitnessPublicationPending):
                    core.deploy(None,a.candidate.archive,root=root,spec=None,
                        validate=lambda _:candidate.name,
                        hook=lambda action,loc:hooks.append(action),
                        witness_factory=lambda _:a.witness(),retention_factory=lambda _:retention)
            self.assertEqual(hooks,['restart','smoke'])
            retention.failed.assert_not_called()
            retention.sweep.assert_not_called()
