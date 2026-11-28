"""Owner stream availability follows the verified browser/graph binding."""
from pathlib import Path


def enabled(*, browser, stage, graph_source, public_origin):
    """Call after owner composition; the stream checks session and epoch per request."""
    from .browser_owner_api import BrowserOwnerAPI
    from prototypes.assertion_memory.base_browser_surface import LANES

    lane = LANES.get(stage)
    admitted = (
        lane is not None
        and type(browser) is BrowserOwnerAPI and browser.stage == stage
        and isinstance(graph_source, (str, Path)) and Path(graph_source) == lane.base.db
        and public_origin == lane.origin
    )
    return admitted
