"""Base owns one browser service and one canonical application DB per lane."""
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .queue_pause_binding import TEST, PROD
from .queue_rest import exact_database


@dataclass(frozen=True)
class BrowserLane:
    base: object
    origin: str
    private_port: int


LANES=MappingProxyType({
    'test':BrowserLane(TEST,'https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net',8893),
    'prod':BrowserLane(PROD,'https://d5dus2psgclvh5ulbtbh.fovt0b64.apigw.yandexcloud.net',8903),
})


def canonical_lane(stage, *, config, private_port):
    """No Graph source argument, alternate URL or browser-selected stage."""
    lane=LANES.get(stage)
    if lane is None or type(private_port) is not int or private_port!=lane.private_port or Path(config.db)!=lane.base.db:
        raise ValueError('Browser must use its exact canonical Base lane')
    exact_database(lane.base.db)
    return lane



def canonical_browser_owner(stage, *, source_hint=None, origin_hint=None):
    """Legacy installed argv may assert expected values, not select them."""
    lane=LANES.get(stage)
    if (lane is None or (source_hint is not None and Path(source_hint)!=lane.base.db)
            or (origin_hint is not None and origin_hint!=lane.origin)):
        raise ValueError('Base-owned browser component received a different DB or origin')
    return lane


def base_graph_component(stage, state, *, browser, dashboard, source_hint=None, origin_hint=None, **opts):
    """Existing Base-packaged browser component; no separately chosen source."""
    from apps.graph_viewer.server.app import create_app
    from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
    lane=canonical_browser_owner(stage,source_hint=source_hint,origin_hint=origin_hint)
    if (type(browser) is not BrowserOwnerAPI or browser.stage!=stage
            or (dashboard is not None and
                (dashboard.stage!=stage or Path(dashboard.db)!=lane.base.db))
            or set(opts)&{'source','source_path','public_origin','browser_owner_api'}):
        raise ValueError('Browser projection and writer are not the same Base lane')
    return create_app(lane.base.db,state,public_origin=lane.origin,browser_owner_api=browser,**opts)

def base_browser_app(stage, *, config, private_port, verified_identity, release_lease,
                     interactive, machine_devices=None, test_dashboard=None):
    """Composition only; caller owns authorization, OS and listener custody."""
    from apps.graph_viewer.server.browser_owner_api import configured_browser_api
    from apps.graph_viewer.server.queue_dashboard import HostedQueueDashboard
    from apps.graph_viewer.server.owner_snapshot_release_policy import enabled
    from .browser_owner_binding import base_owner_browser
    from .browser_surface import browser_surface

    lane=canonical_lane(stage,config=config,private_port=private_port)
    state=lane.base.root/'state'/'graph-state'  # existing owner store and materialized graph cache, not DB
    if test_dashboard is not None:
        if (stage!='test' or type(test_dashboard) is not HostedQueueDashboard
                or test_dashboard.stage!='test' or test_dashboard.db!=lane.base.db
                or not callable(test_dashboard.activation)
                or machine_devices is not None):
            raise ValueError('Wrong existing TEST Base scheduler projection')
        browser=configured_browser_api(test_dashboard,ordinary_test_clear=True)
    else:
        if stage=='test':
            raise ValueError('Canonical TEST Base scheduler projection required')
        browser=base_owner_browser(stage=stage,config=config,verified_identity=verified_identity,
            release_lease=release_lease,interactive_enabled=interactive,
            **({} if machine_devices is None else {'machine_devices':machine_devices}))
    graph=base_graph_component(stage,state,browser=browser,dashboard=test_dashboard,existing_owner_only=True,
                     native_test_dashboard=test_dashboard,
                     owner_snapshot_events=enabled(dashboard=test_dashboard,browser=browser))
    return browser_surface(graph)
