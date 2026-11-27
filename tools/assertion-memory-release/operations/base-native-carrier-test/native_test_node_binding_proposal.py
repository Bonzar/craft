"""TEST receiver-to-native-Base composition seam; not a listener or operator."""
from dataclasses import dataclass
from pathlib import Path
import os
import pwd

from starlette.responses import JSONResponse

from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.owner_snapshot_release_policy import enabled
from apps.graph_viewer.server.queue_dashboard import configured_browser_dashboard
from prototypes.assertion_memory.dashboard_projection import projection_path
from apps.graph_viewer.server.existing_owner_store import ExistingOwnerStore
from apps.graph_viewer.server.graph_node_test_binding import TEST_GRAPH_ORIGIN
from prototypes.assertion_memory.browser_owner_binding import base_owner_browser
from prototypes.assertion_memory.browser_surface import browser_surface
from prototypes.assertion_memory.queue_pause_binding import TEST, release_lease
from prototypes.assertion_memory.queue_rest import exact_database


@dataclass(frozen=True)
class ProtectedTestBinding:
    """Only the admitted local release controller may construct this evidence."""
    stage: str
    mode: str
    current_scope: tuple
    expected_uid: int
    physical_release: tuple  # revision/archive, distinct from dashboard tag/generation
    existing_direct_origin: str | None = None  # Same fixed upstream already observed by receiver.


def build_existing_test_surface(config, *, private_port, carrier_port, admitted, runtime_identity):
    """A pure composition entry point; caller owns handoff under the release lease.

    A staged probe reopens the existing owner store but mounts no node writer.
    The prepared app has no bind/listen, startup side-effects/config switch or
    replacement for the receiver's trust. Production cannot use this seam.
    """
    uid=pwd.getpwnam(TEST.account).pw_uid
    if (type(admitted) is not ProtectedTestBinding or admitted.stage!='test'
            or admitted.mode not in ('shadow','carrier')
            or uid<=0 or uid!=os.getuid() or uid!=os.geteuid()
            or type(admitted.expected_uid) is not int or admitted.expected_uid!=uid
            or private_port!=TEST.port or carrier_port!=8891 or Path(config.db)!=TEST.db
            or not callable(runtime_identity)):
        raise ValueError('Wrong fixed TEST native owner binding')
    exact_database(TEST.db)
    state=TEST.root/'state'/'graph-state'
    def same_runtime():
        current=runtime_identity()
        if current!=admitted.current_scope:
            raise ValueError('Observed TEST owner authority changed')
        return current
    same_runtime()
    # Explicit readonly verification before securing a browser app. No
    # password, mcp-token or graph writer bearer is read or supplied here.
    ExistingOwnerStore(state)
    dashboard=configured_browser_dashboard('test',projection_path('test'),TEST.db)
    if dashboard.publisher_uid!=uid or dashboard.activation()!=admitted.physical_release:
        raise ValueError('Existing TEST queue release diverges from admitted scope')
    browser=base_owner_browser(stage='test',config=config,verified_identity=same_runtime,
        release_lease=lambda:release_lease(TEST,uid=uid),interactive_enabled=False,
        node_writer_enabled=admitted.mode=='carrier',test_dashboard_live=True)
    candidate=browser_surface(create_app(TEST.db,state,public_origin=TEST_GRAPH_ORIGIN,
        browser_owner_api=browser,existing_owner_only=True,synthetic_fixture=True,
        existing_test_dashboard=dashboard,owner_snapshot_events=enabled(dashboard=dashboard,browser=browser),
        test_direct_events_origin=admitted.existing_direct_origin))

    async def shadow(scope, receive, send):
        if scope['type']=='http' and scope['method'] not in ('GET','HEAD'):
            return await JSONResponse({'error':'not_found'},status_code=404,
                headers={'Cache-Control':'no-store'})(scope,receive,send)
        return await candidate(scope,receive,send)

    return shadow if admitted.mode=='shadow' else candidate
