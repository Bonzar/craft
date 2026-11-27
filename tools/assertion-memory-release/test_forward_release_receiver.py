from dataclasses import replace
import multiprocessing as mp
import sqlite3
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from forward_release_checkpoints import Checkpoints, Published
from forward_release_receiver import FamilyLocks, Request, activate_forward
from forward_release_train import FixApproval, Ledger, Reject
import test_forward_release_checkpoints as fixture_module
from test_forward_release_checkpoints import witness
from test_forward_release_train import art, cand


class ReceiverTest(unittest.TestCase):
    def test_proof_and_lock_fail_closed(self):
        fixture=fixture_module.CheckpointTests()
        fixture.setUp()
        a=fixture.a
        effect=Mock(side_effect=lambda a:witness('test',a))
        r=Request(a,'test',88,Path('/dev/null'))
        signed=dict(schema='forward-release-stage/v1',repository_id=1260097465,
                    family='base',stage='test',tag=a.candidate.tag,revision=a.candidate.revision,
                    release_id=a.release_id,workflow_run_id=88,payload_sha256=a.digest,test_generation=None)
        def execute(stage, claim):
            return activate_forward(fixture.q,r,receiver_stage=stage,verify_dispatch=lambda req,g: claim == signed and req.artifact == a and g is None,
                   verify_release=fixture.lookup,active_test=None,verify_promote_approval=None,
                   activate_and_probe=effect,live_prod=lambda _:fixture.base,verify_fix=None)
        with self.assertRaises(Reject):
            execute('prod',signed)
        with self.assertRaises(Reject):
            execute('test',signed|{'payload_sha256':'f'*64})
        effect.assert_not_called()
        self.assertEqual(execute('test',signed).artifact,a)
        effect.assert_called_once()

        with tempfile.TemporaryDirectory() as d:
            locks=FamilyLocks(d,os.getuid())
            with locks('base'):
                with locks('visual'):
                    with self.assertRaises(BlockingIOError), locks('base'):
                        pass
                with self.assertRaises(Reject), locks('unexpected'):
                    pass

    def test_prepared_shared_hook_checks_before_native_switch(self):
        from forward_release_receiver import protected_activation
        fixture=fixture_module.CheckpointTests()
        fixture.setUp()
        a=fixture.a
        r=Request(a,'test',99,Path('/dev/null'))
        switch=Mock()
        probe=Mock(side_effect=lambda stage,x:witness(stage,x))
        hook=protected_activation(fixture.q,r,receiver_stage='test',
               verify_dispatch=lambda req,g:req==r and g is None,
               live_prod=lambda _:fixture.base,verify_release=fixture.lookup,
               identify_prepared=lambda p:a if p=='candidate-path' else None,probe=probe)
        with self.assertRaises(Reject):
            hook('wrong-prepared-path',switch)
        switch.assert_not_called()
        probe.assert_not_called()
        self.assertEqual(hook('candidate-path',switch).artifact,a)
        switch.assert_called_once()
        probe.assert_called_once_with('test',a)


def open_controller(root):
    connection=sqlite3.connect(Path(root)/'ledger.sqlite')
    return Checkpoints(Ledger(connection,lambda:1000),FamilyLocks(root,os.getuid()))


def identities():
    baseline=art(cand('0.1.36','a'),1)
    first=art(cand('1.0.0','b'),2)
    replacement=replace(art(cand('1.1.0','c'),3),digest='f'*64)
    return baseline,first,replacement


def publication(artifact):
    return Published(artifact.candidate,artifact.release_id,True)


def tester(root, attempt_started, release, outcome):
    q=open_controller(root)
    _,old,new=identities()
    proof=FixApproval(old,new.candidate,4,5,'d'*64,'open_major_defect_only')
    def effect(a):
        attempt_started.set()
        if not release.wait(8):
            raise RuntimeError('test synchronization failed')
        return witness('test',a,g='f')
    try:
        q.test(new,verified_release=lambda _:publication(new),activate_and_probe=effect,
               live_prod=None,verify_fix=lambda _:proof)
    except BaseException as e:
        outcome.send(type(e).__name__)
    else:
        outcome.send('candidate-caused-one-native-effect')


class ProcessRace(unittest.TestCase):
    def test_competing_old_prod_new_test_and_exact_replay(self):
        ctx=mp.get_context('fork')
        with tempfile.TemporaryDirectory() as root:
            q=open_controller(root)
            baseline,old,new=identities()
            visual=art(cand('0.1.16','a','visual'),20)
            q.ledger.initialize(witness('prod',baseline))
            q.ledger.initialize(witness('prod',visual))
            earlier=q.test(old,verified_release=lambda _:publication(old),
                   activate_and_probe=lambda a:witness('test',a),live_prod=lambda _:witness('prod',baseline),
                   verify_fix=None)
            ready,release=ctx.Event(),ctx.Event()
            reader,writer=ctx.Pipe(duplex=False)
            child=ctx.Process(target=tester,args=(root,ready,release,writer))
            child.start()
            writer.close()
            effects=[]
            def no_effect(a):
                effects.append(a)
                return witness('prod',a)
            def old_prod():
                return q.production(old,verified_release=lambda _:publication(old),actual_test=lambda _:earlier,
                                   approve=lambda _:True,activate_and_probe=no_effect)
            try:
                self.assertTrue(ready.wait(8),'separate TEST receiver did not reach switch')
                with self.assertRaises(BlockingIOError):
                    old_prod()
                # Independent family is not serialized behind Base.
                v=art(cand('1.0.0','e','visual'),21)
                self.assertEqual(q.test(v,verified_release=lambda _:publication(v),live_prod=lambda _:witness('prod',visual),
                                 verify_fix=None,activate_and_probe=lambda a:witness('test',a)).artifact,v)
                release.set()
                self.assertTrue(reader.poll(8))
                self.assertEqual(reader.recv(),'candidate-caused-one-native-effect')
                with self.assertRaises(Reject):
                    old_prod()
                current=witness('test',new,g='f')
                replay=q.test(new,verified_release=lambda _:publication(new),live_prod=None,
                              verify_fix=None,activate_and_probe=no_effect,observe_test=lambda _:current)
                self.assertEqual(replay,current)
                self.assertEqual(effects,[])
                q.production(new,verified_release=lambda _:publication(new),actual_test=lambda _:current,
                             approve=lambda _:True,activate_and_probe=no_effect)
                self.assertEqual(effects,[new])
            finally:
                release.set()
                child.join(8)
                if child.is_alive():
                    child.terminate()
                    child.join()
                reader.close()
            self.assertEqual(child.exitcode,0)
