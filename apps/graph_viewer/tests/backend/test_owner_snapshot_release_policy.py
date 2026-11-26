from types import SimpleNamespace
import unittest
from unittest.mock import patch

from apps.graph_viewer.server import owner_snapshot_release_policy as policy


class OwnerSnapshotReleasePolicy(unittest.TestCase):
    def test_same_release_enables_verified_test_only(self):
        test = SimpleNamespace(stage='test')
        prod = SimpleNamespace(stage='prod')
        for a, b, expected in ((test,test,True),(prod,prod,False),
                               (test,prod,False),(test,None,False),
                               (None,test,False),(None,None,False)):
            with self.subTest(a=a,b=b):
                self.assertIs(policy.enabled(dashboard=a,browser=b),expected)
        with self.assertRaises(ValueError):
            policy.enabled(dashboard=prod,browser=prod,explicit_test=True)

    def test_unselected_release_and_explicit_test(self):
        test = SimpleNamespace(stage='test')
        with patch.object(policy,'OWNER_STREAM_TEST',False):
            self.assertFalse(policy.enabled(dashboard=test,browser=test))
            self.assertTrue(policy.enabled(dashboard=test,browser=test,explicit_test=True))
        with self.assertRaises(ValueError):
            policy.enabled(dashboard=test,browser=test,explicit_test='true')
