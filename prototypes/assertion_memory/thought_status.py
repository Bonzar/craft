"""Bounded raw diagnostics plus exact current-scope unfinished canonical work."""
import json

from . import query_timing as timing
from .service import _bounded_status, _status_selection, _fits, _continuation


WORK_COLUMNS = 'w.thought_id,w.status,w.action,w.attempts,w.available_at,w.error IS NOT NULL AS has_error'


def _work(service, db, scope, *, thought_ids=None):
    predicate=' AND w.thought_id IN (SELECT value FROM json_each(?))' if thought_ids is not None else ''
    arguments=(json.dumps(thought_ids),) if thought_ids is not None else ()
    rows = db.execute('SELECT '+WORK_COLUMNS+",h.current_version FROM memory_thought_work w "
        "LEFT JOIN memory_thoughts h ON h.id=w.thought_id WHERE w.status!='completed' "+predicate+
        ' ORDER BY w.available_at,w.thought_id',arguments).fetchall()
    if scope is None:
        return [dict(row) for row in rows]
    from .thought_selection import ThoughtSelection
    selected = ThoughtSelection(service.thoughts,scope=scope,thought_ids=[row['thought_id'] for row in rows])
    return [dict(row) for row in rows if (thought:=selected.get(row['thought_id'])) is not None
            and thought.version==row['current_version']]


def _work_page(db,rows,offset,limit,*,errors):
    if rows is not None:
        values=[row for row in rows if row['has_error']] if errors else rows
        return values[offset:offset+limit],len(values)
    predicate="w.status!='completed'"+(' AND w.error IS NOT NULL' if errors else '')
    total=db.execute('SELECT count(*) FROM memory_thought_work w WHERE '+predicate).fetchone()[0]
    page=db.execute('SELECT '+WORK_COLUMNS+' FROM memory_thought_work w WHERE '+predicate+
        ' ORDER BY w.available_at,w.thought_id LIMIT ? OFFSET ?',(limit,offset)).fetchall()
    return page,total


def _work_details(result,db,rows,offset,limit):
    totals=[]
    for name in ('thought_pending_jobs','thought_errors'):
        values,total=_work_page(db,rows,offset,limit,errors=name=='thought_errors')
        bodies={}
        if name=='thought_errors' and values:
            bodies=dict(db.execute('SELECT thought_id,error FROM memory_thought_work WHERE thought_id '
                'IN (SELECT value FROM json_each(?))',(json.dumps([row['thought_id'] for row in values]),)))
        result[name]=[{'job_id':row['thought_id'],'status':row['status'],'phase':row['action'],
                      'attempts':row['attempts'],**({'error':bodies[row['thought_id']]} if name=='thought_errors' else {})}
                     for row in values]
        result['pagination'][name]={'offset':offset,'limit':limit,'total':total,
            'next_offset':offset+limit if offset+limit<total else None}
        totals.append(total)
    result['raw_pending_count']=result['pending_count']
    result['raw_error_count']=result['error_count']
    result['pending_count']+=totals[0]
    result['error_count']+=totals[1]


def snapshot(service,event_id,scope,offset,limit):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=1000:
        raise ValueError('offset must be nonnegative and limit between 1 and 1000')
    if scope is not None and (not isinstance(scope,str) or not scope.strip()):
        raise ValueError('scope must be nonempty or None for global diagnostics')
    base,parameters=_status_selection(scope)
    selection=(base+' AND j.event_id IS NOT NULL',parameters)
    with service.store.transaction() as db:
        result=service._status_counts(db,scope,selection=selection)
        result.update(service._status_details(db,scope,offset,limit,result,selection=selection))
        result['transport_status']=service.transport_status()
        rows=_work(service,db,scope) if scope is not None else None
        _work_details(result,db,rows,offset,limit)
        if scope is None:
            counts=dict(db.execute('SELECT status,count(*) FROM memory_thought_work GROUP BY status').fetchall())
            result['thought_count']=db.execute('SELECT count(*) FROM memory_thoughts').fetchone()[0]
            result['legacy_assertion_audit_count']=result['assertion_count']
        else:
            from collections import Counter
            counts=dict(Counter(row['status'] for row in rows))
        result.update(storage='unified-thought-v1',thought_work_counts=counts,
            thought_work_counts_coverage='all' if scope is None else 'unfinished_current_scope',
            diagnostic_scope='global' if scope is None else scope,
            model_configured=service.engine.model is not None)
        if scope is None:
            from .thought_repair_stats import repair_statistics
            repair=repair_statistics(service.store,service.engine.model)
            result.update(thought_repairs=repair['summary'],
                repair_tracking={key:repair[key] for key in ('scope','coverage') if key in repair},
                configured_processing_session_ids=list(service.processing_session_ids)
                    if service.processing_session_ids is not None else None)
        if event_id is not None:
            result['job']=job(service,event_id,scope,rows)
    return _bounded_status(result,scope,offset)


def job(service,identity,scope,rows=None,*,batch_diagnostics=False):
    if service.queue.get(identity) is not None:
        with service.store.transaction() as db:
            raw = service._status_job(identity,scope)
            if batch_diagnostics and raw['event_id'] is not None:
                from .capture_batch_diagnostics import exact_event
                diagnostics = exact_event(db,raw['event_id'])
                if diagnostics is not None:
                    raw['capture_batch'] = diagnostics
        return raw
    with service.store.transaction() as db:
        values=_work(service,db,scope,thought_ids=[identity]) if rows is None else rows
    row=next((row for row in values if row['thought_id']==identity),None)
    if row is None:
        raise ValueError('unknown unfinished thought job in requested scope')
    return dict(service.work.get(identity),id=identity,event_id=None,assertion_id=None,phase=row['action'])


@timing.timed('diagnostics')
def query_diagnostics(service,result,scope):
    health=snapshot(service,None,scope,0,100)
    processing=('errors' if health['error_count'] else 'pending' if health['pending_count'] else
                'needs_review' if health['unresolved_count'] else 'idle')
    # Counts reflect live scope health, not relevance to this exact question.
    # A newly saved answer itself enqueues maintenance before this call.
    incomplete=bool(result.get('knowledge_limit') or result.get('answer_error'))
    answer_completeness='incomplete' if incomplete else 'available_evidence'
    response={**health,**result,'processing_state':processing,
        'refresh_required':False,'completeness':answer_completeness,
        'response_complete':health['response_complete'] and result.get('response_complete',True)}
    response=service._attach_query_transport(response)
    # Preserve the transport freshness precaution and diagnostics, but a global
    # ingest gap cannot downgrade the completeness of the scoped answer.
    response['completeness']=answer_completeness
    from .query_coverage import project
    response['query_coverage']=project(response)
    if _fits(response):return response
    details=('pending_jobs','errors','unresolved','source_audit_requests','thought_pending_jobs','thought_errors','pagination','job')
    response={key:value for key,value in response.items() if key not in details}
    cursors=list(response.get('continuations',[]))
    diagnostic=_continuation('memory_status',scope=scope,offset=0,limit=100)
    if diagnostic not in cursors:cursors.append(diagnostic)
    response.update(response_complete=False,diagnostics_inline=False,continuations=cursors)
    if not _fits(response):
        raise ValueError('canonical query metadata exceeds the response budget')
    return response
