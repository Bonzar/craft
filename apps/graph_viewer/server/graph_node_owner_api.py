"""Browser proof for one explicitly selected graph thought; transport is injected."""
from contextvars import ContextVar
from .common import APIError
from starlette.responses import JSONResponse
from prototypes.assertion_memory import graph_node_removal as nodes
from prototypes.assertion_memory.processing_control import REVISION


_verified_base_commit = ContextVar('verified_browser_graph_commit', default=None)


def require_verified_base_commit(stage, args):
    """Assert the exact owner operation was consumed by the browser API."""
    expected = (stage, args['thought_id'], args['thought_version'], args['expected_revision'])
    if _verified_base_commit.get() != expected:
        raise APIError('csrf_denied', 'Exact owner graph confirmation required', 403)
    _verified_base_commit.set(None)


def bind_native_test_writer(browser, dashboard):
    """Use the same admitted TEST Base database and release authority as the owner queue."""
    import sqlite3
    from .browser_owner_api import BrowserOwnerAPI
    from .queue_dashboard import HostedQueueDashboard
    from prototypes.assertion_memory.processing_control import Conflict
    from prototypes.assertion_memory.queue_owner_domain import owner_operations

    if (type(browser) is not BrowserOwnerAPI or browser.stage != 'test' or browser.node_removal is not None
            or type(dashboard) is not HostedQueueDashboard or dashboard.stage != 'test'
            or not callable(dashboard.activation)):
        raise ValueError('Native node writer requires the existing canonical TEST owner binding')

    def identity():
        config = dashboard.metadata()
        return config['release_version'], config['generation']

    def perform(action, args):
        try:
            dispatch = owner_operations(database=dashboard.db, release_lease=dashboard.release_lease,
                                        identity=identity, stage='test')
            if action == 'graph_node_preview' and args.get('expected_revision') == '0'*32:
                return dispatch(action, {k: v for k, v in args.items() if k != 'expected_revision'})
            if action == 'graph_node_remove':
                def authorize(_db):
                    require_verified_base_commit('test', args)
                return dispatch(action, args, authorize=authorize)
            raise ValueError('Unknown native node owner operation')
        except nodes.NodeConflict:
            raise
        except Conflict:
            raise nodes.NodeConflict('node_confirmation_stale') from None
        except (OSError, ValueError, KeyError, sqlite3.Error):
            raise APIError('owner_api_unavailable', 'Graph writer authority unavailable', 503) from None

    browser.node_removal = perform


async def control_nodes(api, request, prepare, data, owner, token_headers):
    import asyncio
    fixed={'stage','thought_id','thought_version'}
    if set(data)!=(fixed if prepare else fixed|{'expected_revision'}) or data.get('stage')!=api.stage:
        raise APIError('invalid_request','Invalid graph node identity',400)
    try:
        nodes.check_request(data['thought_id'],data['thought_version'])
    except ValueError:
        raise APIError('invalid_request','Invalid graph node identity',400) from None
    args={k:data[k] for k in ('thought_id','thought_version')}
    base=(owner,api.stage,'graph-nodes/remove',args['thought_id'],args['thought_version'])
    try:
        if prepare:
            result=await asyncio.to_thread(api.node_removal,'graph_node_preview',
                                           dict(args,expected_revision='0'*32))
            return JSONResponse({**result,'csrf_token':api.tickets.issue((*base,result['revision']))})
        revision=data['expected_revision']
        if type(revision) is not str or not REVISION.fullmatch(revision):
            raise APIError('invalid_request','Invalid graph confirmation',400)
        if len(token_headers)!=1:
            raise APIError('csrf_denied','Action confirmation required',403)
        if request.app.state.owner.session(request) is None:
            raise APIError('owner_required','Owner session required',401)
        api.tickets.consume(token_headers[0],(*base,revision))
        grant=_verified_base_commit.set((api.stage,args['thought_id'],args['thought_version'],revision))
        try:
            result=await asyncio.to_thread(api.node_removal,'graph_node_remove',dict(args,expected_revision=revision))
        finally:
            _verified_base_commit.reset(grant)
        return JSONResponse(result)
    except nodes.NodeConflict as error:
        return JSONResponse({'error':error.code},status_code=409)
