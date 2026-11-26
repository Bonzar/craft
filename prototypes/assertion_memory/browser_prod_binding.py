"""Canonical PROD Base browser listener: root profile plus sealed package capability."""
from functools import partial
from hashlib import sha256
import errno
import json
import os
from pathlib import Path
import pwd
import socket
import stat

from .queue_pause_binding import PROD, release_lease
from .queue_rest import _checked_file, _identity, exact_database

PROD_BROWSER_PORT=8902
PROD_PRIVATE_PORT=8903
PROD_OWNER_ORIGIN='https://d5dus2psgclvh5ulbtbh.fovt0b64.apigw.yandexcloud.net'
PROD_OWNER_STATE=PROD.root/'state'/'graph-state'
PROD_HTTP_CONFIG=PROD.root/'state'/'http.json'
PROD_SWITCH=PROD.authority/'base-browser-listener.json'
_SWITCH={'binding':'dedicated-base-browser-v1','stage':'prod','version':1,
         'retired_graph_unit':'assertion-production-deploy-graph.service'}
_LEGACY_GRAPH_PORT=8901
_CAPABILITY='BASE_BROWSER_LISTENER_CONTRACT'
_CAPABILITY_V1=b'assertion-memory-base-browser-listener-v1\n'
_MAIN='prototypes/assertion_memory/http_server.py'
_BINDING='prototypes/assertion_memory/browser_prod_binding.py'

def _capable_release(root):
    # _identity proves the exact loaded physical release and its sealed
    # identity files via existing release admission and manifest policy.
    _identity(root,'prod')
    path=(root/'current').resolve(strict=True)
    if Path(__file__).resolve()!=path/_BINDING:
        raise ValueError('Wrong physical browser binding package')
    manifest=_checked_file(path/'SHA256SUMS',5*1024*1024).decode('ascii').splitlines()
    def entries(name):
        return [line for line in manifest if line.partition('  ')[2] in (name,'./'+name)]
    marker=entries(_CAPABILITY)
    if not marker:
        try:
            (path/_CAPABILITY).lstat()
        except FileNotFoundError:
            return False
        raise ValueError('Unsealed browser capability marker')
    for name, limit in ((_CAPABILITY,128),(_MAIN,65536),(_BINDING,65536)):
        raw=_checked_file(path/name,limit)
        if entries(name)!=[sha256(raw).hexdigest()+'  ./'+name]:
            raise ValueError('Unverified Base browser capability member')
        if name==_CAPABILITY and raw!=_CAPABILITY_V1:
            raise ValueError('Unsupported Base browser listener contract')
    if (root/'current').resolve(strict=True)!=path:
        raise ValueError('Base release changed during browser startup')
    return True


def _read_switch(path):
    # Marker is written only by the separately approved release authority;
    # the app cannot create it or infer retirement from a free socket alone.
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    for parent in (path.parent,path.parent.parent):
        st=parent.lstat()
        if parent.resolve(strict=True)!=parent or not stat.S_ISDIR(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:
            raise ValueError('Untrusted PROD browser profile parent')
    def distinct(items):
        result=dict(items)
        if len(items)!=len(result):
            raise ValueError('Duplicate PROD browser profile key')
        return result
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or stat.S_IMODE(st.st_mode)!=0o644
                or st.st_nlink!=1 or not os.path.samestat(st,path.lstat())):
            raise ValueError('Untrusted PROD browser profile')
        raw=stream.read(513)
    if len(raw)>512:
        raise ValueError('Oversized PROD browser profile')
    payload=json.loads(raw,object_pairs_hook=distinct)
    if payload!=_SWITCH or type(payload['version']) is not int:
        raise ValueError('Unknown PROD browser profile')
    return True

def _require_legacy_graph_stopped():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        if probe.connect_ex(('127.0.0.1',_LEGACY_GRAPH_PORT))!=errno.ECONNREFUSED:
            raise ValueError('Legacy Graph lifecycle has not been retired')

def installed_prod_browser_lifespan(config_path, config, *, private_port, on_failure):
    if Path(config_path)!=PROD_HTTP_CONFIG:
        return None  # TEST and existing unscoped clients keep original startup.
    if not _read_switch(PROD_SWITCH):
        return None
    if PROD_HTTP_CONFIG.resolve(strict=True)!=PROD_HTTP_CONFIG:
        raise ValueError('Noncanonical PROD Base HTTP config')
    if not _capable_release(PROD.root):
        return None
    return configured_prod_browser_lifespan(config,private_port=private_port,
        interactive_enabled=None,on_failure=on_failure)

def configured_prod_browser_lifespan(config, *, private_port, interactive_enabled, on_failure=None):
    """Call only after protected legacy login-writer retirement is recorded."""
    if private_port != PROD_PRIVATE_PORT or Path(config.db) != PROD.db:
        raise ValueError('Browser endpoint requires exact PROD Base authority')
    uid=pwd.getpwnam(PROD.account).pw_uid
    if uid<=0 or os.geteuid()!=uid or os.getuid()!=uid:
        raise ValueError('Unexpected PROD Base writer identity')
    exact_database(PROD.db)
    _identity(PROD.root,'prod')  # Validate loaded bytes without taking the release lease.

    def lifespan(*, interactive_active=False):
        _require_legacy_graph_stopped()
        from apps.graph_viewer.server.app import create_app
        from .browser_listener import browser_listener
        from .browser_owner_binding import base_owner_browser
        from .browser_surface import browser_surface
        browser=base_owner_browser(stage='prod',config=config,
            verified_identity=partial(_identity,PROD.root,'prod'),
            release_lease=partial(release_lease,PROD,uid=uid),
            interactive_enabled=interactive_active if interactive_enabled is None else interactive_enabled)
        graph=create_app(PROD.db,PROD_OWNER_STATE,public_origin=PROD_OWNER_ORIGIN,
                         browser_owner_api=browser,existing_owner_only=True)
        return browser_listener(browser_surface(graph),PROD_BROWSER_PORT,on_failure=on_failure)

    return lifespan
