from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import feature_plan as f
import test_device_plan
import test_graph_plan
import test_transaction
from transaction import execute, Refused


class JoinedPlan(TestCase):
    def test_exact_planners_apply_and_inverse_via_same_seam(self):
        for plan, fixture in ((f.graph, test_graph_plan.Tests().fixture),
                              (f.devices, test_device_plan.DeviceTestPlan().profile)):
            with self.subTest(plan=plan), TemporaryDirectory() as tmp:
                data,pins = fixture()
                req,b = plan(data,pins,'f'*64)
                runner=test_transaction.RecordingTestRunner(Path(tmp),req)
                approve=test_transaction.LocalTransactions().verified
                applied=execute(req,b,approve(req),runner)
                wrong=type(applied)(**(vars(applied)|{'current':'/elsewhere'}))
                with self.assertRaises(Refused):
                    f.inverse(req,b,verified_after=wrong,receipt_id='e'*64)
                inv,payload=f.inverse(req,b,verified_after=applied,receipt_id='e'*64)
                reverted=execute(inv,payload,approve(inv),runner)
                self.assertIsNone(reverted.existing_digest)
                self.assertEqual(reverted.registry_identity,applied.registry_identity)
                self.assertEqual(len(runner.events),2)
