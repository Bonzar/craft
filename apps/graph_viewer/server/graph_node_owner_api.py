"""Browser proof for one explicitly selected graph thought; transport is injected."""
from contextvars import ContextVar
from .common import APIError
from starlette.responses import JSONResponse
from prototypes.assertion_memory import graph_node_removal as nodes
from prototypes.assertion_memory.processing_control import REVISION


_verified_base_commit = ContextVar('verified_browser_graph_commit', default=None)


def require_verified_base_commit(stage, args):
    """Assert this call came from this exact already-consumed owner action."""
    expected = (stage,args['thought_id'],args['thought_version'],args['expected_revision'])
    if _verified_base_commit.get() != expected:
        raise APIError('csrf_denied','Exact owner graph confirmation required',403)
    _verified_base_commit.set(None)


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
