import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from forward_release_receiver import FamilyLocks, Request, activate_forward
from forward_release_train import Reject
import test_forward_release_checkpoints as fixture_module
from test_forward_release_checkpoints import witness


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
