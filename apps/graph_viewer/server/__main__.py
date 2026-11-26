"""Local CLI. Password travels only through environment or a private file."""
import argparse
import os
from pathlib import Path
import uvicorn
from .app import create_app,validate_static_directory


def main():
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
                        help='Enable the Base-owned same-origin browser API on the canonical TEST Graph listener')
    parser.add_argument('--browser-owner-clear',action='store_true',
                        help='Enable the verified owner clear action; requires the browser API and remains off by default')
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
    queue_dashboard=None
    if bool(args.queue_status_config)!=bool(args.queue_status_stage):
        parser.error('queue status config and stage must be explicitly paired')
    if args.queue_control and (args.queue_status_config is None or args.queue_status_stage!='test'):
        parser.error('queue control requires the explicit standard TEST owner web app')
    if args.browser_owner_api and (args.queue_status_stage!='test' or not args.public_origin
            or args.queue_control or args.bridge_read_token_file or args.database_clear_read_token_file
            or args.database_clear_lane or args.allowed_proxy_host):
        parser.error('Browser owner API requires the canonical TEST-only HTTPS Graph profile without legacy bridge authority')
    if args.browser_owner_clear and not args.browser_owner_api:
        parser.error('Browser owner clear requires the browser API')
    if args.queue_status_config is not None:
        from .queue_dashboard import HostedQueueDashboard, _test_activation, TEST_DEPLOY_ROOT
        try:
            owner_control=args.queue_control or args.browser_owner_api
            queue_dashboard=HostedQueueDashboard(args.queue_status_stage,args.queue_status_config,args.source,
                activation=_test_activation if owner_control else None,
                control_lock=TEST_DEPLOY_ROOT/'deploy.lock' if owner_control else None)
            if owner_control:
                if os.getuid()!=queue_dashboard.publisher_uid or os.geteuid()!=queue_dashboard.publisher_uid:
                    raise ValueError('Queue control requires the canonical TEST account')
                _test_activation()
        except (ValueError, KeyError) as error:
            parser.error(str(error))
    browser=None
    if args.browser_owner_api:
        from .browser_owner_api import configured_browser_api
        try:
            browser=configured_browser_api(queue_dashboard,clear_enabled=args.browser_owner_clear)
        except (OSError,ValueError,KeyError) as error:
            parser.error(str(error))
    app=create_app(args.source,args.state_dir,password=password,provider_id=args.provider_id,static_dir=args.static_dir,semantic_model=model,synthetic_fixture=args.synthetic_fixture,public_origin=args.public_origin,allowed_proxy_hosts=args.allowed_proxy_host,bridge_read_token_file=args.bridge_read_token_file,queue_dashboard=queue_dashboard,queue_control=args.queue_control,database_clear_lane=args.database_clear_lane,database_clear_read_token_file=args.database_clear_read_token_file,test_direct_events_origin=args.test_direct_events_origin,browser_owner_api=browser)
    uvicorn.run(app,host=args.host,port=args.port,access_log=False,proxy_headers=False,server_header=False)


if __name__=='__main__':
    main()
