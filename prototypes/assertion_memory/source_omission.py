"""Grounded query critique carried by existing source-audit requests.

This module grants no positive source acceptance. Its compact receipts refer to
immutable checks; only the ordinary independent source coverage loop can repair.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from .model import ModelError
from .overview_source_proof import digest, text_hash
from .overview_source_proof import StaleSourceRevision

SCHEMA='query-source-omission/v1'


def _canonical_hash(row):
    from .overview_review import _canonical_digest
    return _canonical_digest(row)


def revision(db,event_id,*,watermark=None,cutoff=None):
    row=db.execute('SELECT id,fingerprint,result FROM memory_extraction_runs WHERE event_id=? '
        'AND (? IS NULL OR id<=?) AND (? IS NULL OR completed_at<=?) ORDER BY id DESC LIMIT 1',
        (event_id,watermark,watermark,cutoff,cutoff)).fetchone()
    return {'id':row['id'],'fingerprint':row['fingerprint'],'result_sha256':text_hash(row['result'])} if row else None


def declare(need,ledger,request,frame,text,*,review_status='accepted'):
    value=need.get('source_omission')
    if 'source_omission' not in need:return None
    qualification=need.get('quote')
    qualified=isinstance(qualification,str) and 1<=len(qualification)<=2000 and qualification in text
    if (not isinstance(value,dict) or set(value)!={'canonical_id','proof_id','start','end'} or
            need.get('include_source_evidence') is not True or
            (not qualified and review_status not in ('revise','unresolved')) or
            not isinstance(need.get('reason'),str) or not 1<=len(need['reason'])<=2000):
        raise ModelError('Source omission requires a bounded independently reviewed original-read declaration.')
    key=value['canonical_id']; proof_id=value['proof_id']; start=value['start']; end=value['end']
    if (not isinstance(key,str) or key not in need.get('source_ids',[]) or key not in ledger.visible or
            not isinstance(proof_id,str) or type(start) is not int or type(end) is not int):
        raise ModelError('Source omission canonical anchor or exact range is invalid.')
    supplied=[row for row in request.records if row.get('role')=='source_evidence']
    if len(supplied)!=1 or supplied[0]!=ledger.proof_for('global'):
        raise ModelError('Source omission requires proof actually delivered to this independent review.')
    proof=supplied[0]
    bodies={row['proof_id']:row for row in proof['bodies']}
    anchors=[row for row in proof['anchors'] if row['assertion_id']==key]
    body=bodies.get(proof_id)
    if (body is None or not anchors or not any(row['proof_id']==proof_id for row in anchors) or
            not all(bodies[row['proof_id']]['source_text_complete'] for row in anchors) or
            not body['source_text_complete'] or body['source_span']!={'start':0,'end':body['source_length']} or
            not 0<=start<end<=len(body['text']) or body['body_sha256']!=text_hash(body['text']) or
            body['text_sha256']!=text_hash(body['text'])):
        raise ModelError('Source omission requires complete exact original and antecedent proof.')
    resolver=ledger.source_resolver
    if not hasattr(resolver,'omission_authority'):
        raise ModelError('Source omission has no frozen source authority.')
    authority=resolver.omission_authority(ledger.visible[key],proof)
    from .model import format_prompt
    return {'schema':SCHEMA,**authority,'canonical_id':key,'canonical_sha256':_canonical_hash(ledger.visible[key]),
        'proof_id':proof_id,'proof_sha256':digest(body),'body_sha256':body['body_sha256'],
        'proof_event_id':next(row['source']['event_id'] for row in anchors if row['proof_id']==proof_id),
        'start':start,'end':end,'quote_sha256':text_hash(body['text'][start:end]),
        'reason':need['reason'],'answer_qualification':qualification if qualified else None,
        'review_status':review_status,
        'review_request_sha256':text_hash(format_prompt(request)),
        'frame_sha256':digest(frame),'delivery_sha256':proof['delivery_sha256']}


def declare_gap(item,ledger,request,frame,text,*,review_status='accepted'):
    """Event/run witness from a complete original delivered to this independent review."""
    if 'source_omission' not in item:return None
    span=item['source_omission']
    qualification=item.get('qualification_quote')
    qualified=(item.get('answer_qualified') is True and isinstance(qualification,str)
        and 1<=len(qualification)<=2000 and qualification in text)
    if (not isinstance(span,dict) or set(span)!={'start','end'} or
            type(span['start']) is not int or type(span['end']) is not int or
            item.get('disposition')!='material' or
            (not qualified and review_status not in ('revise','unresolved')) or
            not isinstance(item.get('reason'),str) or not 1<=len(item['reason'])<=2000 or
            not isinstance(item.get('event_id'),str) or type(item.get('run_id')) is not int):
        raise ModelError('Source gap omission requires an exact material qualified declaration.')
    inventory=ledger.source_gap_inventory
    supplied=[r for r in request.records if r.get('role')=='source_gap_inventory']
    if not inventory or len(supplied)!=1 or supplied[0]!=inventory:
        raise ModelError('Source gap original was not delivered to this independent review.')
    rows=[r for r in inventory['gaps'] if (r['event_id'],r['run_id'])==(item['event_id'],item['run_id'])]
    if len(rows)!=1 or rows[0].get('context_only') or not isinstance(rows[0].get('raw_source',{}).get('text'),str):
        raise ModelError('Source gap omission has no complete attributable supplied original.')
    gap=rows[0];body=gap['raw_source']['text'];start,end=span['start'],span['end']
    if not 0<=start<end<=len(body):raise ModelError('Source gap omission exact range is invalid.')
    resolver=ledger.source_resolver
    if not hasattr(resolver,'omission_authority'):
        raise ModelError('Source gap omission has no frozen source authority.')
    if inventory!=resolver.snapshot.source_gaps(tuple(frame.get('scopes',[]))):
        raise ModelError('Source gap delivery is outside the frozen query scope.')
    authority=resolver.omission_authority(None,None,gap=gap)
    from .model import format_prompt
    return {'schema':SCHEMA,'anchor_kind':'source_gap',**authority,'proof_event_id':gap['event_id'],
        'start':start,'end':end,'body_sha256':text_hash(body),'quote_sha256':text_hash(body[start:end]),
        'gap_entry_sha256':digest(gap),'delivery_sha256':digest(inventory),
        'reason':item['reason'],'answer_qualification':qualification if qualified else None,
        'review_status':review_status,
        'review_request_sha256':text_hash(format_prompt(request)),'frame_sha256':digest(frame)}


def _gap_declarations(value):
    rows=value.get('source_gap_dispositions',[])
    return [r for r in rows if isinstance(r,dict) and 'source_omission' in r] if isinstance(rows,list) and len(rows)<=16 else []


def observe(value,ledger,request,frame,text):
    """Retain individually valid independent declarations even if later format repair fails."""
    needs=value.get('unknown_needs',[])
    if not isinstance(needs,list) or len(needs)>8: needs=[]
    from .overview_review import _unknown_needs
    for need in needs:
        if not isinstance(need,dict) or 'source_omission' not in need: continue
        try:
            _unknown_needs({'unknown_needs':[need]},False,value.get('status'))
            admitted=declare(need,ledger,request,frame,text,review_status=value.get('status'))
        except StaleSourceRevision:raise
        except ModelError:
            continue
        identity=digest({k:v for k,v in admitted.items() if k not in ('reason','answer_qualification','review_request_sha256','review_status')})
        ledger.source_omissions.setdefault(identity,admitted)
    for item in _gap_declarations(value):
        try:admitted=declare_gap(item,ledger,request,frame,text,review_status=value.get('status'))
        except StaleSourceRevision:raise
        except ModelError:continue
        identity=digest({k:v for k,v in admitted.items() if k not in ('reason','answer_qualification','review_request_sha256','review_status')})
        ledger.source_omissions.setdefault(identity,admitted)


def guard(value,ledger,request,frame,text):
    for item in _gap_declarations(value):declare_gap(item,ledger,request,frame,text,review_status=value['status'])
    for need in value['unknown_needs']:
        if 'source_omission' in need: declare(need,ledger,request,frame,text,review_status=value['status'])


def guard_repair(before,after):
    for item in _gap_declarations(before):
        # Explicit null is malformed optional JSON, with no span intent to
        # preserve. Guard still requires the existing bounded format repair.
        if item['source_omission'] is None:continue
        if not any(all(row.get(k)==item.get(k) for k in ('event_id','run_id','disposition','source_omission','answer_qualified','qualification_quote'))
                for row in _gap_declarations(after)):
            raise ModelError('Overview contract repair lost or changed source gap omission intent.')
    original=[n['source_omission'] for n in (before.get('unknown_needs') or [])
              if isinstance(n,dict) and n.get('source_omission') is not None]
    repaired=[n['source_omission'] for n in after['unknown_needs'] if 'source_omission' in n]
    if any(value not in repaired for value in original):
        raise ModelError('Overview contract repair lost or changed source omission intent.')


def _origin(engine,spec,snapshot,audit,check_id):
    frame=audit.get('query_context',{})
    if (frame.get('question')!=spec.query or frame.get('scopes')!=list(spec.scopes) or
            frame.get('cutoffs')!={'valid_as_of':snapshot.requested_valid_as_of,'recorded_as_of':snapshot.requested_recorded_as_of} or
            engine._spec(spec.id)!=spec):
        return None
    return {'view_id':spec.id,'question':spec.query,'definition_sha256':digest(asdict(spec)),
        'scopes':list(spec.scopes),'valid_as_of':snapshot.requested_valid_as_of,
        'recorded_as_of':snapshot.requested_recorded_as_of,'check_id':check_id}


def persist(engine,db,spec,snapshot,audit,check_id):
    """Called only inside the transaction that inserted this exact immutable check."""
    declarations=audit.get('source_omissions',[])
    if not declarations:return
    from types import SimpleNamespace
    from .source_reprocessing import admit_request, freeze
    queue=SimpleNamespace(store=engine.store)
    origin=_origin(engine,spec,snapshot,audit,check_id)
    receipts=[]
    for declaration in declarations:
        if origin is None:
            receipts.append({'status':'unavailable_origin','scheduled':False}); continue
        event_id=declaration['event_id']
        current=freeze(queue,event_id,db)
        if (current.fingerprint!=declaration['input_fingerprint'] or
                revision(db,event_id)!=declaration['source_revision'] or
                sorted(current.prior_ids)!=declaration['prior_ids']):
            receipts.append({'status':'stale','scheduled':False}); continue
        # A persisted reviewer receipt, exact source snapshot and current input all
        # agree. Rehydrate the original map; never accept model-supplied event IDs.
        if not _revalidate(engine,snapshot,declaration,audit,spec):
            receipts.append({'status':'unavailable','scheduled':False}); continue
        evidence={**declaration,'check_id':check_id,'discovered_at':db.execute(
            'SELECT checked_at FROM memory_view_checks WHERE id=?',(check_id,)).fetchone()[0],
            'origins':[origin]}
        fields=(('anchor_kind','event_id','source_revision','proof_event_id','start','end','body_sha256','quote_sha256')
            if declaration.get('anchor_kind')=='source_gap' else
            ('source_revision','canonical_id','canonical_sha256','proof_id','proof_event_id','start','end'))
        identity=digest([declaration[k] for k in fields])
        receipts.append(admit_request(queue,db,current,identity,evidence))
    audit['source_audit_admissions']=receipts
    db.execute('UPDATE memory_view_checks SET overview_review=? WHERE id=?',
        (json.dumps(audit,ensure_ascii=False,sort_keys=True),check_id))


def _revalidate(engine,snapshot,declaration,audit,spec):
    if declaration.get('anchor_kind')=='source_gap':
        inventory=snapshot.source_gaps(spec.scopes)
        rows=[r for r in inventory['gaps'] if r['event_id']==declaration['event_id'] and r['run_id']==declaration['source_revision']['id']]
        if (len(rows)!=1 or digest(rows[0])!=declaration['gap_entry_sha256'] or
                digest(inventory)!=declaration['delivery_sha256'] or
                digest(snapshot.descriptor())!=declaration['snapshot_sha256'] or
                digest(audit['query_context'])!=declaration['frame_sha256'] or not declaration['review_request_sha256']):return False
        gap=rows[0];body=gap.get('raw_source',{}).get('text')
        if (not isinstance(body,str) or text_hash(body)!=declaration['body_sha256'] or
                type(declaration['start']) is not int or type(declaration['end']) is not int or
                not 0<=declaration['start']<declaration['end']<=len(body) or
                text_hash(body[declaration['start']:declaration['end']])!=declaration['quote_sha256']):return False
        try:authority=snapshot.source_proof_resolver(engine).omission_authority(None,None,gap=gap)
        except ModelError:return False
        return all(declaration.get(k)==v for k,v in authority.items())
    key=declaration['canonical_id']; row=snapshot.assertions.get(key)
    if (row is None or _canonical_hash(row)!=declaration['canonical_sha256'] or
            declaration['snapshot_sha256']!=digest(snapshot.descriptor())): return False
    resolver=snapshot.source_proof_resolver(engine)
    expected=resolver(row); proof=audit.get('source_evidence',{})
    if not expected['anchors'] or expected['unavailable']: return False
    bodies={b['proof_id']:b for b in proof.get('bodies',[])}
    body=bodies.get(declaration['proof_id'])
    if body is None or digest(body)!=declaration['proof_sha256']:return False
    if any(a not in proof.get('anchors',[]) for a in expected['anchors']):return False
    if any(bodies.get(b['proof_id'])!=b for b in expected['bodies']):return False
    return bool(declaration['review_request_sha256'] and declaration['frame_sha256']==digest(audit['query_context']))


def pending(db,scope=None,cutoff=None):
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_source_audit_requests'").fetchone()
    if not exists:return False
    return bool(db.execute("SELECT 1 FROM memory_source_audit_requests r JOIN memory_raw_events e ON e.id=r.event_id "
        "WHERE r.status IN ('pending','scheduled','refresh') AND (? IS NULL OR e.scope=?) "
        "AND (? IS NULL OR (r.evidence_json IS NOT NULL AND json_extract(r.evidence_json,'$.discovered_at')<=?)) LIMIT 1",
        (scope,scope,cutoff,cutoff)).fetchone())


def actionable_pending(db):
    """Only a terminal target can be scheduled by tick immediately.

    Scheduled work and pending requests on busy targets use the raw job's
    available_at/lease deadline. Their operational pending status is separate.
    """
    return bool(db.execute("SELECT 1 FROM memory_source_audit_requests r JOIN memory_capture_jobs j "
        "ON j.event_id=r.event_id WHERE r.status='pending' AND j.status IN ('completed','failed') LIMIT 1").fetchone())


def _admission_hash(evidence):
    # Origins can coalesce after admission; their exact immutable checks are
    # independently fenced by _origin_bound. Every admission authority field,
    # including the original run, prior IDs, frame/range and hashes, stays bound.
    return digest({key:value for key,value in evidence.items() if key!='origins'})


def _own_partial_chain(queue,db,frozen,claim,row,evidence):
    from .source_reprocessing import PROTOCOL
    from .source_revision import validate_result
    original=evidence['source_revision']
    if revision(db,frozen.event.id,watermark=original['id'])!=original:return False
    runs=db.execute('SELECT id,result FROM memory_extraction_runs WHERE event_id=? AND id>? ORDER BY id LIMIT ?',
        (frozen.event.id,original['id'],queue._max_attempts)).fetchall()
    if not runs or len(runs)>=claim.job.attempts or not 1<claim.job.attempts<=queue._max_attempts:return False
    active=set(evidence['prior_ids']);last_attempt=None
    for run in runs:
        result=json.loads(run['result']);revision_value=result.get('source_revision',{})
        receipts=result.get('context_recovery',[])
        lineage=[r for r in receipts if r.get('operation')=='source_audit_continuation']
        if (len(lineage)!=1 or revision_value.get('event_id')!=frozen.event.id
                or revision_value.get('protocol')!=PROTOCOL or revision_value.get('input_fingerprint')!=frozen.fingerprint
                or revision_value.get('status') not in ('unresolved','failed')
                or not any(r.get('operational_error') for r in receipts)):return False
        proof=lineage[0];attempt=proof.get('attempt')
        if (proof.get('schema')!='source-audit-continuation/v1' or proof.get('job_id')!=claim.job.id
                or type(attempt) is not int or not 1<=attempt<claim.job.attempts
                or (last_attempt is not None and attempt!=last_attempt+1)
                or not isinstance(proof.get('bindings'),dict)
                or proof['bindings'].get(row['id'])!=_admission_hash(evidence)):return False
        try:validate_result(db,frozen.event.id,result,prior_ids=active)
        except ValueError:return False
        for item in revision_value['reconciliation']:
            if item['decision']=='introduced':active.add(item['assertion_id'])
            elif item['decision']=='withdrawn':active.discard(item['assertion_id'])
        last_attempt=attempt
    return last_attempt==claim.job.attempts-1 and active==set(frozen.prior_ids)


def _request_current(queue,db,frozen,claim,row,evidence):
    from .source_reprocessing import PROTOCOL
    if row['protocol']!=PROTOCOL or row['input_fingerprint']!=frozen.fingerprint:return False
    if not evidence:return True
    if (evidence['source_revision']==revision(db,frozen.event.id)
            and evidence['prior_ids']==sorted(frozen.prior_ids)):return True
    return _own_partial_chain(queue,db,frozen,claim,row,evidence)


def claim_requests(queue,db,frozen,claim):
    rows=db.execute("SELECT * FROM memory_source_audit_requests WHERE event_id=? AND status='scheduled' ORDER BY rowid LIMIT 2",
        (frozen.event.id,)).fetchall()
    claimed=[]
    for row in rows:
        evidence=json.loads(row['evidence_json']) if row['evidence_json'] else None
        if not _request_current(queue,db,frozen,claim,row,evidence):
            db.execute("UPDATE memory_source_audit_requests SET status='stale' WHERE id=?",(row['id'],));continue
        claimed.append({'request_id':row['id'],'evidence':evidence})
    return tuple(claimed)


def request_consumption(queue,db,frozen,claim,extraction):
    """Validate the frozen requests consumed by this owned publication.

    This immutable consumption receipt authorizes bounded continuation, never a
    rewrite of the original admission or a general waiver of revision freshness.
    It commits/rolls back with the partial run and existing retry transition.
    """
    admissions=[r for r in extraction.get('context_recovery',[]) if r.get('operation')=='source_audit_admission']
    consumed=admissions[0].get('bindings',{}) if len(admissions)==1 else {}
    bindings={}
    for key in extraction.get('source_audit_request_ids',[]):
        row=db.execute("SELECT * FROM memory_source_audit_requests WHERE id=? AND event_id=? AND status='scheduled'",
            (key,frozen.event.id)).fetchone()
        evidence=json.loads(row['evidence_json']) if row and row['evidence_json'] else None
        if (row is None or not _request_current(queue,db,frozen,claim,row,evidence)
                or (evidence and consumed.get(key)!=_admission_hash(evidence))):
            raise ValueError('Source audit admission changed before partial publication.')
        if evidence:bindings[key]=_admission_hash(evidence)
    return ({'operation':'source_audit_continuation','schema':'source-audit-continuation/v1',
             'job_id':claim.job.id,'attempt':claim.job.attempts,'bindings':bindings} if bindings else None)


def critiques(frozen,window):
    records=[]
    for request in frozen.requests:
        evidence=request['evidence']
        if not evidence:continue
        source=window.sources.get(evidence['proof_event_id'])
        if source is None or text_hash(source.prompt)!=evidence['body_sha256']:
            raise ModelError('Query omission proof is not present in the authorized audit window.')
        start,end=evidence['start'],evidence['end']
        if not any(left<=start<end<=right for left,right in window.spans.get(source.id,[])):
            result=window.recover([{'operation':'read','event_id':source.id,'start':start,'end':end}])
            if result[0]['status']!='supplied':raise ModelError('Query omission proof exceeds bounded audit delivery.')
        if text_hash(source.prompt[start:end])!=evidence['quote_sha256']:
            raise ModelError('Query omission exact proof changed.')
        records.append({'role':'source_omission_hypothesis','use':'OPERATIONAL_CRITIQUE_NOT_SOURCE_FACT',
            'request_id':request['request_id'],'reason':evidence['reason'],
            **({'canonical_id':evidence['canonical_id']} if evidence.get('anchor_kind')!='source_gap' else {}),
            'proof_event_id':source.id,'start':start,'end':end})
    return records


def finish_requests(db,extraction,*,run_id=None):
    consumed=extraction.get('source_audit_request_ids',[])
    refresh=False
    for key in consumed:
        row=db.execute("SELECT evidence_json FROM memory_source_audit_requests WHERE id=? AND status='scheduled'",(key,)).fetchone()
        if row is None:continue
        evidence=json.loads(row['evidence_json']) if row['evidence_json'] else None
        status='refresh' if evidence else 'completed'
        if evidence:
            evidence['audit_run_id']=run_id
            evidence['audit_outcome']=[r for r in extraction.get('context_recovery',[]) if r.get('operation')=='query_source_omission']
            db.execute('UPDATE memory_source_audit_requests SET evidence_json=? WHERE id=?',(json.dumps(evidence,ensure_ascii=False),key))
            refresh=True
        db.execute('UPDATE memory_source_audit_requests SET status=? WHERE id=?',(status,key))
    return refresh


def refresh_origins(worker,claim):
    """Exact stored definitions and perspective; no semantic top-k impact guess."""
    from .queue import _json
    versions=[]
    with worker.queue.store.transaction() as db:
        rows=[dict(r) for r in db.execute("SELECT id,evidence_json FROM memory_source_audit_requests WHERE event_id=? AND status='refresh'",
            (claim.job.event_id,))]
    for row in rows:
        evidence=json.loads(row['evidence_json']); outcomes=[]
        for origin in evidence['origins']:
            spec=worker.engine._spec(origin['view_id'])
            if not _origin_bound(worker,origin,evidence,spec):
                outcomes.append({'view_id':spec.id,'status':'stale_origin'});continue
            receipts={}
            with worker.queue.store.transaction() as db:
                run=db.execute('SELECT result FROM memory_extraction_runs WHERE id=? AND event_id=?',
                    (evidence['audit_run_id'],claim.job.event_id)).fetchone()
            accepted=json.loads(run['result'])['source_revision']['assertion_ids'] if run else []
            from .model import SemanticUnavailable
            outcome='refreshed'
            try:
                version=worker.engine.build_view(spec.id,valid_as_of=origin['valid_as_of'],recorded_as_of=origin['recorded_as_of'],
                    _receipts=receipts,_recovery_ids=tuple(accepted))
            except SemanticUnavailable as error:
                version=getattr(error,'failure_version',None)
                check=getattr(error,'failure_check',None)
                audit=version.overview_review if version is not None else {}
                if (getattr(error,'operational_contract',False) or not check or not audit
                        or audit.get('failure_proof') is not True or audit.get('status')!='unresolved'
                        or audit.get('failure_classification')!='semantic'
                        or getattr(error,'failure_view_id',None)!=spec.id):raise
                receipts[spec.id]=check;outcome='unresolved'
            versions.append(version)
            outcomes.append({'view_id':spec.id,'status':outcome,'version_id':version.id,'check_id':receipts[spec.id]['id']})
        evidence['refresh_outcomes']=outcomes
        with worker.queue.store.transaction(write=True) as db:
            worker.queue._assert_owned(db,claim)
            db.execute("UPDATE memory_source_audit_requests SET evidence_json=?,status='completed' WHERE id=? AND status='refresh'",
                (_json(evidence),row['id']))
    return versions


def _origin_bound(worker,origin,evidence,spec):
    if digest(asdict(spec))!=origin['definition_sha256'] or spec.query!=origin['question']:return False
    with worker.queue.store.transaction() as db:
        check=db.execute('SELECT view_id,overview_review FROM memory_view_checks WHERE id=?',(origin['check_id'],)).fetchone()
    if check is None or check['view_id']!=spec.id:return False
    audit=json.loads(check['overview_review']);frame=audit.get('query_context',{})
    if frame!={'role':'query_context','question':origin['question'],'scopes':origin['scopes'],
               'cutoffs':{'valid_as_of':origin['valid_as_of'],'recorded_as_of':origin['recorded_as_of']},
               'answer_version_policy':'server_envelope_only'}:return False
    fields=(('anchor_kind','event_id','source_revision','proof_event_id','start','end','body_sha256','quote_sha256')
        if evidence.get('anchor_kind')=='source_gap' else ('event_id','canonical_id','proof_id','start','end'))
    return any(all(row.get(k)==evidence.get(k) for k in fields)
               for row in audit.get('source_omissions',[]))


def retarget_fallback(engine,db,check_id,view_id,request,route_identity,*,failure=None):
    """Carry a checked critique only across the existing route CAS materialization."""
    row=db.execute('SELECT * FROM memory_view_checks WHERE id=?',(check_id,)).fetchone()
    if row is None:return
    audit=json.loads(row['overview_review']);cert=audit.get('certificate',{});frame=audit.get('query_context',{})
    spec=engine._spec(view_id)
    if (cert.get('kind')!='answer_applicability' or any(cert.get(k)!=v for k,v in route_identity.items()) or
            not audit.get('source_omissions') or frame.get('question')!=request['question'] or
            spec.query!=request['question'] or list(spec.scopes)!=request['scopes'] or
            frame.get('scopes')!=request['scopes'] or frame.get('cutoffs')!={k:request[k] for k in ('valid_as_of','recorded_as_of')}):
        return
    from .query_snapshot import _restore_frozen
    from .build_proof import _write_failure, acquisition_state
    snapshot=_restore_frozen(engine,json.loads(row['query_snapshot']))
    state=acquisition_state(engine,spec,snapshot,phase='fixed_answer_omission_retarget')
    prior=engine.get_version(row['version_id'])
    state.update(evidence=prior.evidence,edges=prior.edges,
        review={**audit,'source_omission_retarget':{'check_id':check_id,'route_identity':route_identity}})
    error=(failure if audit.get('failure_classification')=='operational' and isinstance(failure,Exception) else
        ModelError('Source interpretation recovery is pending for this exact question.'))
    _write_failure(engine,spec,snapshot,error,state)


def status_counts(db,scope):
    return dict(db.execute('SELECT r.status,count(*) FROM memory_source_audit_requests r '
        'JOIN memory_raw_events e ON e.id=r.event_id WHERE (? IS NULL OR e.scope=?) GROUP BY r.status',
        (scope,scope)).fetchall())


def status_page(db,scope,offset,limit):
    from .service import _continuation
    rows=db.execute("SELECT r.id,r.event_id,r.status,json_extract(r.evidence_json,'$.check_id') check_id,"
        "json_extract(r.evidence_json,'$.origins[0].view_id') origin_view_id,"
        "json_extract(r.evidence_json,'$.discovered_at') discovered_at,"
        "json_extract(r.evidence_json,'$.audit_run_id') audit_run_id FROM memory_source_audit_requests r "
        'JOIN memory_raw_events e ON e.id=r.event_id WHERE (? IS NULL OR e.scope=?) ORDER BY r.rowid LIMIT ? OFFSET ?',
        (scope,scope,limit,offset)).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        if item['check_id'] is not None and item['origin_view_id']:
            item['proof_continuation']=_continuation('memory_history',view_id=item['origin_view_id'],section='checks',
                snapshot_id=item['check_id'],offset=0,limit=6000)
        result.append(item)
    return result
