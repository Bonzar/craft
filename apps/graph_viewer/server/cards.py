"""Immutable cards, literal fragment marks, full saved source and adjacency."""
import json
from urllib.parse import quote
from .common import APIError, bounded, cursor_encode, cursor_decode, digest, timestamp


def node_row(c, id=None, ordinal=None):
    row=c.execute('SELECT * FROM nodes WHERE '+('id=?' if id is not None else 'ordinal=?'),(id if id is not None else ordinal,)).fetchone()
    if row is None or row['flags']&4:
        raise APIError('not_found','Assertion is unavailable in this perspective',404)
    return row


def incident(c, graph, row, params):
    visible=params.get('visible','1')!='0'; revision=graph.revision
    limit=bounded(params.get('limit'),200,2000,1)
    binding=f'edges:{revision}:{row["id"]}:{visible}'
    offset=cursor_decode(params.get('cursor'),binding)
    clause='(e.f=? OR e.t=?) AND a.flags&4=0 AND b.flags&4=0'
    if visible and not graph.manifest['selector']['history']:
        clause+=' AND a.flags&3=0 AND b.flags&3=0'
    base=' FROM edges e JOIN nodes a ON a.ordinal=e.f JOIN nodes b ON b.ordinal=e.t WHERE '+clause
    values=(row['ordinal'],row['ordinal'])
    total=c.execute('SELECT count(*)'+base,values).fetchone()[0]
    rows=c.execute('SELECT e.id,e.ordinal,e.f,e.t,e.kind,a.id AS from_id,b.id AS to_id'+base+' ORDER BY e.ordinal LIMIT ? OFFSET ?',(*values,limit,offset)).fetchall()
    items=[{'id':r['id'],'ordinal':r['ordinal'],'from_id':r['from_id'],'to_id':r['to_id'],'from_ordinal':r['f'],'to_ordinal':r['t'],'kind':r['kind']} for r in rows]
    return {'revision':revision,'node_id':row['id'],'visible_only':visible,'total':total,'items':items,
            'next_cursor':cursor_encode(binding,offset+len(items)) if offset+len(items)<total else None}


def source_refs(c, metadata):
    refs=list(metadata.get('evidence_refs') or [])
    if metadata.get('event_id') and metadata.get('source_span'):
        span=metadata['source_span']
        if isinstance(span,list) and len(span)==2:
            span={'start':span[0],'end':span[1]}
        if isinstance(span,dict):
            refs.insert(0,dict(span,event_id=metadata['event_id']))
    output=[]; seen=set()
    for ref in refs:
        if not isinstance(ref,dict):
            continue
        event=ref.get('event_id'); start=ref.get('start'); end=ref.get('end')
        key=(event,start,end)
        if not event or key in seen:
            continue
        seen.add(key)
        stored=c.execute('SELECT body FROM sources WHERE id=?',(event,)).fetchone()
        total=len(json.loads(stored[0])['prompt']) if stored else 0
        valid=type(start) is int and type(end) is int and 0<=start<end<=total
        output.append({'record_id':event,'start':start,'end':end,'offset_unit':'unicode_codepoints',
                       'available':stored is not None and valid,'context_truncated':bool(metadata.get('context_truncated'))})
    return output


def node_card(c, graph, row, params):
    body=json.loads(row['body']); meta=body['metadata']; text=body['text']
    offset=bounded(params.get('offset'),0,len(text)); limit=bounded(params.get('limit'),12000,64000,1)
    end=min(len(text),offset+limit)
    links=c.execute('SELECT n.id,s.id AS edge_id,n.valid_at FROM status_links s JOIN nodes n ON n.ordinal=s.f WHERE s.t=? ORDER BY n.ordinal',(row['ordinal'],)).fetchall()
    page=incident(c,graph,row,{'limit':'20','visible':'1'})
    total=incident(c,graph,row,{'limit':'1','visible':'0'})['total']
    visible=page['total']
    valid=body.get('valid_at'); status_at=graph.manifest['status_at']
    effectiveness='unknown' if not valid else 'future' if timestamp(valid)>status_at else 'effective'
    topic_indices=[r[0] for r in c.execute('SELECT topic FROM memberships WHERE node=?',(row['ordinal'],))]
    topics=graph.topics()['topics']
    canonical=dict(meta.get('canonical_thought') or {})
    if canonical:
        selected=graph.manifest['selector'].get('session_id')
        canonical['session_context_only']=selected is not None and selected.split(':',1)[1] not in canonical['session_ids']
    member_topics = [topics[index] for index in topic_indices]
    if canonical:
        member_topics = [topic for topic in member_topics
                         if topic.get('source_ref', {}).get('source_version_id') != row['id']]
    return {**canonical,
            'id':row['id'],'ordinal':row['ordinal'],'revision':graph.revision,'text_revision':digest(text),
            'text':text[offset:end],'text_offset':offset,'text_total':len(text),'next_text_offset':end if end<len(text) else None,
            'actor':{'id':body.get('actor'),'display_name':None},'source':body.get('source'),'episode':body.get('episode'),'scope':body.get('scope'),
            'time':{'valid_at':valid,'valid_precision':meta.get('temporal_precision','unknown'),'recorded_at':body.get('recorded_at'),
                    'source_recorded_at':meta.get('source_recorded_at'),'source_available_at':meta.get('source_available_at'),
                    'interpreted_at':(meta.get('interpretation') or {}).get('interpreted_at')},
            'status':{'disposition':row['disposition'],'unresolved_conflict':bool(row['conflict']),'replacements':[r['id'] for r in links],
                      'basis_edge_ids':[r['edge_id'] for r in links],'effective_at':max((r['valid_at'] for r in links if r['valid_at']),default=None),
                      'effectiveness':effectiveness},'sources':source_refs(c,meta),'topic_ids':[topic['id'] for topic in member_topics],
            'incident_edges':dict(page,total=total,visible_total=visible,url=f'/api/v1/nodes/{quote(row["id"],safe="")}/edges?revision={graph.revision}&visible=1'),
            'unknowns':[] if meta.get('event_id') else ['saved_source_reference_unknown']}


def fragment(text, quote_text):
    spans=[]; start=0
    if not isinstance(quote_text,str) or not quote_text:
        return {'quote':quote_text,'spans':[],'offset_unit':'unicode_codepoints','status':'missing'}
    while (index:=text.find(quote_text,start))>=0:
        spans.append({'start':index,'end':index+len(quote_text)})
        start=index+1
    return {'quote':quote_text,'spans':spans,'offset_unit':'unicode_codepoints',
            'status':'exact' if len(spans)==1 else 'ambiguous' if spans else 'invalid'}


def edge_card(c, graph, id=None, ordinal=None):
    row=c.execute('SELECT * FROM edges WHERE '+('id=?' if id is not None else 'ordinal=?'),(id if id is not None else ordinal,)).fetchone()
    if row is None:
        raise APIError('not_found','Relationship is unavailable in this perspective',404)
    meta=json.loads(row['metadata']); endpoints={}
    for side,index,quote_key in [('from',row['f'],'source_quote'),('to',row['t'],'target_quote')]:
        node=node_row(c,ordinal=index)
        endpoints[side]={'id':node['id'],'text':node['text'],'fragment':fragment(node['text'],meta.get(quote_key))}
    canonical = {key: meta[key] for key in ('relation_type', 'source_ref', 'target_ref', 'source_span', 'target_span', 'provenance') if key in meta}
    return {**canonical, 'basis':meta.get('basis'),
            'id':row['id'],'ordinal':row['ordinal'],'revision':graph.revision,'kind':row['kind'],'condition':meta.get('condition'),
            'recorded_at':row['recorded_at'],**endpoints,'unknowns':[]}


def source_page(c, graph, id, params):
    row=c.execute('SELECT * FROM sources WHERE id=?',(id,)).fetchone()
    if row is None:
        raise APIError('source_unavailable','Saved source record is unavailable in this perspective',404)
    body=json.loads(row['body']); text=body['prompt']; total=len(text)
    limit=bounded(params.get('limit'),12000,64000,1); focus=None
    if 'focus_start' in params or 'focus_end' in params:
        if 'focus_start' not in params or 'focus_end' not in params:
            raise APIError('invalid_span','Both focused span bounds are required')
        start=bounded(params.get('focus_start'),0,total); end=bounded(params.get('focus_end'),0,total)
        if start>=end:
            raise APIError('invalid_span','Focused span is outside the saved record')
        focus={'start':start,'end':end}; offset=max(0,start-min(400,limit//4))
    else:
        offset=bounded(params.get('offset'),0,total)
    end=min(total,offset+limit); context=[]
    meta=json.loads(body.get('metadata','{}'))
    neighbors=[]
    if params.get('context','1')!='0':
        if 'viewer_context_episode_ids' in meta:
            # A foreign source supplies context explicitly; never infer adjacency.
            for context_id in meta['viewer_context_episode_ids']:
                other=c.execute('SELECT id,body,position FROM sources WHERE id=?',(context_id,)).fetchone()
                if other is not None and other['id']!=id:
                    neighbors.append(other)
        elif row['position'] is not None:
            neighbors=c.execute('SELECT id,body,position FROM sources WHERE session_id=? AND position BETWEEN ? AND ? AND id!=? ORDER BY position',
                                (row['session_id'],row['position']-2,row['position']+2,id))
    for other in neighbors:
        value=json.loads(other['body']); prompt=value['prompt']
        context.append({'record_id':other['id'],'position':other['position'],'text':prompt[:2000],'offset':0,
                        'next_offset':2000 if len(prompt)>2000 else None,'total_characters':len(prompt)})
    return {'record_id':id,'revision':graph.revision,'text':text[offset:end],'offset':offset,'next_offset':end if end<total else None,
            'total_characters':total,'offset_unit':'unicode_codepoints','saved_text_complete':True,'external_source_complete':None,
            'highlights':[focus] if focus else [],'focus':focus,'previous_offset':max(0,offset-limit) if offset else None,
            'before_available':offset>0,'after_available':end<total,'context':context,'context_truncated':bool(meta.get('context_truncated')),
            'unknowns':['external_source_completeness_unknown']}


def overview_page(c, graph, id, params):
    """Page only the selected source version's saved body from this cache."""
    topic=next((topic for topic in graph.topics()['topics'] if topic['id']==id),None)
    if topic is None:
        raise APIError('not_found','Topic is unavailable in this perspective',404)
    table=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='overviews'").fetchone()
    row=c.execute('SELECT text,text_revision FROM overviews WHERE topic=? AND id=?',(topic['index'],id)).fetchone() if table else None
    if row is None:
        raise APIError('overview_unavailable','Saved overview text is unavailable for this source version',404)
    text=row['text']; total=len(text)
    offset=bounded(params.get('offset'),0,total); limit=bounded(params.get('limit'),12000,64000,1)
    end=min(total,offset+limit)
    return {'topic_id':id,'revision':graph.revision,'provider_id':graph.manifest['provider_id'],
            'source_ref':topic['source_ref'],'available_at':topic['available_at'],'freshness':topic['freshness'],
            **{key:topic[key] for key in ('session_result','slice_member_count','session_filter_applied','unavailable_member_count') if key in topic},
            **{key:topic.get(key) for key in ('overview_status','assembly_coverage','domain_coverage',
                                             'purpose','overview_protocol','overview_failure','accepted_at_that_snapshot',
                                             'navigation_membership')},
            'current_validation':topic.get('current_validation','unknown'),'domain_answer_current':False,
            'text':text[offset:end],'offset':offset,'text_total':total,'next_offset':end if end<total else None,
            'text_revision':row['text_revision'],'offset_unit':'unicode_codepoints'}
