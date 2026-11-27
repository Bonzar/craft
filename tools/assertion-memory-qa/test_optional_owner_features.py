"""Two synthetic owner-feature checks: no sockets, live lanes or deployment."""
import argparse
import socket
from unittest import defaultTestLoader, TextTestRunner
from unittest.mock import patch

CASES = {
    'graph-node-removal': (
        'apps.graph_viewer.tests.backend.test_graph_node_owner_api.BrowserNodeBoundaryTest.test_node_button_capability_and_real_owner_browser_commit',
        'apps.graph_viewer.tests.backend.test_graph_node_test_startup.GraphNodeGuardIntegration.test_boot_composed_dashboard_guard_commit_and_provenance_conflict',
        'prototypes.assertion_memory.tests.test_graph_node_removal',
    ),
    'device-registry': (
        'apps.graph_viewer.tests.backend.test_device_owner_http',
        'prototypes.assertion_memory.tests.test_machine_device_registry',
        'test_optional_machine_catalog',
    ),
}


def forbidden_connect(*_args, **_kwargs):
    raise AssertionError('Synthetic owner feature checks must not connect to a network socket')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feature', choices=sorted(CASES))
    args = parser.parse_args()
    with (patch.object(socket.socket, 'connect', forbidden_connect),
          patch.object(socket.socket, 'connect_ex', forbidden_connect),
          patch.object(socket, 'create_connection', forbidden_connect)):
        tests = defaultTestLoader.loadTestsFromNames(CASES[args.feature])
        if tests.countTestCases() < len(CASES[args.feature]):
            raise SystemExit('Synthetic feature fixture missing')
        raise SystemExit(not TextTestRunner(verbosity=2).run(tests).wasSuccessful())
