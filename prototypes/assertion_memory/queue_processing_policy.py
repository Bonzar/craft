"""Revisioned owner policy. The default is all new normal work; legacy policy is not widened."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from uuid import uuid4

from .processing_control import Conflict, INITIAL, REVISION
from .queue_session_control import normalize, read as read_legacy

PATH='/api/v1/queue-processing-policy'


@dataclass(frozen=True)
class Policy:
    mode: str = 'all'
    included_session_ids: tuple = ()
    excluded_session_ids: tuple = ()
    added_from: str | None = None
    added_through: str | None = None


def _bound(value):
    if value is None:
        return value
    if not isinstance(value,str) or len(value)>40 or 'T' not in value:
        raise ValueError('Expected an ISO timestamp with timezone')
    bound=datetime.fromisoformat(value)
    if bound.tzinfo is None:
        raise ValueError('Expected an explicit timezone')
    return bound.astimezone(timezone.utc).isoformat(timespec='microseconds')


def validate(document):
    if type(document) is not dict or set(document)!=set(Policy.__dataclass_fields__):
        raise ValueError('Invalid processing policy fields')
    mode=document['mode']
    include=normalize(document['included_session_ids'])
    exclude=normalize(document['excluded_session_ids'])
    lower,upper=(_bound(document[k]) for k in ('added_from','added_through'))
    if (mode not in ('all','include') or mode=='all' and include or set(include)&set(exclude)
            or lower is not None and upper is not None and lower>upper):
        raise ValueError('Invalid processing policy combination')
    return Policy(mode,include,exclude,lower,upper)


def read(db):
    """None is a legacy configuration, not implicit authorization to reopen historical work."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_queue_processing_policy'").fetchone():
        row=db.execute('SELECT document,revision FROM memory_queue_processing_policy WHERE singleton=1').fetchone()
        if row is None or row[1]==INITIAL or not REVISION.fullmatch(row[1]):
            raise ValueError('Invalid processing policy revision')
        return validate(json.loads(row[0])),row[1]
    selected,rev=read_legacy(db)
    return (Policy('include',selected) if rev!=INITIAL else None),rev


def has_saved(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_queue_processing_policy'").fetchone() is not None


def describe(db, *, stage, baseline=None, fresh_only=False):
    if stage not in ('test','prod'):
        raise ValueError('Invalid queue stage')
    policy,revision=read(db)
    if policy is None:
        policy=Policy('include',tuple(baseline)) if baseline is not None and not fresh_only else Policy()
    from .queue_session_control import choices
    selected=tuple(dict.fromkeys((*policy.included_session_ids,*policy.excluded_session_ids)))
    return {'stage':stage,'revision':revision,'policy':asdict(policy),
            'sessions':choices(db,selected,fresh_only=fresh_only)}


def update(db, *, stage, expected_revision, policy, baseline=None, fresh_only=False):
    if (not db.in_transaction or type(expected_revision) is not str
            or not REVISION.fullmatch(expected_revision)):
        raise ValueError('Invalid processing policy revision')
    wanted=validate(policy)
    if read(db)[1]!=expected_revision:
        raise Conflict('Processing policy changed')
    db.execute('CREATE TABLE IF NOT EXISTS memory_queue_processing_policy('
               'singleton INTEGER PRIMARY KEY CHECK(singleton=1),document TEXT NOT NULL,revision TEXT NOT NULL)')
    db.execute('INSERT INTO memory_queue_processing_policy VALUES (1,?,?) '
               'ON CONFLICT(singleton) DO UPDATE SET document=excluded.document,revision=excluded.revision',
               (json.dumps(asdict(wanted),separators=(',',':')),uuid4().hex))
    return describe(db,stage=stage,baseline=baseline,fresh_only=fresh_only)


def predicate(policy, *, session, added):
    """Expressions come only from source code; every owner value is a parameter."""
    if policy is None:
        return '1',[]
    clauses,args=[],[]
    if policy.mode=='include':
        clauses.append(f'{session} IN (SELECT value FROM json_each(?))')
        args.append(json.dumps(policy.included_session_ids))
    if policy.excluded_session_ids:
        clauses.append(f'({session} IS NULL OR {session} NOT IN (SELECT value FROM json_each(?)))')
        args.append(json.dumps(policy.excluded_session_ids))
    for operator,bound in (('>=',policy.added_from),('<=',policy.added_through)):
        if bound is not None:
            # Use canonical UTC textual microseconds (SQLite datetime rounds to seconds).
            clauses.append(f'{added}{operator}?')
            args.append(bound)
    return ' AND '.join(clauses) or '1',args


def explicit(db):
    return read(db)[0] if has_saved(db) else None


def source_predicate(db, *, event_alias, job_alias):
    from .session_api import _SOURCE_SESSION
    active=explicit(db)
    column='NULL'
    if active is not None and (active.added_from or active.added_through):
        if any(row[1]=='created_at' for row in db.execute('PRAGMA table_info(memory_capture_jobs)')):
            column=job_alias+'.created_at'
    return predicate(active,session=_SOURCE_SESSION.replace('e.',event_alias+'.'),added=column)


def allows_any_maintenance_scope(policy):
    return policy.mode=='all' and not policy.excluded_session_ids and policy.added_from is None and policy.added_through is None


def maintenance_predicate(db, policy, *, alias, admitted_only=False):
    """A coalesced generation is indivisible; a disallowed cause must not be marked complete."""
    if not admitted_only and allows_any_maintenance_scope(policy):
        return '1',[]
    columns={row[1] for row in db.execute('PRAGMA table_info(memory_thought_work_causes)')}
    if not columns:
        return '0',[]
    has_dates=policy.added_from is not None or policy.added_through is not None
    if has_dates and 'added_at' not in columns:
        return '0',[]
    causes=(f'pc.thought_id={alias}.thought_id AND pc.generation>{alias}.completed_generation '
            f'AND pc.generation<={alias}.desired_generation')
    date,date_args=predicate(Policy(added_from=policy.added_from,added_through=policy.added_through),
                             session='NULL',added='pc.added_at')
    disallowed=[f'NOT coalesce(({date}),0)'] if has_dates else []
    args=list(date_args)
    if admitted_only or policy.mode=='include' or policy.excluded_session_ids:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_thought_cause_sessions'").fetchone():
            return '0',[]
        linked='ps.thought_id=pc.thought_id AND ps.cause=pc.cause'
        # Unknown session attribution is not evidence that the cause may pass a scope restriction.
        disallowed.append(f'NOT EXISTS (SELECT 1 FROM memory_thought_cause_sessions ps WHERE {linked})')
        session,session_args=predicate(Policy(policy.mode,policy.included_session_ids,policy.excluded_session_ids),
                                       session='ps.session_id',added='NULL')
        if admitted_only:
            from .queue_session_control import _exists
            allowed=[]
            if _exists(db,'memory_fresh_mac_streams'):
                allowed.append('EXISTS (SELECT 1 FROM memory_fresh_mac_streams fs WHERE fs.session_id=ps.session_id)')
            if _exists(db,'memory_capture_batches'):
                allowed.append('EXISTS (SELECT 1 FROM memory_capture_batches cb WHERE cb.session_id=ps.session_id)')
            session=f"({session}) AND ({' OR '.join(allowed) if allowed else '0'})"
        disallowed.append(f'EXISTS (SELECT 1 FROM memory_thought_cause_sessions ps WHERE {linked} AND NOT ({session}))')
        args+=session_args
    return (f'EXISTS (SELECT 1 FROM memory_thought_work_causes pc WHERE {causes}) '
            f'AND NOT EXISTS (SELECT 1 FROM memory_thought_work_causes pc WHERE {causes} AND ('+' OR '.join(disallowed)+'))',args)
