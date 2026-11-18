"""Bounded source audits. Operational cursors are mutable; meaning stays in runs.

Reviewer receipts are produced only after real independent source checks. Prior
canonical records are hypotheses, never added to the raw source collection.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from .capture import RawEvent
from .model import ModelError, ModelRequest
from .source_revision import selection
from .source_coverage import PROTOCOL as COVERAGE_PROTOCOL

PROTOCOL = 'source-reconciliation/v2:' + COVERAGE_PROTOCOL
SCAN_LIMIT = 16
MAX_GAP_AUDITS = 2


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class FrozenInput:
    event: RawEvent
    events: tuple[RawEvent, ...]
    fingerprint: str
    order: dict
    prior_ids: frozenset[str]
    requests: tuple = ()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS memory_source_audits ('
        'event_id TEXT PRIMARY KEY, protocol TEXT NOT NULL, desired_input TEXT NOT NULL, '
        'attempted_input TEXT, status TEXT NOT NULL, reason TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS memory_source_audit_requests ('
        'id TEXT PRIMARY KEY,event_id TEXT NOT NULL,protocol TEXT NOT NULL,'
        'input_fingerprint TEXT NOT NULL,status TEXT NOT NULL,evidence_json TEXT)')
    if 'evidence_json' not in {row[1] for row in db.execute('PRAGMA table_info(memory_source_audit_requests)')}:
        db.execute('ALTER TABLE memory_source_audit_requests ADD COLUMN evidence_json TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS memory_source_audit_cursor ('
        'protocol TEXT PRIMARY KEY, cursor INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS memory_source_context_changes ('
        'id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,changed_at TEXT NOT NULL,'
        'cursor INTEGER NOT NULL DEFAULT 0,UNIQUE(event_id,changed_at))')


# The suffix predicate is shared by scheduling and immediate freshness checks.
# It describes possible authorized-prefix changes, never a whole-session hash.
_SUFFIX = """e.id != c.id AND e.session_id=c.session_id AND e.scope=c.scope AND (
    (p.position IS NOT NULL AND cp.position IS NOT NULL AND p.position>=cp.position)
    OR ((p.position IS NULL OR cp.position IS NULL) AND e.rowid>c.rowid))"""


def changed(db, event_id, changed_at):
    # Capture writes this short marker only. Suffix enumeration lives in worker.
    db.execute('INSERT OR IGNORE INTO memory_source_context_changes(event_id,changed_at) VALUES(?,?)',
        (event_id,changed_at))


def pending_context(db, scope=None, cutoff=None):
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_source_context_changes'").fetchone()
    if not exists: return False
    return bool(db.execute('SELECT EXISTS(SELECT 1 FROM memory_source_context_changes m '
        'JOIN memory_raw_events c ON c.id=m.event_id '
        'LEFT JOIN memory_event_positions cp ON cp.event_id=c.id '
        'JOIN memory_raw_events e LEFT JOIN memory_event_positions p ON p.event_id=e.id '
        'WHERE '+_SUFFIX+' AND (? IS NULL OR e.scope=?) AND (? IS NULL OR m.changed_at<=?) '
        'AND (? IS NULL OR e.recorded_at<=?) '
        'AND EXISTS(SELECT 1 FROM memory_extraction_runs r WHERE r.event_id=e.id AND r.completed_at<m.changed_at) '
        'AND NOT EXISTS(SELECT 1 FROM memory_extraction_runs r WHERE r.event_id=e.id AND r.completed_at>=m.changed_at '
        'AND (? IS NULL OR r.completed_at<=?))'
        ')',(scope,scope,cutoff,cutoff,cutoff,cutoff,cutoff,cutoff)).fetchone()[0])


def historical_gaps(db,scope,cutoff):
    """Read audit status at its publication time, separate from latest queue health."""
    if cutoff is None: return False
    rows=db.execute('SELECT r.result FROM memory_extraction_runs r '
        'JOIN memory_raw_events e ON e.id=r.event_id WHERE (? IS NULL OR e.scope=?) '
        'AND e.recorded_at<=? AND r.completed_at<=? AND NOT EXISTS('
        'SELECT 1 FROM memory_extraction_runs newer WHERE newer.event_id=r.event_id '
        'AND newer.completed_at<=? AND (newer.completed_at>r.completed_at OR '
        '(newer.completed_at=r.completed_at AND newer.id>r.id)))',
        (scope,scope,cutoff,cutoff,cutoff))
    for row in rows:
        result=json.loads(row['result'])
        revision=result.get('source_revision')
        if revision is not None:
            if revision['status']!='accepted_complete': return True
            continue
        from .queue import _current_reviews
        if any(review.get('status') in ('unresolved','pending') for review in _current_reviews(result.get('reviews',[]))):
            return True
    return False


def request_audit(queue,event_id,gap_identity):
    if not isinstance(gap_identity,str) or not 1<=len(gap_identity)<=2000:
        raise ValueError('source audit requires a bounded material-gap identity')
    with queue.store.transaction(write=True) as db:
        schema(db)
        frozen=freeze(queue,event_id,db)
        if not db.execute('SELECT 1 FROM memory_extraction_runs WHERE event_id=? LIMIT 1',(event_id,)).fetchone():
            raise ValueError('source interpretation must exist before a material-gap audit')
        identity=hashlib.sha256(_json([event_id,PROTOCOL,frozen.fingerprint,gap_identity]).encode()).hexdigest()
        count=db.execute('SELECT count(*) FROM memory_source_audit_requests WHERE event_id=? AND protocol=? '
            'AND input_fingerprint=?',(event_id,PROTOCOL,frozen.fingerprint)).fetchone()[0]
        if count<MAX_GAP_AUDITS:
            db.execute('INSERT OR IGNORE INTO memory_source_audit_requests(id,event_id,protocol,input_fingerprint,status) VALUES(?,?,?,?,?)',
                (identity,event_id,PROTOCOL,frozen.fingerprint,'pending'))
        return identity


def admit_request(queue,db,frozen,gap_identity,evidence):
    """Honest internal admission; the legacy public hash return is not a receipt."""
    schema(db)
    identity=hashlib.sha256(_json([frozen.event.id,PROTOCOL,frozen.fingerprint,gap_identity]).encode()).hexdigest()
    row=db.execute('SELECT status,evidence_json FROM memory_source_audit_requests WHERE id=?',(identity,)).fetchone()
    if row:
        old=json.loads(row['evidence_json']) if row['evidence_json'] else None
        if old and row['status'] in ('pending','scheduled','refresh'):
            for origin in evidence['origins']:
                if not any(item['view_id']==origin['view_id'] and item['valid_as_of']==origin['valid_as_of'] and
                        item['recorded_as_of']==origin['recorded_as_of'] for item in old['origins']):
                    if len(old['origins'])>=8:return {'request_id':identity,'status':'origin_limit','scheduled':False}
                    old['origins'].append(origin)
            db.execute('UPDATE memory_source_audit_requests SET evidence_json=? WHERE id=?',(_json(old),identity))
        return {'request_id':identity,'status':'existing','scheduled':row['status'] in ('pending','scheduled','refresh')}
    count=db.execute('SELECT count(*) FROM memory_source_audit_requests WHERE event_id=? AND protocol=? AND input_fingerprint=?',
        (frozen.event.id,PROTOCOL,frozen.fingerprint)).fetchone()[0]
    if count>=MAX_GAP_AUDITS:return {'status':'cap_exhausted','scheduled':False}
    db.execute('INSERT OR IGNORE INTO memory_source_audit_requests(id,event_id,protocol,input_fingerprint,status,evidence_json) '
        'VALUES(?,?,?,?,?,?)',(identity,frozen.event.id,PROTOCOL,frozen.fingerprint,'pending',_json(evidence)))
    inserted=db.execute('SELECT 1 FROM memory_source_audit_requests WHERE id=?',(identity,)).fetchone()
    return {'request_id':identity,'status':'inserted' if inserted else 'unavailable','scheduled':bool(inserted)}


def prior_ids(db, event_id):
    ids = set()
    for row in db.execute('SELECT result FROM memory_extraction_runs WHERE event_id=? ORDER BY id', (event_id,)):
        result = json.loads(row['result'])
        ids.update(result.get('source_revision', result).get('assertion_ids', []))
    return ids - selection(db).inactive_ids


def _explicit_unavailable_before(event):
    """Generic adapter-declared source discontinuity; no fabricated source body."""
    descriptors = event.metadata.get('unavailable_context_before', [])
    if not isinstance(descriptors, list) or len(descriptors) > 128:
        raise ValueError('unavailable context descriptors must be a bounded list')
    result = []
    for index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, dict) or len(_json(descriptor)) > 8000:
            raise ValueError('unavailable context descriptor must be a bounded object')
        identity = hashlib.sha256(_json([event.id, index, descriptor]).encode()).hexdigest()
        result.append(replace(event, id='explicit-source-gap-' + identity, prompt='', actor='assistant',
            source='context://unavailable', turn_id='source-gap',
            metadata={'_source_unavailable': True, 'reason': 'explicit_source_unavailable',
                      'source_descriptor': descriptor}))
    return result


def freeze(queue, event_id, db=None):
    if db is None:
        with queue.store.transaction() as connection:
            return freeze(queue,event_id,connection)
    from .queue import _event
    target = db.execute('SELECT e.*,e.rowid AS intake,p.position,j.status FROM memory_raw_events e '
        'JOIN memory_capture_jobs j ON j.event_id=e.id '
        'LEFT JOIN memory_event_positions p ON p.event_id=e.id WHERE e.id=?', (event_id,)).fetchone()
    if target is None:
        raise ValueError('unknown raw source')
    positioned = target['position'] is not None
    # Unpositioned earlier captures remain explicit unavailable gaps when the
    # target has an authenticated position. Future appends never enter its prefix.
    bound = ('(p.position<=? OR (p.position IS NULL AND e.rowid<=?))' if positioned else 'e.rowid<=?')
    limits = (target['position'],target['intake']) if positioned else (target['intake'],)
    rows = db.execute('SELECT e.*,e.rowid AS intake,p.position,j.status FROM memory_raw_events e '
        'JOIN memory_capture_jobs j ON j.event_id=e.id LEFT JOIN memory_event_positions p ON p.event_id=e.id '
        'WHERE e.session_id=? AND e.scope=? AND '+bound+
        (' ORDER BY coalesce(p.position,-1),e.rowid' if positioned else ' ORDER BY e.rowid'),
        (target['session_id'],target['scope'],*limits)).fetchall()
    events, descriptor = [], []
    previous_position = None
    previous_metadata = {}
    for row in rows:
        event = _event(row)
        position = row['position']
        byte_neighbors = (event.metadata.get('source_position_unit') == previous_metadata.get('source_position_unit') == 'byte'
            and event.metadata.get('source_position_origin') is not None
            and event.metadata.get('source_position_origin') == previous_metadata.get('source_position_origin'))
        if positioned and previous_position is not None and position is not None and position>previous_position+1 and not byte_neighbors:
            gap={'position_gap':[previous_position+1,position-1]}
            descriptor.append(gap)
            gap_id='position-gap-'+hashlib.sha256(_json([event.session_id,event.scope,gap]).encode()).hexdigest()
            events.append(replace(event,id=gap_id,prompt='',metadata={'_source_unavailable':True},
                actor='assistant',source='context://unavailable',turn_id='position-gap'))
        if position is not None:
            previous_position=position
            previous_metadata=event.metadata
        events.extend(_explicit_unavailable_before(event))
        unavailable = row['status']=='waiting_origin' or (positioned and row['position'] is None)
        descriptor.append({'id':event.id,'position':row['position'],'intake':row['intake'],
            'unavailable':unavailable,'raw':hashlib.sha256(_json({
                'prompt':event.prompt,'actor':event.actor,'source':event.source,'scope':event.scope,
                'recorded_at':event.recorded_at,'metadata':event.metadata}).encode()).hexdigest()})
        if unavailable:
            event = replace(event,prompt='',metadata={'_source_unavailable':True})
        events.append(event)
    order={'basis':'transcript_position' if positioned else 'intake_order',
        'has_recorded_source_position':positioned,
        'excluded_unpositioned_event_count':sum(row['position'] is None for row in rows) if positioned else 0}
    return FrozenInput(_event(target),tuple(events),hashlib.sha256(_json([PROTOCOL,descriptor]).encode()).hexdigest(),
        order,frozenset(prior_ids(db,event_id)))


def _schedule(queue, db, row, *, changed_at=None, force=False):
    # This cursor only reads metadata. Freezing raw source/context and loading
    # prior proof happens once after a worker claims the finite audit.
    if row['status']=='waiting_origin':
        return
    if not db.execute('SELECT 1 FROM memory_extraction_runs WHERE event_id=? LIMIT 1',
                      (row['id'],)).fetchone():
        # Legacy assistant input was marked completed without any extraction.
        # Upgrade the job only; retain its exact immutable raw event/provenance.
        if row['status']=='completed' and (row['actor']=='assistant' or row['role']=='assistant'):
            db.execute("UPDATE memory_capture_jobs SET status='waiting_model',phase='extract',attempts=0,"
                "error=NULL,updated_at=?,available_at=? WHERE event_id=? AND status='completed'",
                (queue._time(),queue._time(),row['id']))
        return
    current = db.execute('SELECT * FROM memory_source_audits WHERE event_id=?',(row['id'],)).fetchone()
    if not force and changed_at is None and current and current['protocol']==PROTOCOL:
        return
    if changed_at is not None and db.execute('SELECT 1 FROM memory_extraction_runs '
            'WHERE event_id=? AND completed_at>=? LIMIT 1',(row['id'],changed_at)).fetchone():
        return
    db.execute('INSERT INTO memory_source_audits VALUES(?,?,?,NULL,\'pending\',NULL) '
        'ON CONFLICT(event_id) DO UPDATE SET protocol=excluded.protocol,desired_input=excluded.desired_input,'
        "status='pending',reason=NULL", (row['id'],PROTOCOL,'pending-context' if changed_at else 'pending-protocol'))
    # Never steal a live extraction/link lease. The commit fence catches a stale
    # running completion even before this bounded cursor reaches its source.
    db.execute("UPDATE memory_capture_jobs SET status='pending',phase='audit',attempts=0,error=NULL,"
        'updated_at=?,available_at=? WHERE event_id=? AND status IN (\'completed\',\'failed\')',
        (queue._time(),queue._time(),row['id']))


def initialize_admission(db):
    """Pin rollout highwater before intake; no historical proof is upgraded.

    The marker is an operational cursor, not a source compatibility certificate.
    Existing durable jobs, context changes and explicit audit/policy requests are
    separate obligations and remain eligible even below this admission boundary.
    Call inside the service's startup write transaction before first capture.
    """
    schema(db)
    marker='explicit-history-admission-v1:'+PROTOCOL
    existing=db.execute('SELECT cursor FROM memory_source_audit_cursor WHERE protocol=?',(marker,)).fetchone()
    if existing is not None:return existing[0]
    highwater=db.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0]
    db.execute('INSERT INTO memory_source_audit_cursor VALUES(?,?)',(marker,highwater))
    db.execute('INSERT INTO memory_source_audit_cursor VALUES(?,?) ON CONFLICT(protocol) '
        'DO UPDATE SET cursor=max(cursor,excluded.cursor)',(PROTOCOL,highwater))
    return highwater


def tick(queue):
    """One bounded page per trigger, with no model calls on capture paths."""
    with queue.store.transaction(write=True) as db:
        schema(db)
        initialize_admission(db)
        cursor=db.execute('SELECT cursor FROM memory_source_audit_cursor WHERE protocol=?',(PROTOCOL,)).fetchone()[0]
        rows=db.execute('SELECT e.id,e.actor,json_extract(e.metadata,\'$.role\') AS role,e.rowid AS intake,j.status FROM memory_raw_events e '
            'JOIN memory_capture_jobs j ON j.event_id=e.id WHERE e.rowid>? ORDER BY e.rowid LIMIT ?',
            (cursor,SCAN_LIMIT)).fetchall()
        for row in rows: _schedule(queue,db,row)
        if rows: db.execute('UPDATE memory_source_audit_cursor SET cursor=? WHERE protocol=?',(rows[-1]['intake'],PROTOCOL))
        requests=db.execute('SELECT r.id AS request_id,r.input_fingerprint,r.evidence_json,e.id,e.actor,json_extract(e.metadata,\'$.role\') AS role,j.status '
            'FROM memory_source_audit_requests r JOIN memory_raw_events e ON e.id=r.event_id '
            'JOIN memory_capture_jobs j ON j.event_id=e.id WHERE r.status=\'pending\' '
            'AND j.status IN (\'completed\',\'failed\') ORDER BY r.rowid LIMIT ?',(SCAN_LIMIT,)).fetchall()
        for row in requests:
            frozen=freeze(queue,row['id'],db)
            evidence=json.loads(row['evidence_json']) if row['evidence_json'] else None
            from .source_omission import revision
            if row['input_fingerprint']!=frozen.fingerprint or (evidence and evidence['source_revision']!=revision(db,row['id'])):
                db.execute("UPDATE memory_source_audit_requests SET status='stale' WHERE id=?",(row['request_id'],));continue
            _schedule(queue,db,row,force=True)
            db.execute("UPDATE memory_source_audit_requests SET status='scheduled' WHERE id=?",(row['request_id'],))
        marker=db.execute('SELECT * FROM memory_source_context_changes ORDER BY id LIMIT 1').fetchone()
        if marker:
            rows=db.execute('SELECT e.id,e.actor,json_extract(e.metadata,\'$.role\') AS role,e.rowid AS intake,j.status FROM memory_raw_events c '
                'LEFT JOIN memory_event_positions cp ON cp.event_id=c.id '
                'JOIN memory_raw_events e JOIN memory_capture_jobs j ON j.event_id=e.id '
                'LEFT JOIN memory_event_positions p ON p.event_id=e.id '
                'WHERE c.id=? AND '+_SUFFIX+' AND e.rowid>? ORDER BY e.rowid LIMIT ?',
                (marker['event_id'],marker['cursor'],SCAN_LIMIT)).fetchall()
            for row in rows: _schedule(queue,db,row,changed_at=marker['changed_at'])
            if len(rows)<SCAN_LIMIT:
                db.execute('DELETE FROM memory_source_context_changes WHERE id=?',(marker['id'],))
            else:
                db.execute('UPDATE memory_source_context_changes SET cursor=? WHERE id=?',
                    (rows[-1]['intake'],marker['id']))


def receipt(reason, refs, verifier='contextual-entailment/v1'):
    return {'status':'accepted','verifier':verifier,'reason':reason,
        'evidence_refs':[{key:ref[key] for key in ('event_id','start','end')} for ref in refs]}


def coverage_accepted(extraction):
    audits=[row for row in extraction.get('context_recovery',[]) if row.get('operation')=='source_coverage']
    return bool(audits and audits[-1].get('status')=='accepted')


def _old_claim(assertion, frozen, window):
    sources={event.id:event for event in frozen.events if not event.metadata.get('_source_unavailable')}
    refs=[]
    for ref in assertion.metadata.get('evidence_refs',[]):
        source=sources.get(ref.get('event_id'))
        if source is None: raise ModelError('Prior evidence is no longer authorized.')
        start,end=ref.get('start'),ref.get('end')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(source.prompt):
            raise ModelError('Prior evidence has invalid immutable bounds.')
        refs.append({**ref,'quote':source.prompt[start:end]})
    if not refs or not any(ref['event_id']==frozen.event.id for ref in refs):
        raise ModelError('Prior interpretation has no exact target proof.')
    claim={'text':assertion.text,'actor':assertion.actor,'source':assertion.source,
        'episode':assertion.episode,'scope':assertion.scope,
        **({'kind':assertion.metadata['kind']} if 'kind' in assertion.metadata else {}),
        'quote':assertion.metadata.get('excerpt',''),'source_span':assertion.metadata.get('source_span'),
        'evidence_refs':refs}
    for key in ('valid_at','temporal_precision','temporal_evidence'):
        value=assertion.valid_at if key=='valid_at' else assertion.metadata.get(key)
        if value is not None: claim[key]=value
    window.validate_references(claim)
    return claim


_WITHDRAW = ('Independently verify a proposed interpretation withdrawal. Prior canonical text and the '
    'previous rejection are hypotheses, never source evidence. Return JSON {"withdrawals":['
    '{"assertion_id":"prior ID","status":"accepted|unresolved","reason":"source-grounded reason",'
    '"evidence":[{"event_id":"raw source id","quote":"exact supplied contiguous text","start":0}]}]}. '
    'Accept ONLY when the actual supplied raw target/preceding context positively establishes that this '
    'specific attributed proposition is unsupported or misattributed. Mere omission, ambiguity, incomplete '
    'context, or failure is unresolved. Evidence must anchor the target event. No invented quotes. '
    'An absolute valid_at is part of the specific hypothesis: a relative-only raw message and '
    'knowledge/ingestion clock cannot substantiate an absolute date. A source-clock/calendar limitation '
    'can invalidate that specific absolute-date strengthening while the attributed undated relative '
    'meaning remains supportable; do not withdraw independently supported absolute source-body dates. '
    'You may request bounded context_requests before deciding. Account for every listed prior ID.')


def _withdrawals(model, frozen, window, rejected):
    from .dialogue import response_object, _source_ref
    result={}
    if not rejected: return result
    for attempt in range(7):
        records=[*window.records(),*rejected]
        from .source_message_clock import CLOCK_RULES
        response=response_object(model,ModelRequest('Verify source interpretation withdrawals',records,_WITHDRAW+CLOCK_RULES))
        if response.get('context_requests'):
            if window.exhausted or attempt==6: return result
            window.recover(response['context_requests']); continue
        values=response.get('withdrawals')
        if not isinstance(values,list) or len(values)!=len(rejected):
            raise ModelError('Withdrawal verifier must account for every rejected prior meaning.')
        allowed={row['assertion_id'] for row in rejected}; seen=set()
        for value in values:
            key=value.get('assertion_id') if isinstance(value,dict) else None
            if key not in allowed or key in seen: raise ModelError('Invalid withdrawal identity.')
            seen.add(key)
            if value.get('status')=='unresolved': continue
            if value.get('status')!='accepted' or not isinstance(value.get('reason'),str) or not value['reason']:
                raise ModelError('Invalid withdrawal verdict.')
            refs=[]
            if not isinstance(value.get('evidence'),list) or not 1<=len(value['evidence'])<=64:
                raise ModelError('Withdrawal requires bounded exact source proof.')
            for ref in value['evidence']:
                source=window.sources.get(ref.get('event_id'))
                if source is None: raise ModelError('Withdrawal proof is not authorized.')
                refs.append({**_source_ref(source,ref.get('quote'),start=ref.get('start'))})
            if not any(ref['event_id']==frozen.event.id for ref in refs):
                raise ModelError('Withdrawal must anchor target raw source.')
            window.validate_references({'evidence_refs':refs})
            result[key]=receipt(value['reason'],refs,'source-withdrawal-independent/v1')
        return _withdrawal_coverage(model,window,result,rejected)
    return result


_WITHDRAW_COVERAGE = (
    'Independently check each previously verified source interpretation withdrawal against the additional '
    'full-source chunk marked coverage_scan. The verified_withdrawal record is a hypothesis and receipt, '
    'never source evidence. Its exact raw target proof remains supplied in the base records on every pass. '
    'Decide whether this chunk contains qualifications, adoption, attribution, conditions or other content '
    'that undermines that source-grounded invalidation. Do not require this single chunk to establish the '
    'whole withdrawal by itself; evaluate it together with the stable actually supplied target proof and '
    'authorized preceding context. Do not infer missing content, change the base proof, or turn a prior '
    'critique into facts. A later actual adoption or qualification can defeat an apparent earlier rejection. '
    'Return JSON {"withdrawals":[{"assertion_id":"existing prior ID",'
    '"status":"accepted|unresolved","reason":"why this chunk does or does not undermine the proof"}]}. '
    'Account for every supplied verified_withdrawal exactly once. Accepted means this chunk does not '
    'undermine the independently established raw proof. Ambiguous or needed unavailable context means '
    'unresolved. Do not return new assertions, replacement proof or semantic links.')


def _withdrawal_coverage(model,window,verified,rejected):
    """Every bounded full-source pass preserves the actually supplied base proof."""
    from .dialogue import response_object
    from .source_message_clock import CLOCK_RULES
    if not verified: return verified
    prior={row['assertion_id']:row for row in rejected}
    claims=[{'evidence_refs':[*proof['evidence_refs'],*prior[key]['evidence_refs']]}
            for key,proof in verified.items()]
    chunks=window.coverage_records(claims)
    if chunks is None:
        window.audit.append({'operation':'source_withdrawal_coverage','status':'unresolved',
            'reason':'Relevant full sources exceed the existing bounded chunk budget.'})
        return {}
    base=window.records()
    remaining=dict(verified)
    for chunk in chunks:
        hypotheses=[{'role':'verified_withdrawal','assertion_id':key,
            'interpretation':{field:prior[key][field] for field in ('text','actor','source','episode','scope',
                'valid_at','temporal_precision','temporal_evidence') if field in prior[key]},
            'verification':proof} for key,proof in remaining.items()]
        response=response_object(model,ModelRequest('Check source withdrawals against full-source coverage',
            [*base,chunk,*hypotheses],_WITHDRAW_COVERAGE+CLOCK_RULES))
        values=response.get('withdrawals')
        if response.get('context_requests') or not isinstance(values,list) or len(values)!=len(remaining):
            raise ModelError('Full-source withdrawal review is incomplete or requires unavailable context.')
        seen=set(); statuses={}
        for value in values:
            if not isinstance(value,dict) or set(value)!={'assertion_id','status','reason'}:
                raise ModelError('Invalid full-source withdrawal verdict.')
            key=value['assertion_id']; status=value['status']; reason=value['reason']
            if (key not in remaining or key in seen or status not in ('accepted','unresolved')
                    or not isinstance(reason,str) or not 1<=len(reason)<=2000):
                raise ModelError('Full-source withdrawal review must account for each prior ID.')
            seen.add(key); statuses[key]=status
        window.audit.append({'operation':'source_withdrawal_coverage','event_id':chunk['id'],
            **chunk['source_span'],'status':'checked','verdicts':statuses})
        remaining={key:proof for key,proof in remaining.items() if statuses[key]=='accepted'}
        if not remaining: break
    return remaining


def audit(model, queue, frozen, *, candidates=None, before_model=None):
    from .dialogue import (_with_before_model,_context_records,_event_record,MAX_CONTEXT_CHARACTERS,
        _verify_coverage,_review_claims,_dialogue_result,_operational_error,_source_operation_error)
    from .dialogue_context import ContextWindow
    from .source_coverage import review_source_coverage
    model=_with_before_model(model,before_model)
    event=frozen.event; context=list(frozen.events)
    records,_=_context_records(event,context)
    window=ContextWindow(event,context,records,_event_record,budget=MAX_CONTEXT_CHARACTERS,candidates=candidates)
    accepted,reviews,retained,rejected,withdrawn=[],[],{},[],{}
    checked=[]
    try:
        # Bounds match existing extraction capacity; no wholesale legacy replay.
        if len(frozen.prior_ids)>32: raise ModelError('Prior set exceeds bounded interpretation capacity.')
        claims=[]; keys=[]
        for key in sorted(frozen.prior_ids):
            try:
                claim=_old_claim(queue.store.get_assertion(key),frozen,window)
            except ModelError as error:
                reviews.append({'kind':'prior_reconciliation','status':'unresolved','reason':str(error),'evidence_refs':[]})
                continue
            claims.append(claim); keys.append(key)
        checked=_verify_coverage(model,window.records(),claims,window.truncated,window) if claims else []
        good,audited=_review_claims(claims,checked); accepted.extend(good); reviews.extend(audited)
        for key,claim,verdict in zip(keys,claims,checked):
            suitable=(claim['actor']==event.actor and claim['source']==event.source and
                claim['episode']==event.session_id and claim['scope']==event.scope)
            if verdict['status']=='accepted' and suitable:
                claim['_retained_id']=key
                retained[key]=receipt(verdict['reason'],claim['evidence_refs'])
            elif verdict['status']=='unsupported' or not suitable:
                if claim in accepted: accepted.remove(claim)
                rejected.append({'role':'withdrawal_hypothesis','assertion_id':key,
                    **{field:claim[field] for field in ('text','actor','source','episode','scope','evidence_refs',
                        'valid_at','temporal_precision','temporal_evidence') if field in claim},
                    'reason':verdict['reason'] if suitable else
                        'Check immutable attribution/provenance against the actual target source.'})
        withdrawn=_withdrawals(model,frozen,window,rejected)
        from .source_omission import critiques
        critique=critiques(frozen,window)
        review_source_coverage(model,event,context,window,accepted,reviews,query_omissions=critique)
    except Exception as error:
        if not _source_operation_error(error):raise
        # Keep any already independently established specific withdrawal; a later
        # coverage/model failure cannot turn it into a bulk removal.
        reviews.append({'kind':'source_audit','status':'unresolved','reason':str(error),'evidence_refs':[]})
        window.audit.append({'operation':'source_coverage','status':'unresolved','reason':str(error),
                             'operational_error':_operational_error(error)})
    grounded=[row['request_id'] for row in frozen.requests if row['evidence']]
    if grounded and not any(row.get('operation')=='query_source_omission' for row in window.audit):
        window.audit.append({'operation':'query_source_omission','status':'unresolved','dispositions':[
            {'request_id':key,'disposition':'unresolved','reason':'Independent source audit did not reach gap adjudication.'}
            for key in grounded]})
    extraction=_dialogue_result(window,accepted,reviews)
    extraction.update(input_fingerprint=frozen.fingerprint,prior_ids=sorted(frozen.prior_ids),
        retained=retained,withdrawn=withdrawn,context_order=frozen.order)
    extraction['source_audit_request_ids']=[row['request_id'] for row in frozen.requests]
    from .source_omission import _admission_hash
    admissions={row['request_id']:_admission_hash(row['evidence']) for row in frozen.requests if row['evidence']}
    if admissions:
        extraction['context_recovery']=[{'operation':'source_audit_admission','bindings':admissions},
                                        *extraction['context_recovery']]
    extraction['reconciliation_complete']=set(retained)|set(withdrawn)==set(frozen.prior_ids)
    if not coverage_accepted(extraction):
        # A program-derived terminal context limitation is a completed semantic
        # audit even when there is no positive claim to verify. Provider/format
        # failures lack this receipt and retain their failed operational status.
        recovery=window.audit[-1].get('recovery',{}) if window.audit else {}
        terminal_context=recovery.get('schema')=='source-context-exhaustion/v1'
        from .answer_source_gaps import semantic_terminal
        # Empty prior membership is a valid comparison set. Reuse the existing
        # parsed semantic receipt boundary; transport/schema/budget failures lack
        # it and remain operational failures even when there were no prior claims.
        terminal_semantic=semantic_terminal({**extraction,'source_revision':{'status':'unresolved'}})
        extraction['outcome']='failed' if not checked and not retained and not withdrawn and not terminal_context and not terminal_semantic else 'unresolved'
    return extraction


def legacy_coverage(db, scope=None):
    """Read-only policy accounting; old receipts are not current-source clearance."""
    rows=db.execute("SELECT count(*) AS total, "
        "coalesce(sum(a.protocol=?),0) AS current_policy, "
        "coalesce(sum((a.protocol IS NULL OR a.protocol!=?) AND ("
        "EXISTS(SELECT 1 FROM memory_extraction_runs r WHERE r.event_id=e.id) "
        "OR j.status='completed')),0) AS legacy, "
        "coalesce(sum(a.protocol=? AND a.status='accepted'),0) AS accepted_current "
        "FROM memory_raw_events e LEFT JOIN memory_source_audits a ON a.event_id=e.id "
        "LEFT JOIN memory_capture_jobs j ON j.event_id=e.id WHERE (? IS NULL OR e.scope=?)",
        (PROTOCOL,PROTOCOL,PROTOCOL,scope,scope)).fetchone()
    return {'source_policy':PROTOCOL,'event_count':rows[0],
        'current_protocol_event_count':rows[1],'legacy_event_count':rows[2],
        'unprocessed_event_count':rows[0]-rows[1]-rows[2],
        'current_accepted_event_count':rows[3],
        'coverage_current':rows[0]==rows[3],
        'historical_reprocessing':'explicit_component_scope_range'}
