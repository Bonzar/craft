"""Local CLI. Password travels only through environment or a private file."""
import argparse
import os
from pathlib import Path
import uvicorn
from .app import create_app,validate_static_directory


PROD_OWNER_CREDENTIALS = Path('/run/credentials/assertion-base-prod-browser-graph.service')


def installed_prod_owner_api(dashboard):
    """Use only the credential directory of the existing protected service."""
    if os.environ.get('CREDENTIALS_DIRECTORY')!=str(PROD_OWNER_CREDENTIALS):
        raise ValueError('PROD browser owner API requires canonical systemd credentials')
    try:
        from .prod_owner_bridge import configured_prod_owner_bridge
    except ModuleNotFoundError as error:
        if error.name=='apps.graph_viewer.server.prod_owner_bridge':
            raise ValueError('PROD private Base writer adapter is not installed') from None
        raise
    from .browser_owner_api import configured_browser_api
    bridge=configured_prod_owner_bridge(PROD_OWNER_CREDENTIALS)
    return configured_browser_api(dashboard,clear_enabled=False,prod_owner_bridge=bridge)


def configure_owner_lanes(args, *, prod_owner_api=None):
    """Opt-in lane binding; the read-only PROD Graph never constructs local effects."""
    if bool(args.queue_status_config)!=bool(args.queue_status_stage):
        raise ValueError('queue status config and stage must be explicitly paired')
    if args.queue_control and (args.queue_status_config is None or args.queue_status_stage!='test'):
        raise ValueError('queue control requires the explicit standard TEST owner web app')
    if args.browser_owner_clear and not args.browser_owner_api:
        raise ValueError('Browser owner clear requires the browser API')
    if args.browser_owner_api:
        from .auth import validate_public_origin
        from .queue_dashboard import configured_browser_dashboard
        if (args.queue_status_stage not in ('test','prod') or args.queue_status_config is None
                or not validate_public_origin(args.public_origin)
                or args.queue_control or args.bridge_read_token_file or args.database_clear_read_token_file
                or args.database_clear_lane or args.allowed_proxy_host):
            raise ValueError('Browser owner API requires the canonical HTTPS Graph lane without legacy bridge authority')
        if args.queue_status_stage=='prod':
            if args.test_direct_events_origin or args.synthetic_fixture:
                raise ValueError('PROD browser owner API forbids TEST transport and fixtures')
            if args.browser_owner_clear:
                raise ValueError('PROD browser clear needs a separately verified writer capability')
            if not callable(prod_owner_api):
                raise ValueError('PROD browser owner API requires the installed private Base writer adapter')
        dashboard=configured_browser_dashboard(args.queue_status_stage,args.queue_status_config,args.source)
        if args.queue_status_stage=='prod':
            from .browser_owner_api import BrowserOwnerAPI
            browser=prod_owner_api(dashboard)
            if type(browser) is not BrowserOwnerAPI or browser.stage!='prod' or browser.clear_enabled:
                raise ValueError('Wrong private Base writer authority')
        else:
            from .browser_owner_api import configured_browser_api, installed_test_clear_profile
            profile=installed_test_clear_profile()
            if args.browser_owner_clear and profile is None:
                raise ValueError('A verified TEST clear profile is required')
            browser=(configured_browser_api(dashboard,clear_enabled=False) if profile is None
                     else configured_browser_api(dashboard,clear_profile=profile))
            from .device_test_binding import installed_test_device_binding
            device_binding=installed_test_device_binding(dashboard)
            if device_binding is not None:
                registry,identity=device_binding
                browser.attach_machine_devices(registry,dashboard.release_lease,identity)
        return dashboard,browser
    if args.queue_control:
        from .queue_dashboard import configured_browser_dashboard
        return configured_browser_dashboard('test',args.queue_status_config,args.source),None
    if args.queue_status_config is not None:
        from .queue_dashboard import HostedQueueDashboard
        return HostedQueueDashboard(args.queue_status_stage,args.queue_status_config,args.source),None
    return None,None


def main(*, prod_owner_api=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8791)
    parser.add_argument('--host',default='127.0.0.1',choices=['127.0.0.1','::1'])
    parser.add_argument('--provider-id',default='assertion-memory')
    parser.add_argument('--password-file',type=Path)
    parser.add_argument('--embedding-model')
    parser.add_argument('--embedding-cache',type=Path)
    parser.add_argument('--synthetic-fixture',action='store_true')
    parser.add_argument('--test-direct-events-origin',help='Optional existing HTTPS/IP route, synthetic TEST only')
    parser.add_argument('--public-origin',help='Expected HTTPS browser origin; forwarded headers remain untrusted')
    parser.add_argument('--allowed-proxy-host',action='append',default=[],help='Explicit upstream bridge hostname; repeatable, independent of browser origin')
    parser.add_argument('--bridge-read-token-file',type=Path,help='Owner-only high-entropy read service credential file; no write or session authority')
    parser.add_argument('--database-clear-lane',choices=['test','prod'],help='Scope an optional private reset readiness proof')
    parser.add_argument('--database-clear-read-token-file',type=Path,help='Owner-only credential for the exact reset-readiness GET only')
    parser.add_argument('--queue-status-config',type=Path,
                        help='Optional canonical lane-local Base scheduler projection (no secrets)')
    parser.add_argument('--queue-status-stage',choices=['test','prod'])
    parser.add_argument('--browser-owner-api',action='store_true',
                        help='Enable the Base-owned same-origin browser API on the canonical lane-local Graph listener')
    parser.add_argument('--browser-owner-clear',action='store_true',
                        help='Compatibility flag: require an independently provisioned TEST clear profile')
    parser.add_argument('--queue-control',action='store_true',help='Enable owner-session-only processing pause on the standard web app')
    parser.add_argument('--static-dir',type=Path,default=Path(__file__).resolve().parents[1]/'web'/'dist')
    args=parser.parse_args()
    try:
        validate_static_directory(args.static_dir,args.source,args.state_dir,
                                  args.password_file,args.bridge_read_token_file,args.embedding_cache,args.queue_status_config)
    except ValueError as error:
        parser.error(str(error))
    password=os.environ.get('GRAPH_VIEWER_PASSWORD')
    if args.password_file:
        if args.password_file.stat().st_mode & 0o077:
            parser.error('Password file must be owner-readable only (chmod 600)')
        password=args.password_file.read_text().rstrip('\r\n')
    if not password and not (args.state_dir/'viewer.sqlite3').is_file():
        parser.error('Set GRAPH_VIEWER_PASSWORD or supply --password-file for initial owner setup')
    model=None
    if args.embedding_model:
        if args.embedding_cache is None:
            parser.error('--embedding-cache is required with --embedding-model')
        from .semantic import FastEmbedQueryModel
        model=FastEmbedQueryModel(args.embedding_model,args.embedding_cache)
    try:
        queue_dashboard,browser=configure_owner_lanes(args,prod_owner_api=prod_owner_api or installed_prod_owner_api)
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.error(str(error))
    app=create_app(args.source,args.state_dir,password=password,provider_id=args.provider_id,static_dir=args.static_dir,semantic_model=model,synthetic_fixture=args.synthetic_fixture,public_origin=args.public_origin,allowed_proxy_hosts=args.allowed_proxy_host,bridge_read_token_file=args.bridge_read_token_file,queue_dashboard=queue_dashboard,queue_control=args.queue_control,database_clear_lane=args.database_clear_lane,database_clear_read_token_file=args.database_clear_read_token_file,test_direct_events_origin=args.test_direct_events_origin,browser_owner_api=browser)
    uvicorn.run(app,host=args.host,port=args.port,access_log=False,proxy_headers=False,server_header=False)


if __name__=='__main__':
    main()
