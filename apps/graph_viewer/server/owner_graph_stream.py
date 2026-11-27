"""One authenticated, generation-fenced graph stream (baseline or delta)."""
import asyncio
import base64
from contextlib import aclosing, closing
import hmac
from hashlib import sha256
import json
import os
import re
import stat
import time

from starlette.responses import StreamingResponse
from .auth import COOKIE
from .common import APIError,selector
from .database_generation import epoch_name

WIRE='graph-v1'
NAMES=('nodes.bin','edges.bin','topics.json','identities.json')
CHUNK=48*1024
BASE_LIMIT=64*1024*1024
JSON_LIMIT=16*1024*1024
FRAME_LIMIT=96*1024
META_LIMIT=92*1024
DELTA_LIMIT=1024*1024
MAX_DELTAS=32
LIVE_SECONDS=90
POLL=.20
HEARTBEAT=5
REV=re.compile('[0-9a-f]{32}\\Z')
EPOCH=re.compile('[0-9a-f]{64}\\Z')
CURSOR=re.compile('0|[1-9][0-9]{0,19}')
SELECTOR_FIELDS={'mode','at','history','undated','session_id','run_id','output_snapshot_id'}
RESUME_FIELDS={'epoch','revision','after'}

def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')

def _frame(kind,payload=None):
    wire=b'event: '+kind.encode('ascii')+b'\ndata: '+_json(payload or {})+b'\n\n'
    if len(wire)>FRAME_LIMIT:raise ValueError('graph SSE frame too large')
    return wire

def _owner(request):
    return (getattr(request.app.state,'owner_graph_stream',False) is True
            and request.app.state.owner.session(request) is not None)

def _same_origin(request):
    site=request.headers.getlist('sec-fetch-site')
    origins=request.headers.getlist('origin')
    return (not request.headers.getlist('authorization')
            and (not site or site==['same-origin'])
            and (not origins or origins==[request.app.state.public_origin])
            and request.scope.get('raw_path')==b'/api/v1/events')

def session_scope(request, token=None):
    """Rotates on session change or process restart; never a bearer or password hash."""
    key=getattr(request.app.state,'owner_graph_stream_key',None)
    if getattr(request.app.state,'owner_graph_stream',False) is not True or not isinstance(key,bytes):
        return None
    # Caller supplies a new token from a just successful owner login, or the
    # already independently authenticated live cookie from the GET path.
    raw=token if token is not None else request.cookies.get(COOKIE)
    if not isinstance(raw,str) or len(raw)>512:
        return None
    manager=request.app.state.manager
    try:
        # Never consult an existing prepared graph or cached epoch here.
        generation=manager.sync_generation()
        if (type(generation) is not tuple or len(generation)!=3
                or any(type(n) is not int or n<0 for n in generation[:2])
                or not isinstance(generation[2],str) or not REV.fullmatch(generation[2])):
            return None
        epoch=epoch_name(generation)
    except (APIError,OSError,ValueError):
        return None
    scope=hmac.digest(key,b'graph-checkpoint-v1\0'+epoch.encode('ascii')+b'\0'+raw.encode('utf-8'),'sha256').hex()
    return scope,epoch

def graph_cache_fields(request,token=None):
    if getattr(request.app.state,'owner_graph_stream',False) is not True:
        return {}
    fields={'owner_live_graph_v1':True}
    cache=session_scope(request,token)
    if cache is None:
        return fields
    scope,epoch=cache
    fields.update(graph_cache_scope=scope,graph_cache_epoch=epoch)
    lane=getattr(getattr(request.app.state,'browser_owner_api',None),'stage',None)
    if lane in ('test','prod'):
        fields['graph_cache_stage']=lane
    return fields

def parse_start(request):
    pairs=request.query_params.multi_items()
    vals=dict(pairs)
    names=set(vals)
    if (len(vals)!=len(pairs) or len(request.scope.get('query_string',b''))>4096
            or vals.get('wire')!=WIRE or names-SELECTOR_FIELDS-RESUME_FIELDS-{'wire'}
            or request.headers.getlist('last-event-id')):
        raise APIError('invalid_graph_stream','Invalid graph stream selector or resume checkpoint',400)
    selected=selector(vals)
    present=names & RESUME_FIELDS
    if not present:return selected,None
    if (present!=RESUME_FIELDS or not REV.fullmatch(vals['revision'])
            or not EPOCH.fullmatch(vals['epoch']) or not CURSOR.fullmatch(vals['after'])):
        raise APIError('invalid_graph_stream','Invalid graph stream resume checkpoint',400)
    return selected,(vals['epoch'],vals['revision'],int(vals['after']))

def _pick(value,keys):
    if type(value) is not dict:
        raise ValueError('invalid graph projection object')
    return {k:value[k] for k in keys if k in value}

def projection_manifest(graph,epoch,cursor):
    m=_pick(graph.manifest,('schema','revision','source_revision','provider_id','captured_at','selector',
            'counts','nodes','edges','topics_url','bounds','layout','complete_graph',
            'delta_capabilities','status_at','knowledge_cutoff'))
    m.update(owner_live_epoch=epoch,live_cursor=str(cursor))
    for k in ('nodes','edges'):
        if k in m:m[k]=_pick(m[k],('url','format','stride','byte_length'))
    if 'layout' in m:m['layout']=_pick(m['layout'],('version','semantic','stable','extent','raw_bounds'))
    if 'session_filter' in graph.manifest and graph.manifest['session_filter'] is not None:
        m['session_filter']=_pick(graph.manifest['session_filter'],('session_id','unknown_origin_assertion_count',
                                  'policy','semantic_search_available'))
    return m

def _topic(t):
    # Only graph layout and reviewed editorial captions, never a question,
    # raw session/overview record, definition or fallback source title.
    d=_pick(t,('id','index','label_method','source_ref','position','radius','depth',
               'member_count','freshness','available_at','slice_member_count','session_filter_applied'))
    if 'source_ref' in d:
        d['source_ref']=_pick(d['source_ref'],('view_id','version_id','route_revision'))
    title=t.get('display_title') if t.get('naming_status')=='accepted' else None
    title=title if isinstance(title,str) and len(title)<=512 else 'Тема '+str(1+int(t['index']))
    d.update(label=title,full_title=title,purpose=None,overview_protocol=None)
    return d

def _topics_projection(value):
    out=_pick(value,('revision','presentation_snapshot','topics','layout_topics','layout_memberships',
                    'memberships','links','completeness','unknowns'))
    for key in ('topics','layout_topics'):
        if out.get(key) is not None:out[key]=[_topic(t) for t in out[key]]
    for key in ('memberships','layout_memberships'):
        if out.get(key) is not None:out[key]=[_pick(x,('topic_index','node_indices')) for x in out[key]]
    if out.get('links') is not None:
        out['links']=[{**_pick(x,('parent','child','relation')),'provenance':{}} for x in out['links']]
    if out.get('unknowns') is not None:
        out['unknowns']=[x for x in out['unknowns'] if isinstance(x,str) and re.fullmatch('[a-z0-9_]{1,80}',x)]
    if out.get('presentation_snapshot') is not None:
        out['presentation_snapshot']=_pick(out['presentation_snapshot'],('presentation_cutoff','name_watermark'))
    return out

def _delta_projection(payload,target,epoch,num):
    out=_pick(payload,('cursor','base_revision','revision','source_revision','captured_at',
        'remove_node_ordinals','remove_edge_ordinals','upsert_nodes','upsert_edges','status_changes','topic_changes'))
    for key,allowed in (('upsert_nodes',('id','ordinal','position','flags')),
                        ('upsert_edges',('id','ordinal','from','to')),
                        ('status_changes',('id','ordinal','flags','status'))):
        if key in out:out[key]=[_pick(x,allowed) for x in out[key]]
    for status in out.get('status_changes',[]):
        status['status']=_pick(status['status'],('disposition','unresolved_conflict','replacements',
                                               'basis_edge_ids','effective_at','effectiveness'))
    if 'topic_changes' in out:
        cleaned=[]
        for t in out['topic_changes']:
            if t.get('schema')=='source-membership/v1':
                patch=_pick(t,('schema','topics','memberships','completeness','unknowns'))
                patch['topics']=[_topic(x) for x in patch.get('topics',[])]
                patch['memberships']=[_pick(x,('topic_id','topic_index','added_node_ordinals',
                                              'removed_node_ordinals')) for x in patch.get('memberships',[])]
                cleaned.append(patch)
            else:cleaned.append(_topic(t))
        out['topic_changes']=cleaned
    out['manifest']=projection_manifest(target,epoch,num)
    out['epoch']=epoch
    return out

def retained(manager,selection,revision,after,epoch):
    """Atomic in event-loop context. Return target and chain or fallback reset.
    No disk cache restore, private fields or unbounded response assembly.
    """
    latest=manager.latest.get(manager.key(selection))
    end=manager.cursor
    initial=manager.graphs.get(revision)
    if (latest is None or initial is None or initial.manifest['selector']!=selection
            or after>end or after<0):
        return None
    if selection['mode']=='known_then':
        return (revision,end,[])
    if revision==latest:
        return (revision,end,[])
    log=tuple(manager.journal)
    if not log or after<log[0][0]-1:
        return None
    tip=revision
    events=[]
    budget=0
    for num,kind,payload in log:
        if num<=after:continue
        if kind=='reset' and payload.get('reason')=='database_cleared':
            return None
        if kind not in ('delta','reset'):
            continue
        target=manager.graphs.get(payload.get('revision'))
        if target is None:
            # The missing observation's membership cannot be verified.
            return None
        if target.manifest['selector']!=selection:
            continue
        if kind!='delta' or payload.get('base_revision')!=tip or payload.get('cursor')!=str(num):
            return None
        payload=_delta_projection(payload,target,epoch,num)
        raw=_json(payload)
        if len(raw)>FRAME_LIMIT-128:return None
        budget+=len(raw)
        if budget>DELTA_LIMIT or len(events)>=MAX_DELTAS:
            return None
        events.append(payload)
        tip=target.revision
    return (tip,end,events) if tip==latest else None

def _files(graph,name):
    root=graph.directory
    path=root/name
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        cap=JSON_LIMIT if name=='topics.json' else BASE_LIMIT
        if not stat.S_ISREG(st.st_mode) or st.st_size>cap or not os.path.samestat(st,path.lstat()):
            raise ValueError('unexpected immutable snapshot bytes')
        count=0
        while part:=os.read(fd,CHUNK):
            count+=len(part)
            if count>st.st_size:raise ValueError('snapshot file replaced')
            yield part
        end=os.fstat(fd)
        if count!=st.st_size or end.st_mtime_ns!=st.st_mtime_ns or not os.path.samestat(end,path.lstat()):
            raise ValueError('snapshot file changed')
    finally:os.close(fd)

def _topics(graph):
    data=b''.join(_files(graph,'topics.json'))
    value=_topics_projection(json.loads(data))
    if value.get('revision')!=graph.revision:
        raise ValueError('topic projection revision changed')
    raw=_json(value)
    if len(raw)>JSON_LIMIT:raise ValueError('topic projection too large')
    for start in range(0,len(raw),CHUNK):yield raw[start:start+CHUNK]

def _identity_page(graph,offset):
    with closing(graph.connect()) as db:
        return db.execute('SELECT ordinal,id FROM nodes WHERE ordinal>=? ORDER BY ordinal LIMIT 128',(offset,)).fetchall()

def _identities(graph):
    count=0
    buffer=bytearray(b'[')
    while page:=_identity_page(graph,count):
        for ordinal,id_ in page:
            if (ordinal!=count or not isinstance(id_,str) or not 0<len(id_)<=1024):
                raise ValueError('invalid snapshot identity ordinal')
            val=(b',' if count else b'')+_json(id_)
            count+=1
            buffer.extend(val)
            if len(buffer)>=CHUNK:
                yield bytes(buffer[:CHUNK]);del buffer[:CHUNK]
    buffer.extend(b']')
    yield bytes(buffer)

def _next(iterator):
    return next(iterator,None)

def _close_after_read(task,iterator):
    if not task.cancelled():
        task.exception()
    iterator.close()

async def baseline(manager,gen,graph,cursor,is_valid):
    epoch=epoch_name(gen); revision=graph.revision
    manifest=projection_manifest(graph,epoch,cursor)
    if len(_json(manifest))>META_LIMIT:
        raise ValueError('snapshot manifest too large')
    if not await is_valid():
        return
    yield _frame('snapshot-start',dict(epoch=epoch,revision=revision,cursor=str(cursor),manifest=manifest))
    meta={}; total=0
    for name in NAMES:
        iterator=(_identities(graph) if name=='identities.json' else
                  _topics(graph) if name=='topics.json' else _files(graph,name))
        closing_deferred=False
        try:
            offset=0;sha=sha256()
            while await is_valid():
                read=asyncio.create_task(asyncio.to_thread(_next,iterator))
                try:
                    part=await asyncio.shield(read)
                except asyncio.CancelledError:
                    # Never close a generator from the loop while its next()
                    # is executing in the worker. No further frame is sent.
                    closing_deferred=True
                    read.add_done_callback(lambda task, it=iterator: _close_after_read(task,it))
                    raise
                if not await is_valid():
                    return
                if part is None:break
                total+=len(part)
                if (total>BASE_LIMIT or len(part)>CHUNK or
                        name=='identities.json' and offset+len(part)>JSON_LIMIT):
                    raise ValueError('snapshot body too large')
                sha.update(part)
                yield _frame('snapshot-chunk',dict(revision=revision,name=name,offset=offset,
                                                  base64=base64.b64encode(part).decode('ascii')))
                offset+=len(part)
            else:return
        finally:
            if not closing_deferred:iterator.close()
        meta[name]={'bytes':offset,'sha256':sha.hexdigest()}
    if await is_valid():
        yield _frame('snapshot-end',dict(epoch=epoch,revision=revision,cursor=str(cursor),segments=meta))

async def owner_graph_events(request):
    if not _owner(request) or not _same_origin(request):
        raise APIError('unauthorized','Same-origin owner session required',401)
    selected,resume=parse_start(request)
    if request.method!='GET':
        raise APIError('method_not_allowed','Graph data stream requires GET',405)
    slots=request.app.state.owner_snapshot_slots
    if slots.locked():
        raise APIError('rate_limited','Owner stream busy',429)
    manager=request.app.state.manager
    async def frames():
        async with slots:
            end=time.monotonic()+LIVE_SECONDS
            ping=time.monotonic()
            generation=None;revision=None;cursor=0;announced=False
            pending=resume
            async def allowed():
                return (time.monotonic()<end and not manager.closed
                        and not await request.is_disconnected()
                        and await asyncio.to_thread(_owner,request))
            try:
                while await allowed():
                    current=manager.sync_generation()
                    if current is None:
                        yield _frame('unavailable',{'reason':'missing_epoch'})
                        return
                    epoch=epoch_name(current)
                    if current!=generation:
                        if generation is not None:
                            yield _frame('reset',dict(epoch=epoch,reason='epoch_changed'))
                            pending=None
                        generation=current;revision=None;announced=False
                        yield _frame('hello',dict(protocol=WIRE,epoch=epoch))
                        if pending is not None:
                            if pending[0]==epoch:
                                revision,cursor=pending[1:]
                            if revision is None or retained(manager,selected,revision,cursor,epoch) is None:
                                revision=None
                                yield _frame('reset',dict(epoch=epoch,reason='checkpoint_expired'))
                            pending=None
                    if revision is None:
                        graph=await manager.ensure(selected)
                        if graph is not None:
                            # In the same event-loop turn as reading the
                            # immutable graph, pin the cut before any I/O.
                            revision,cursor=graph.revision,manager.cursor
                            async def exact():
                                return await allowed() and manager.sync_generation()==generation
                            async with aclosing(baseline(manager,generation,graph,cursor,exact)) as chunks:
                                async for frame in chunks:
                                    yield frame
                            if not await exact():
                                revision=None
                                continue
                    else:
                        chain=retained(manager,selected,revision,cursor,epoch)
                        if chain is None:
                            revision=None;announced=False
                            yield _frame('reset',dict(epoch=epoch,reason='checkpoint_expired'))
                            continue
                        next_revision,after,events=chain
                        change=(next_revision,after)!=(revision,cursor)
                        for event in events:
                            if not await allowed():return
                            if manager.sync_generation()!=generation:
                                break
                            yield _frame('delta',{**event,'epoch':epoch})
                        else:
                            if change or not announced:
                                if not await allowed() or manager.sync_generation()!=generation:
                                    continue
                                yield _frame('caught-up',dict(epoch=epoch,revision=next_revision,cursor=str(after)))
                                revision,cursor=next_revision,after
                                announced=True
                    if time.monotonic()-ping>=HEARTBEAT:
                        yield _frame('heartbeat')
                        ping=time.monotonic()
                    await asyncio.sleep(POLL)
            except (APIError,ValueError,OSError):
                if await allowed():
                    yield _frame('unavailable',{'reason':'graph_stream_unavailable'})
    return StreamingResponse(frames(),media_type='text/event-stream',
        headers={'Cache-Control':'private, no-store, no-transform','X-Accel-Buffering':'no'})
