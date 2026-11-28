from types import SimpleNamespace
import unittest

from apps.graph_viewer.server import owner_snapshot_release_policy as policy
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from prototypes.assertion_memory.base_browser_surface import LANES


class OwnerSnapshotReleasePolicy(unittest.TestCase):
    def binding(self, stage):
        lane = LANES[stage]
        return dict(
            stage=stage,
            browser=BrowserOwnerAPI(stage=stage, perform=lambda *a: None,
                                    queue_snapshot=lambda *a: None),
            graph_source=lane.base.db,
            public_origin=lane.origin,
        )

    def test_same_build_accepts_both_verified_lane_bindings(self):
        for stage in ('test', 'prod'):
            with self.subTest(stage=stage):
                self.assertTrue(policy.enabled(**self.binding(stage)))

    def test_other_lane_dependencies_do_not_enable_stream(self):
        for stage, other in (('test', 'prod'), ('prod', 'test')):
            args, wrong = self.binding(stage), self.binding(other)
            for field in ('browser', 'graph_source', 'public_origin'):
                with self.subTest(stage=stage, field=field):
                    self.assertFalse(policy.enabled(**(args | {field: wrong[field]})))

    def test_malformed_or_unconfigured_bindings_fail_closed(self):
        for edit in ({'browser': None}, {'browser': SimpleNamespace(stage='test')},
                     {'graph_source': None}, {'public_origin': None}, {'stage': 'unknown'}):
            with self.subTest(edit=edit):
                self.assertFalse(policy.enabled(**(self.binding('test') | edit)))
