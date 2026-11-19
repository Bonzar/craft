"""Read-only Core provenance SDK -> exact finished-output / referenced-input bundle.

Mutable job last-output maps are deliberately not a fallback. All provenance
reads are delegated to the genuine Core reader at one immutable watermark.
"""
import json
from ..common import APIError,timestamp
from .version_identity import topic_version_id

POLICY='core-run-provenance-pins-v2-request-context'

def _sdk():
    from prototypes.assertion_memory import run_provenance
    return run_provenance

def _json_pages(call,*,snapshot=None):
    offset=0;parts=[]
    while True:
        page=call(offset)
        if page.get('encoding')!='json' or page.get('offset')!=offset or not isinstance(page.get('content'),str):raise ValueError('Invalid SDK JSON page')
        if snapshot is not None and page.get('snapshot_id')!=snapshot:raise ValueError('SDK watermark changed')
        parts.append(page['content']);end=offset+len(page['content'])
        if page.get('end')!=end:raise ValueError('SDK page length mismatch')
        next_offset=page.get('next_offset')
        if next_offset is None:
            if not page.get('response_complete') or end!=page.get('total_characters'):raise ValueError('Incomplete SDK section')
            return json.loads(''.join(parts))
        if type(next_offset) is not int or next_offset!=end or next_offset<=offset:raise ValueError('SDK page did not advance')
        offset=next_offset

def _pair(view,version):
    topic_version_id(view,version)
    return view,version

def _upstream(value):
    if not isinstance(value,dict):raise ValueError('Invalid exact upstream map')
    return [_pair(view,version) for view,version in value.items()]

def result_bundle(db,selected,cutoff,*,run_id=None,snapshot_id=None,sdk=None,read_material=None,use_core_reader=True):
    history={'state':'unknown','known_results':0,'history_complete':False,'reason':'recorded_runs_only','unresolved':0,
             'snapshot_id':0,'selected_run_id':run_id,'runs':[],'pending_output_count':0,'caller_context_output_count':0}
    empty={'pins':[],'runs':[],'events':[],'history':history}
    if not use_core_reader:
        history['reason']='reader_unavailable'
        if run_id or snapshot_id is not None:raise APIError('causal_history_unavailable','This provider has no Core causal reader',409)
        return empty
    try:sdk=sdk or _sdk()
    except ImportError:
        history['reason']='reader_unavailable'
        if run_id or snapshot_id is not None:raise APIError('causal_history_unavailable','Core causal reader is unavailable',409)
        return empty
    basis,raw=selected.split(':',1);wanted='metadata.native_session_id' if basis=='native' else 'stored_session_id_fallback'
    entries=[];offset=0;pinned=snapshot_id
    try:
        while True:
            page=sdk.session_outputs(db,raw,offset=offset,limit=100,snapshot_id=pinned)
            if pinned is None:pinned=page['snapshot_id']
            if page.get('snapshot_id')!=pinned or page.get('offset')!=offset or not isinstance(page.get('entries'),list):raise ValueError('Invalid SDK output page')
            entries.extend(page['entries']);next_offset=page.get('next_offset')
            if next_offset is None:
                if not page.get('response_complete') or len(entries)!=page['total']:raise ValueError('Incomplete recorded output set')
                break
            if type(next_offset) is not int or next_offset!=offset+len(page['entries']) or next_offset<=offset:raise ValueError('SDK outputs did not advance')
            offset=next_offset
    except ValueError as error:
        raise APIError('output_snapshot_unavailable' if snapshot_id is not None else 'causal_history_unavailable','The requested output watermark is unavailable',409) from error
    history['snapshot_id']=pinned
    details={};by_run={}
    for entry in entries:
        if entry.get('details_required'):
            detail=_json_pages(lambda offset:sdk.event_page(db,entry['event_id'],offset=offset,limit=6000))
            if detail.get('id')!=entry['event_id'] or detail.get('run_id')!=entry['run_id'] or detail['id']>pinned:raise ValueError('Wrong Core event details')
            details[entry['event_id']]=detail
        recorded=details.get(entry['event_id'],entry).get('recorded_at')
        if timestamp(recorded)>cutoff:continue
        by_run.setdefault(entry['run_id'],[]).append(entry)
    candidates=set(by_run);finished=[];runs=[];events=[];summaries=[]
    for key in sorted(candidates):
        header=_json_pages(lambda offset:sdk.run_page(db,key,section='run',offset=offset,limit=6000,snapshot_id=pinned),snapshot=pinned)
        causes=_json_pages(lambda offset:sdk.run_page(db,key,section='causes',offset=offset,limit=6000,snapshot_id=pinned),snapshot=pinned)
        run_events=_json_pages(lambda offset:sdk.run_page(db,key,section='events',offset=offset,limit=6000,snapshot_id=pinned),snapshot=pinned)
        if not isinstance(causes,list) or not isinstance(run_events,list):raise ValueError('Invalid Core run sections')
        eligible=[event for event in run_events if timestamp(event['recorded_at'])<=cutoff]
        outputs=[event for event in eligible if event['kind']=='view' and event['outcome'] in ('produced','reused')]
        # Details-required entries are resolved with the genuine bounded reader;
        # event identity and watermark remain fixed by the Source transaction.
        for entry in by_run[key]:
            if entry.get('details_required'):
                detail=details[entry['event_id']]
                if detail.get('id')!=entry['event_id'] or detail.get('run_id')!=key or detail['id']>pinned:raise ValueError('Wrong Core event details')
                if not any(event==detail for event in run_events):raise ValueError('Core event differs from pinned run')
        request_context=header.get('cause_status')=='caller_context_only'
        if request_context:
            # Explicit caller attribution is material context, never authenticated
            # assertion origin. Namespace ambiguity is deliberately not guessed.
            if (header.get('frontier') or {}).get('request_session_id')!=raw:continue
            history['caller_context_output_count']+=len(outputs)
        elif not any(cause.get('source_session_id')==raw and cause.get('session_id_basis')==wanted and cause.get('source_event_id') for cause in causes):continue
        attribution='request_context' if request_context else 'source_cause'
        lifecycle=max((event for event in eligible if event['kind']=='lifecycle'),key=lambda event:event['id'],default=None)
        status=lifecycle['outcome'] if lifecycle else 'unknown'
        cause_sessions=[] if request_context else sorted({('native:' if cause.get('session_id_basis')=='metadata.native_session_id' else 'logical:')+cause['source_session_id'] for cause in causes if cause.get('source_session_id') and cause.get('session_id_basis') in ('metadata.native_session_id','stored_session_id_fallback')})
        summary={'attribution':attribution,'run_id':key,'run_kind':header['kind'],'lifecycle':status,'cause_status':header['cause_status'],'output_count':len(outputs),'last_event_id':max((event['id'] for event in eligible),default=0),'recorded_at':lifecycle['recorded_at'] if lifecycle else None}
        summaries.append(summary);runs.append({'run':header,'causes':causes,'events':eligible,'summary':summary});events.extend(outputs)
        if status!='completed':history['pending_output_count']+=len(outputs);continue
        for event in outputs:finished.append((event,summary,cause_sessions))
    history['runs']=summaries
    if run_id is not None and not any(summary['run_id']==run_id and summary['lifecycle']=='completed' for summary in summaries):raise APIError('run_unavailable','This completed run is unavailable for the selected session/watermark',409)
    applicable=[value for value in finished if run_id is None or value[0]['run_id']==run_id]
    chosen={}
    if run_id is None:
        for value in sorted(applicable,key=lambda value:value[0]['id']):chosen[value[0]['view_id']]=value
        selected_events=list(chosen.values())
    else:selected_events=applicable
    pins={};all_occurrences={}
    for event,summary,causes in applicable:
        pair=_pair(event['view_id'],event['version_id']);all_occurrences.setdefault(pair,[]).append({'event_id':event['id'],'run_id':event['run_id'],'check_id':event['check_id'],'recorded_at':event['recorded_at'],'attribution':summary['attribution'],'cause_status':summary['cause_status'],'cause_sessions':list(causes),'outcome':event['outcome'],'review_status':event.get('review_status'),'failure_proof':bool(event.get('failure_proof'))})
    for event,summary,causes in selected_events:
        pair=_pair(event['view_id'],event['version_id']);_upstream(event['upstream'])
        pin={'attribution':summary['attribution'],'view_id':pair[0],'version_id':pair[1],'session_id':selected,'receipt_id':'run-event:'+str(event['id']),'run_id':event['run_id'],'event_id':event['id'],'check_id':event['check_id'],'review_status':event.get('review_status'),'failure_proof':bool(event.get('failure_proof')),'outcome':event['outcome'],'role':'output','lifecycle':'completed','cause_status':summary['cause_status'],'cause_sessions':causes,'upstream_map':event['upstream'],'occurrences':all_occurrences[pair]}
        if pair not in pins or pins[pair]['event_id']<event['id']:pins[pair]=pin
    queue=list(pins)
    for pair in queue:
        pin=pins[pair]
        material=read_material(*pair) if read_material else None
        if material is not None:
            saved=material[2].get('upstream',{})
            if pin['role']=='output' and saved!=pin['upstream_map']:raise ValueError('Immutable version upstream differs from its output receipt')
            pin['upstream_map']=saved
        for upstream in _upstream(pin['upstream_map']):
            if upstream in pins:continue
            if read_material is None:raise ValueError('Exact input material reader is required')
            saved=read_material(*upstream)[2]
            pins[upstream]={**pin,'view_id':upstream[0],'version_id':upstream[1],'event_id':None,'check_id':None,'review_status':None,'failure_proof':False,'attribution':'referenced_input','outcome':'referenced','role':'input','upstream_map':saved.get('upstream',{}),'occurrences':all_occurrences.get(upstream,[])};queue.append(upstream)
    for pair,pin in pins.items():pin['upstream']=[{'view_id':view,'version_id':version,'topic_id':topic_version_id(view,version)} for view,version in _upstream(pin.pop('upstream_map'))]
    history['known_results']=sum(pin['role']=='output' for pin in pins.values());history['state']='partial' if pins else 'unknown'
    return {'pins':[pins[pair] for pair in sorted(pins)],'runs':runs,'events':events,'history':history}
