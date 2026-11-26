"""Trusted Base launcher seam; the stock launcher does not enable it."""
from functools import partial
import os
from pathlib import Path
import pwd

from .browser_listener import browser_listener
from .browser_owner_binding import base_owner_browser
from .browser_surface import browser_surface
from .queue_pause_binding import PROD, release_lease
from .queue_rest import _identity, exact_database

PROD_BROWSER_PORT=8902
PROD_PRIVATE_PORT=8903
PROD_OWNER_ORIGIN='https://d5dus2psgclvh5ulbtbh.fovt0b64.apigw.yandexcloud.net'
PROD_OWNER_STATE=PROD.root/'state'/'graph-state'

def configured_prod_browser_lifespan(config, *, private_port, interactive_enabled):
    """Call only after protected legacy login-writer retirement is recorded."""
    if private_port != PROD_PRIVATE_PORT or Path(config.db) != PROD.db:
        raise ValueError('Browser endpoint requires exact PROD Base authority')
    uid=pwd.getpwnam(PROD.account).pw_uid
    if uid<=0 or os.geteuid()!=uid or os.getuid()!=uid:
        raise ValueError('Unexpected PROD Base writer identity')
    exact_database(PROD.db)
    _identity(PROD.root,'prod')  # Validate loaded bytes without taking the release lease.

    def lifespan():
        from apps.graph_viewer.server.app import create_app
        browser=base_owner_browser(stage='prod',config=config,
            verified_identity=partial(_identity,PROD.root,'prod'),
            release_lease=partial(release_lease,PROD,uid=uid),
            interactive_enabled=interactive_enabled)
        graph=create_app(PROD.db,PROD_OWNER_STATE,public_origin=PROD_OWNER_ORIGIN,
                         browser_owner_api=browser,existing_owner_only=True)
        return browser_listener(browser_surface(graph),PROD_BROWSER_PORT)

    return lifespan
