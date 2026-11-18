"""Processing permission and causal cohorts, separate from readable knowledge."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
from .session_api import _SOURCE_SESSION

_COHORT=ContextVar('thought_processing_cohort',default=None)


def normalize_session_ids(values):
    if values is None:return None
    if not isinstance(values,(tuple,list)) or any(not isinstance(value,str) or not value.strip() for value in values):
        raise ValueError('processing_session_ids must be null or nonempty session ID strings')
    return tuple(dict.fromkeys(values))


@contextmanager
def processing_cohort(session_ids):
    token=_COHORT.set(normalize_session_ids(session_ids))
    try:yield
    finally:_COHORT.reset(token)


def current_cohort():
    return _COHORT.get()


def canonical_session_id(raw):
    metadata=raw.metadata if hasattr(raw,'metadata') else raw.get('metadata',{})
    if isinstance(metadata,str):metadata=json.loads(metadata)
    native=metadata.get('native_session_id')
    return native if isinstance(native,str) and native.strip() else raw.session_id if hasattr(raw,'session_id') else raw['session_id']


def _alias(value):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value):raise ValueError('SQL alias must be an identifier')
    return value


def _exists(db,table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None


def source_predicate(session_ids,alias='e'):
    ids=normalize_session_ids(session_ids)
    if ids is None:return '1',[]
    if not ids:return '0',[]
    source=_SOURCE_SESSION.replace('e.',_alias(alias)+'.')
    return source+' IN (SELECT value FROM json_each(?))',[json.dumps(ids)]


def maintenance_predicate(db,session_ids,alias='w'):
    ids=normalize_session_ids(session_ids)
    if ids is None:return '1',[]
    if not ids or not _exists(db,'memory_thought_cause_sessions'):return '0',[]
    alias=_alias(alias)
    return ('EXISTS(SELECT 1 FROM memory_thought_work_causes pc JOIN memory_thought_cause_sessions ps '
        'ON ps.thought_id=pc.thought_id AND ps.cause=pc.cause WHERE pc.thought_id='+alias+'.thought_id '
        'AND pc.generation>'+alias+'.completed_generation AND ps.session_id IN (SELECT value FROM json_each(?)))'),[json.dumps(ids)]


def pending_cohort(db,task,allowed):
    if not _exists(db,'memory_thought_cause_sessions'):return None
    ids=normalize_session_ids(allowed)
    clause=' AND ps.session_id IN (SELECT value FROM json_each(?))' if ids is not None else ''
    args=[task['thought_id'],task['completed_generation'],task['generation']]
    if ids is not None:args.append(json.dumps(ids))
    rows=db.execute('SELECT DISTINCT ps.session_id FROM memory_thought_work_causes pc '
        'JOIN memory_thought_cause_sessions ps ON ps.thought_id=pc.thought_id AND ps.cause=pc.cause '
        'WHERE pc.thought_id=? AND pc.generation>? AND pc.generation<=?'+clause+' ORDER BY ps.session_id',args)
    values=tuple(row[0] for row in rows)
    return values if values else None


def processing_deadline(db,session_ids,*,now,with_model=True,lane='all'):
    """Read-only scheduler probe; never build engines, migrate schema, or load bodies."""
    if lane not in ('all','source','maintenance'):raise ValueError('unknown processing lane')
    ids=normalize_session_ids(session_ids)
    if ids==() or not with_model:return None
    deadlines=[]
    if lane!='maintenance' and _exists(db,'memory_raw_events') and _exists(db,'memory_capture_jobs'):
        where,args=source_predicate(ids)
        deadlines.append(db.execute("SELECT min(CASE j.status WHEN 'running' THEN j.lease_until WHEN 'waiting_model' THEN ? ELSE j.available_at END) "
            'FROM memory_capture_jobs j JOIN memory_raw_events e ON e.id=j.event_id '
            "WHERE j.status IN ('pending','running','waiting_model') AND "+where,[now,*args]).fetchone()[0])
    if lane!='source' and _exists(db,'memory_thought_work'):
        where,args=maintenance_predicate(db,ids)
        deadlines.append(db.execute("SELECT min(CASE w.status WHEN 'running' THEN w.lease_until ELSE w.available_at END) "
            "FROM memory_thought_work w WHERE w.status IN ('pending','running') AND "+where,args).fetchone()[0])
    return min(value for value in deadlines if value is not None) if any(value is not None for value in deadlines) else None
