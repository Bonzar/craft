import unittest
from pathlib import Path
from unittest.mock import patch
from starlette.testclient import TestClient

from apps.graph_viewer.server import app as appmod
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.server.graph_node_test_binding import TEST_GRAPH_ORIGIN
from apps.graph_viewer.server.owner_snapshot_release_policy import enabled,graph_v1_enabled
from apps.graph_viewer.server.queue_dashboard import HostedQueueDashboard


def bound(lane):
    dashboard=object.__new__(HostedQueueDashboard)
    dashboard.stage=lane
    browser=BrowserOwnerAPI(stage=lane,perform=lambda *a:None,queue_snapshot=lambda *a:None)
    return dashboard,browser


class GraphDataReleasePolicy(unittest.TestCase):
    def test_legacy_test_metadata_does_not_implicitly_admit_graph_v1(self):
        dashboard,browser=bound('test')
        self.assertTrue(enabled(dashboard=dashboard,browser=browser))
        self.assertFalse(graph_v1_enabled(dashboard=dashboard,browser=browser,
                                          public_origin=TEST_GRAPH_ORIGIN))
        with patch.object(appmod,'OwnerStore'),patch.object(appmod,'GraphManager'), \
                patch.object(appmod,'source_for_path'):
            app=appmod.create_app(Path('/fixture/source'),Path('/fixture/state'),
                                  public_origin=TEST_GRAPH_ORIGIN,queue_dashboard=dashboard,
                                  browser_owner_api=browser,owner_snapshot_events=True)
        self.assertTrue(app.state.owner_snapshot_events)
        self.assertFalse(app.state.owner_graph_stream)
        self.assertFalse(hasattr(app.state,'owner_graph_stream_key'))
        client=TestClient(app,base_url=TEST_GRAPH_ORIGIN)
        self.assertEqual(client.get('/api/v1/events?wire=graph-v1').status_code,401)

    def test_explicit_flag_accepts_only_same_origin_protected_test_pair(self):
        test,browser=bound('test')
        prod,pbrowser=bound('prod')
        self.assertTrue(graph_v1_enabled(dashboard=test,browser=browser,
                          public_origin=TEST_GRAPH_ORIGIN,explicit_test=True))
        for d,b,o in ((prod,pbrowser,TEST_GRAPH_ORIGIN),(test,pbrowser,TEST_GRAPH_ORIGIN),
                      (None,None,TEST_GRAPH_ORIGIN),(test,browser,'https://wrong.invalid')):
            with self.subTest(origin=o,lane=getattr(d,'stage',None)),self.assertRaises(ValueError):
                graph_v1_enabled(dashboard=d,browser=b,public_origin=o,explicit_test=True)
        with self.assertRaises(ValueError):
            graph_v1_enabled(dashboard=test,browser=browser,public_origin=TEST_GRAPH_ORIGIN,
                             explicit_test='true')
        with self.assertRaises(ValueError):
            appmod.create_app(Path('/fixture/source'),Path('/fixture/state'),
                public_origin=TEST_GRAPH_ORIGIN,queue_dashboard=prod,
                browser_owner_api=pbrowser,owner_graph_stream=True)
