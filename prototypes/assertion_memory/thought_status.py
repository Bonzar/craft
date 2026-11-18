"""Bounded raw diagnostics plus exact current-scope unfinished canonical work."""
from .service import _bounded_status, _status_selection, _fits, _continuation


def _work(service, db, scope):
    rows = db.execute("SELECT w.*,h.current_version FROM memory_thought_work w "
        "LEFT JOIN memory_thoughts h ON h.id=w.thought_id WHERE w.status!='completed' "
        'ORDER BY w.available_at,w.thought_id').fetchall()
    if scope is None:
        return [dict(row) for row in rows]
    from .thought_selection import ThoughtSelection
    selected = ThoughtSelection(service.thoughts,scope=scope,thought_ids=[row['thought_id'] for row in rows])
    return [dict(row) for row in rows if (thought:=selected.get(row['thought_id'])) is not None
            and thought.version==row['current_version']]


def _work_details(result, rows, offset, limit):
    errors = [row for row in rows if row['error'] is not None]
    for name, values in (('thought_pending_jobs',rows),('thought_errors',errors)):
        result[name]=[{'job_id':row['thought_id'],'status':row['status'],'phase':row['action'],
                      'attempts':row['attempts'],**({'error':row['error']} if name=='thought_errors' else {})}
                     for row in values[offset:offset+limit]]
        result['pagination'][name]={'offset':offset,'limit':limit,'total':len(values),
            'next_offset':offset+limit if offset+limit<len(values) else None}
    result['raw_pending_count']=result['pending_count']
    result['raw_error_count']=result['error_count']
    result['pending_count']+=len(rows)
    result['error_count']+=len(errors)


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
        rows=_work(service,db,scope)
        _work_details(result,rows,offset,limit)
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


def job(service,identity,scope,rows=None):
    if service.queue.get(identity) is not None:
        return service._status_job(identity,scope)
    with service.store.transaction() as db:
        values=_work(service,db,scope) if rows is None else rows
    row=next((row for row in values if row['thought_id']==identity),None)
    if row is None:
        raise ValueError('unknown unfinished thought job in requested scope')
    return dict(row,id=identity,event_id=None,assertion_id=None,phase=row['action'])


def query_diagnostics(service,result,scope):
    health=snapshot(service,None,scope,0,100)
    processing=('errors' if health['error_count'] else 'pending' if health['pending_count'] else
                'needs_review' if health['unresolved_count'] else 'idle')
    incomplete=processing!='idle' or bool(result.get('knowledge_limit'))
    response={**health,**result,'processing_state':processing,
        'refresh_required':processing!='idle','completeness':'incomplete' if incomplete else 'available_evidence',
        'response_complete':health['response_complete'] and result.get('response_complete',True)}
    response=service._attach_query_transport(response)
    if _fits(response):return response
    details=('pending_jobs','errors','unresolved','source_audit_requests','thought_pending_jobs','thought_errors','pagination','job')
    response={key:value for key,value in response.items() if key not in details}
    response.update(response_complete=False,diagnostics_inline=False,
        continuations=[_continuation('memory_status',scope=scope,offset=0,limit=100)])
    if not _fits(response):
        raise ValueError('canonical query metadata exceeds the response budget')
    return response
