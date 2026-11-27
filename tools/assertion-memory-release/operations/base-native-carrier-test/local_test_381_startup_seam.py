"""Synthetic call graph: normal TEST startup cannot reach a PROD/native sidecar."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from apps.graph_viewer.server import graph_node_test_binding as graph
from apps.graph_viewer.server.__main__ import configure_owner_lanes
from prototypes.assertion_memory import browser_prod_binding as prod


class OrdinaryTestSeam(TestCase):
    def test_stock_test_private_startup_cannot_probe_or_open_prod_owner(self):
        with (patch.object(prod,'_read_switch') as switch,
              patch.object(prod,'_capable_release') as qualified,
              patch.object(prod,'configured_prod_browser_lifespan') as activate):
            self.assertIsNone(prod.installed_prod_browser_lifespan(
                graph.TEST.root/'state'/'http.json',SimpleNamespace(db=graph.TEST.db),
                private_port=8893,on_failure=lambda:None))
        for guard in (switch,qualified,activate):
            guard.assert_not_called()

    def test_normal_graph_test_never_receives_native_writer(self):
        args=SimpleNamespace(queue_status_config=graph.TEST.root/'projection.json',queue_status_stage='test',
            queue_control=False,browser_owner_clear=False,browser_owner_api=True,
            public_origin=graph.TEST_GRAPH_ORIGIN,bridge_read_token_file=None,
            database_clear_read_token_file=None,database_clear_lane=None,allowed_proxy_host=[],
            test_direct_events_origin=None,synthetic_fixture=True,host='127.0.0.1',port=8891,
            source=graph.TEST.db,browser_owner_graph_node_removal=False)
        lane=SimpleNamespace(stage='test',db=graph.TEST.db)
        with (patch('apps.graph_viewer.server.queue_dashboard.configured_browser_dashboard',return_value=lane),
              patch('apps.graph_viewer.server.browser_owner_api.installed_test_clear_profile',return_value=None),
              patch('apps.graph_viewer.server.device_test_binding.installed_test_device_binding',return_value=None),
              patch('prototypes.assertion_memory.browser_owner_binding.base_owner_browser') as native,
              patch.object(graph,'installed_test_graph_node_bridge',side_effect=ValueError('missing fixture authority')) as bridge):
            _,browser=configure_owner_lanes(args)
            self.assertIsNone(browser.node_removal)
            bridge.assert_not_called()
            args.browser_owner_graph_node_removal=True
            with self.assertRaisesRegex(ValueError,'missing fixture authority'):
                configure_owner_lanes(args)
            native.assert_not_called()
            bridge.assert_called_once_with(lane)
