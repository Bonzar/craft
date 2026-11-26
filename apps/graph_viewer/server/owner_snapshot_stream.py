"""Same-origin owner metadata stream, sequence fenced to the current database epoch.

Public /events without query can opt into this path; existing query-bearing
revision-specific SSE and the signed TEST direct transport remain unchanged.
"""
import asyncio
import json
import time

from starlette.responses import Response, StreamingResponse
from .common import APIError
from .database_generation import epoch_name

HEARTBEAT_SECONDS=5
LIVE_SECONDS=90


def enabled_owner(request):
    return (getattr(request.app.state,'owner_snapshot_events',False) is True
            and request.app.state.owner.session(request) is not None)


def owner_frame(kind,payload=None):
    return f'event: {kind}\ndata: {json.dumps(payload or {},separators=(",",":"))}\n\n'


async def owner_snapshot_events(request):
    if not enabled_owner(request):
        raise APIError('unauthorized','Owner session required',401)
    if request.scope.get('raw_path')!=b'/api/v1/events' or request.url.query or request.headers.getlist('last-event-id'):
        raise APIError('invalid_request','Unexpected owner stream selector or cursor',400)
    headers={'Cache-Control':'private, no-store','X-Accel-Buffering':'no'}
    if request.method=='HEAD':
        return Response(media_type='text/event-stream',headers=headers)
    manager=request.app.state.manager
    generation=manager.sync_generation()
    if generation is None:
        raise APIError('graph_unavailable','Database epoch not available',503)
    slots=request.app.state.owner_snapshot_slots
    if slots.locked():
        raise APIError('rate_limited','Owner stream busy',429)
    async def content():
        async with slots:
            if not await asyncio.to_thread(enabled_owner,request) or manager.sync_generation()!=generation:
                return
            cursor=manager.cursor
            start=ping=time.monotonic()
            yield owner_frame('hello',{'epoch':epoch_name(generation)})
            yield owner_frame('reset') # First authoritative epoch-bound snapshot request.
            while not manager.closed and time.monotonic()-start<LIVE_SECONDS:
                if await request.is_disconnected() or not await asyncio.to_thread(enabled_owner,request):
                    return
                if manager.sync_generation()!=generation:
                    yield owner_frame('reset')
                    return
                unseen=[(number,kind) for number,kind,_ in list(manager.journal) if number>cursor]
                if unseen:
                    if unseen[0][0]>cursor+1 or any(kind in {'delta','reset','queue_changed'} for _,kind in unseen):
                        yield owner_frame('changed')
                    cursor=unseen[-1][0]
                if time.monotonic()-ping>=HEARTBEAT_SECONDS:
                    yield owner_frame('heartbeat')
                    ping=time.monotonic()
                await asyncio.sleep(.4)
    return StreamingResponse(content(),media_type='text/event-stream',headers=headers)
