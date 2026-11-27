"""Versioned release selection for the existing owner stream; no host settings."""

# Set by release source review, not by a browser/request/environment input.
# TEST and PROD install the same bytes. A later reviewed release can add PROD
# only after its independently validated ordinary ingress witness.
OWNER_STREAM_TEST = True
OWNER_STREAM_PROD = False


def enabled(*, dashboard, browser, explicit_test=False):
    """Call only after configure_owner_lanes validates the protected binding."""
    scoped_test = (getattr(dashboard, 'stage', None) == 'test'
                   and getattr(browser, 'stage', None) == 'test')
    scoped_prod = (getattr(dashboard, 'stage', None) == 'prod'
                   and getattr(browser, 'stage', None) == 'prod')
    if type(explicit_test) is not bool or explicit_test and not scoped_test:
        raise ValueError('Owner snapshot flag requires the protected TEST owner lane')
    return ((scoped_test and (OWNER_STREAM_TEST or explicit_test))
            or (scoped_prod and OWNER_STREAM_PROD))


def graph_v1_enabled(*, dashboard, browser, public_origin, explicit_test=False):
    """The graph data stream never inherits legacy metadata stream enablement."""
    if type(explicit_test) is not bool:
        raise ValueError('Expected explicit owner graph-v1 flag')
    if not explicit_test:
        return False
    from .browser_owner_api import BrowserOwnerAPI
    from .graph_node_test_binding import TEST_GRAPH_ORIGIN
    from .queue_dashboard import HostedQueueDashboard
    if (type(dashboard) is not HostedQueueDashboard or dashboard.stage != 'test'
            or type(browser) is not BrowserOwnerAPI or browser.stage != 'test'
            or public_origin != TEST_GRAPH_ORIGIN):
        raise ValueError('Owner graph-v1 requires the protected same-origin TEST browser binding')
    return True
