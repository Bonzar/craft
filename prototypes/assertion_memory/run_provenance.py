"""Append-only execution causality. Never inferred from time, scope membership or prose.

Worker mutation boundaries record exactly which coalesced work generations they
advanced. Explicit run contexts bind actual immutable publications to those roots.
Old/uninstrumented work remains unknown. This metadata never changes pipeline keys.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from uuid import uuid4

_ACTIVE = ContextVar('memory_execution_provenance', default=None)
TABLES = ('memory_run_provenance', 'memory_run_causes', 'memory_run_cause_links', 'memory_run_events')


_NATIVE_SESSION = ("CASE WHEN json_type(raw.metadata,'$.native_session_id')='text' "
    "AND length(trim(json_extract(raw.metadata,'$.native_session_id')))>0 "
    "THEN json_extract(raw.metadata,'$.native_session_id') END")
_SOURCE_SESSION = 'coalesce('+_NATIVE_SESSION+',raw.session_id)'


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS memory_run_provenance (
        run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL, scope TEXT,
        started_at TEXT NOT NULL, cause_status TEXT NOT NULL, frontier TEXT NOT NULL, snapshot TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS memory_run_causes (
        id INTEGER PRIMARY KEY, scope TEXT, generation_from INTEGER, generation_to INTEGER,
        source_event_id TEXT, assertion_id TEXT, job_id TEXT NOT NULL, recorded_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS memory_run_cause_links (
        run_id TEXT NOT NULL REFERENCES memory_run_provenance(run_id),
        cause_id INTEGER NOT NULL REFERENCES memory_run_causes(id), PRIMARY KEY(run_id,cause_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS memory_run_events (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES memory_run_provenance(run_id),
        kind TEXT NOT NULL, outcome TEXT NOT NULL, recorded_at TEXT NOT NULL,
        view_id TEXT, version_id INTEGER REFERENCES memory_view_versions(id),
        check_id INTEGER REFERENCES memory_view_checks(id), upstream TEXT NOT NULL,
        review_status TEXT, failure_proof INTEGER NOT NULL DEFAULT 0)''')
    db.execute('CREATE INDEX IF NOT EXISTS memory_run_causes_generation ON memory_run_causes(scope,generation_to)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_run_causes_source ON memory_run_causes(source_event_id)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_run_links_cause ON memory_run_cause_links(cause_id,run_id)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_run_events_owner ON memory_run_events(run_id,id)')
    for table in TABLES:
        for operation in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                       "BEGIN SELECT RAISE(ABORT,'execution provenance is append-only'); END")


def _exists(db,table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None


def frontiers(db):
    return {row['scope']:int(json.loads(row['desired_frontier']).get('generation',0))
            for row in db.execute('SELECT scope,desired_frontier FROM memory_derived_work')}


def _source_event(db,event_id,assertion_id):
    if not _exists(db,'memory_raw_events'):
        return None
    if event_id is None and assertion_id:
        row=db.execute("SELECT json_extract(metadata,'$.event_id') FROM memory_assertions WHERE assertion_id=?",
                       (assertion_id,)).fetchone()
        event_id=row[0] if row else None
    return event_id if event_id and db.execute('SELECT 1 FROM memory_raw_events WHERE id=?',(event_id,)).fetchone() else None


def record_changes(db,before,*,job_id,event_id=None,assertion_id=None,recorded_at=None):
    """Call in the SAME owned transaction as source/link publication, after the mutation."""
    schema(db)
    event_id=_source_event(db,event_id,assertion_id)
    ids=[]
    for scope,generation in frontiers(db).items():
        previous=before.get(scope,0)
        if generation<=previous:continue
        row=db.execute('INSERT INTO memory_run_causes(scope,generation_from,generation_to,source_event_id,assertion_id,job_id,recorded_at) '
            'VALUES(?,?,?,?,?,?,?)',(scope,previous,generation,event_id,assertion_id,job_id,recorded_at or _now()))
        ids.append(row.lastrowid)
    return ids


def outcome(db,run_id,value,*,recorded_at=None):
    return db.execute('INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,upstream) VALUES(?,?,?,?,?)',
                      (run_id,'lifecycle',value,recorded_at or _now(),'{}')).lastrowid


def _begin(db,kind,owner,scope,causes,status,frontier,snapshot,now):
    run_id=uuid4().hex
    db.execute('INSERT INTO memory_run_provenance VALUES(?,?,?,?,?,?,?,?)',
               (run_id,kind,owner,scope,now,status,_json(frontier),_json(snapshot)))
    db.executemany('INSERT INTO memory_run_cause_links VALUES(?,?)',((run_id,key) for key in causes))
    outcome(db,run_id,'started',recorded_at=now)
    return run_id


def begin_derived(db,scope,frontier,completed_frontier,snapshot,*,now=None):
    schema(db)
    previous=int((completed_frontier or {}).get('generation',0))
    end=int(frontier.get('generation',0))
    rows=db.execute('SELECT * FROM memory_run_causes WHERE scope=? AND generation_to>? AND generation_to<=? '
                    'ORDER BY generation_from,generation_to,id',(scope,previous,end)).fetchall()
    covered=previous
    missing=False
    for row in rows:
        if row['generation_from']>covered:missing=True
        covered=max(covered,row['generation_to'])
    status=('complete' if rows and covered>=end and not missing and all(r['source_event_id'] for r in rows)
            else 'partial' if rows else 'unknown')
    return _begin(db,'derived_scope',scope,scope,[r['id'] for r in rows],status,frontier,snapshot,now or _now())


def active(engine):
    value=_ACTIVE.get()
    return value[1] if value and value[0] is engine.store else None


@contextmanager
def bind(engine,run_id):
    token=_ACTIVE.set((engine.store,run_id))
    try:yield run_id
    finally:_ACTIVE.reset(token)


@contextmanager
def execution(engine,*,owner_id,event_id=None,assertion_id=None,request_session_id=None):
    """Bind actual execution to its initiating operation, preserving nested builds.

    request_session_id is optional explicit caller context, not authenticated raw
    source provenance. Legacy callers that omit it remain unrecorded/unknown.
    """
    if request_session_id is not None and (not isinstance(request_session_id,str)
            or not request_session_id.strip() or len(request_session_id)>2048):
        raise ValueError('bounded explicit request_session_id required')
    prior=active(engine)
    if prior is not None:
        yield prior
        return
    with engine.store.transaction(write=True) as db:
        schema(db)
        event_id=_source_event(db,event_id,assertion_id)
        now=_now()
        row=db.execute('INSERT INTO memory_run_causes(source_event_id,assertion_id,job_id,recorded_at) VALUES(?,?,?,?)',
                       (event_id,assertion_id,owner_id,now))
        run_id=_begin(db,'direct_request' if request_session_id is not None else 'source_refresh',
                      owner_id,None,[row.lastrowid],
                      'complete' if event_id else 'caller_context_only' if request_session_id is not None else 'unknown',
                      {'request_session_id':request_session_id} if request_session_id is not None else {},{},now)
    with bind(engine,run_id):
        try:
            yield run_id
        except BaseException as error:
            label='yielded' if type(error).__name__ in ('_RefreshYield','DerivedYield') else 'failed'
            with engine.store.transaction(write=True) as db:outcome(db,run_id,label)
            raise
        else:
            with engine.store.transaction(write=True) as db:outcome(db,run_id,'completed')


def publication(engine,db,version,check_id,*,produced):
    run_id=active(engine)
    if run_id is None:return
    # Publication and causal receipt share the transaction; no log-only winner.
    db.execute('INSERT INTO memory_run_events(run_id,kind,outcome,recorded_at,view_id,version_id,check_id,upstream,review_status,failure_proof) '
        'VALUES(?,?,?,?,?,?,?,?,?,?)',(run_id,'view','produced' if produced else 'reused',_now(),
                                 version.view_id,version.id,check_id,_json(version.upstream),
                                 (version.overview_review or {}).get('status'),
                                 int(bool((version.overview_review or {}).get('failure_proof')))))


def _snapshot(db,snapshot_id):
    maximum=db.execute('SELECT coalesce(max(id),0) FROM memory_run_events').fetchone()[0]
    if snapshot_id is None:return maximum
    if type(snapshot_id) is not int or not 0<=snapshot_id<=maximum:raise ValueError('invalid provenance snapshot')
    return snapshot_id


def session_outputs(db,session_id,*,offset=0,limit=50,snapshot_id=None):
    """Bounded immutable version pins from recorded causes, never source membership guesses."""
    if not isinstance(session_id,str) or not session_id.strip() or len(session_id)>2048:
        raise ValueError('bounded session_id required')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('provenance page requires offset>=0 and limit 1..100')
    base={'session_id':session_id,'causal_coverage':'recorded_runs_only',
          'unrecorded_history':'unknown','session_identity_basis':'explicit_native_session_or_stored_fallback',
          'caller_context':'explicit_only_not_source_authentication','offset':offset,'entries':[],'total':0,'next_offset':None,'snapshot_id':0,'response_complete':True}
    if not _exists(db,'memory_run_events'):return base
    pinned=_snapshot(db,snapshot_id)
    matching=[];arguments=[]
    if _exists(db,'memory_raw_events'):
        matching.append("""SELECT l.run_id FROM memory_run_cause_links l
            JOIN memory_run_causes c ON c.id=l.cause_id
            JOIN memory_raw_events raw ON raw.id=c.source_event_id
            WHERE """+_SOURCE_SESSION+'=?')
        arguments.append(session_id)
    # Resolve each matching run once, rather than revisiting a coalesced run's
    # entire cause list for every output event. UNION deduplicates source/caller
    # matches. Both total and page share this materialization in ONE statement.
    matching.append("SELECT run_id FROM memory_run_provenance WHERE kind='direct_request' "
                    "AND json_extract(frontier,'$.request_session_id')=?")
    arguments.append(session_id)
    join=(' FROM memory_run_events e JOIN matching_runs m ON m.run_id=e.run_id '
          'JOIN memory_run_provenance r ON r.run_id=e.run_id '
          "WHERE e.kind='view' AND e.id<=? ")
    statement=('WITH matching_runs AS MATERIALIZED ('+' UNION '.join(matching)+'), '
        'totals AS (SELECT count(*) AS total_count'+join+'), '
        'paged AS MATERIALIZED (SELECT e.*,r.kind AS run_kind,r.cause_status'+join+
        'ORDER BY e.id LIMIT ? OFFSET ?) '
        'SELECT totals.total_count,paged.* FROM totals LEFT JOIN paged ON 1=1 ORDER BY paged.id')
    fetched=db.execute(statement,[*arguments,pinned,pinned,limit,offset]).fetchall()
    total=fetched[0]['total_count']
    rows=[row for row in fetched if row['id'] is not None]
    entries=[]
    for row in rows:
        entry={key:row[key] for key in ('run_id','run_kind','cause_status','outcome','recorded_at','view_id','version_id','check_id','review_status','failure_proof')}
        entry.update(event_id=row['id'],upstream_count=len(json.loads(row['upstream'])),failure_proof=bool(row['failure_proof']))
        if len(_json(entry))>5000:
            entry={'event_id':row['id'],'run_id':row['run_id'],'details_required':True}
        if entries and len(_json(entries+[entry]))>10000:break
        entries.append(entry)
    end=offset+len(entries)
    return {**base,'entries':entries,'total':total,'snapshot_id':pinned,'next_offset':end if end<total else None,
            'response_complete':end>=total}


def _page(value,*,offset,limit):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=6000:
        raise ValueError('provenance section requires offset>=0 and limit 1..6000')
    text=_json(value)
    if offset>len(text):raise ValueError('offset beyond provenance section')
    end=min(len(text),offset+limit)
    return {'encoding':'json','content':text[offset:end],'offset':offset,'end':end,'total_characters':len(text),
            'next_offset':end if end<len(text) else None,'response_complete':end==len(text)}


def event_page(db,event_id,*,offset=0,limit=6000):
    if type(event_id) is not int or event_id<1:raise ValueError('positive provenance event_id required')
    if not _exists(db,'memory_run_events'):raise ValueError('unknown provenance event')
    row=db.execute('SELECT * FROM memory_run_events WHERE id=?',(event_id,)).fetchone()
    if row is None:raise ValueError('unknown provenance event')
    value=dict(row);value['upstream']=json.loads(value['upstream'])
    return {'event_id':event_id,**_page(value,offset=offset,limit=limit)}


def run_page(db,run_id,*,section='run',offset=0,limit=6000,snapshot_id=None):
    if not isinstance(run_id,str) or not run_id or len(run_id)>128:raise ValueError('bounded run_id required')
    if not _exists(db,'memory_run_provenance'):raise ValueError('unknown execution run')
    row=db.execute('SELECT * FROM memory_run_provenance WHERE run_id=?',(run_id,)).fetchone()
    if row is None:raise ValueError('unknown execution run')
    pinned=_snapshot(db,snapshot_id)
    if section=='run':
        value=dict(row)
        for key in ('frontier','snapshot'):value[key]=json.loads(value[key])
    elif section=='causes':
        if _exists(db,'memory_raw_events'):
            value=[dict(r) for r in db.execute('SELECT c.*,raw.session_id AS capture_session_id,'+
                _NATIVE_SESSION+' AS native_session_id,'+_SOURCE_SESSION+' AS source_session_id,'+
                "CASE WHEN raw.id IS NULL THEN 'unknown' WHEN ("+_NATIVE_SESSION+
                ") IS NOT NULL THEN 'metadata.native_session_id' ELSE 'stored_session_id_fallback' END AS session_id_basis "+
                'FROM memory_run_causes c JOIN memory_run_cause_links l ON l.cause_id=c.id '
                'LEFT JOIN memory_raw_events raw ON raw.id=c.source_event_id WHERE l.run_id=? ORDER BY c.id',(run_id,))]
        else:
            value=[dict(r,source_session_id=None,native_session_id=None,capture_session_id=None,session_id_basis='unknown')
                for r in db.execute('SELECT c.* FROM memory_run_causes c JOIN memory_run_cause_links l '
                    'ON l.cause_id=c.id WHERE l.run_id=? ORDER BY c.id',(run_id,))]
    elif section=='events':
        value=[dict(r) for r in db.execute('SELECT * FROM memory_run_events WHERE run_id=? AND id<=? ORDER BY id',(run_id,pinned))]
        for item in value:item['upstream']=json.loads(item['upstream'])
    else:raise ValueError('unknown provenance section')
    return {'run_id':run_id,'section':section,'snapshot_id':pinned,**_page(value,offset=offset,limit=limit)}
