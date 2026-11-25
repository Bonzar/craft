"""Read-only hosted scheduler projection; call inside a short read transaction."""

from datetime import datetime, timezone

from . import thought_processing_selection
from .processing_control import read_control
from .session_api import _SOURCE_SESSION
from .thought_processing_selection import (
    maintenance_predicate,
    normalize_session_ids,
    source_predicate,
)


QUEUE_FILTER_KEYS = frozenset(('queue','phase','status','session_id','q'))
QUEUE_FILTER_ENUMS = {
    'queue': frozenset(('source','maintenance','interactive')),
    'phase': frozenset(('extract','audit','link','refresh','done','source','partial_source',
                       'consolidate','maintenance','query')),
    'status': frozenset(('ready','running','delayed','paused','waiting_origin','expired')),
}


def validate_queue_filters(**values):
    """Validate public filters without including their values in errors."""
    for key, value in values.items():
        allowed = QUEUE_FILTER_ENUMS.get(key)
        if value is None:
            continue
        bound = 256 if key == 'session_id' else 128
        if (key not in QUEUE_FILTER_KEYS or not isinstance(value,str)
            or (value not in allowed if allowed else
                not 1 <= len(value) <= bound or not value.strip() or not value.isprintable())):
            raise ValueError('invalid queue filter')
    return sorted(key for key,value in values.items() if value is not None)


def _has(db, *tables):
    return all(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None for table in tables)


def _epoch(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='microseconds')


def _page(db, select, args, *, queue_id, status, ordering, limit, offset, exists, filters, epoch=False):
    result = dict(id=queue_id, status=status, ordering=ordering, total=0, unfiltered_total=0, next_offset=None, items=[])
    if not exists:
        return result
    # Only immediately claimable work receives a position. Delayed and paused rows
    # have no promised position: additional events/leases can change the next claim.
    query = "WITH snapshot AS (" + select + ") "
    where, params = [], []
    for key in ('phase','status','q','session_id'):
        if filters[key] is None:
            continue
        if key == 'session_id':
            where.append('session_match')
            continue
        where.append('instr(lower(id),lower(?))>0' if key == 'q' else key+'=?')
        params.append(filters[key])
    predicate = ' AND '.join(where) if where else '1'
    result['unfiltered_total'],result['total'] = db.execute(
        query + 'SELECT count(*),coalesce(sum(CASE WHEN '+predicate+' THEN 1 ELSE 0 END),0) FROM snapshot',
        (*args,*params)).fetchone()
    if offset >= result['total']:
        return result
    order = (" ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'ready' THEN 1 "
             "WHEN 'delayed' THEN 2 WHEN 'waiting_origin' THEN 3 WHEN 'paused' THEN 4 ELSE 5 END,"
             " available,id")
    # Rank the complete queue before filtering to preserve the worker's actual order.
    rows = db.execute(query +
        ", ranked AS (SELECT *, CASE WHEN status='ready' THEN "
        "row_number() OVER (PARTITION BY status ORDER BY available,id) END AS position FROM snapshot) "
        "SELECT id,status,phase,available,position FROM ranked WHERE " + predicate + order +
        ' LIMIT ? OFFSET ?', (*args,*params,limit,offset))
    result['items'] = [
        dict(id=id_, status=state, phase=phase, position=position,
             available_at=_epoch(available) if epoch else available)
        for id_, state, phase, available, position in rows
    ]
    if offset + limit < result['total']:
        result['next_offset'] = offset + limit
    return result


def build_queue_status(connection, *, release_version, stage, processing_session_ids,
                       model_configured, interactive_enabled, now=None, limit=50, offset=0,
                       max_attempts=3, source_expires_exhausted=True, queue_id=None,
                       auto_process_fresh_mac=False, phase=None, status=None, session_id=None, q=None):
    """Return independent queues and their current claim order, never jobs' contents.

    Callers supply trusted deployment metadata and the hosted scheduler configuration.
    'running' describes the recorded lease, not proof of liveness or completion order.
    """
    ids = normalize_session_ids(processing_session_ids)
    if stage not in ('test', 'prod') or not isinstance(release_version, str) or not release_version:
        raise ValueError('trusted stage and release are required')
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError('invalid queue page')
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError('invalid retry policy')
    filters = dict(phase=phase,status=status,session_id=session_id,q=q)
    applied_filters = validate_queue_filters(queue=queue_id, **filters)
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('timezone-aware snapshot timestamp required')
    stamp = now.astimezone(timezone.utc).isoformat(timespec='microseconds')
    if type(auto_process_fresh_mac) is not bool or (auto_process_fresh_mac and ids != ()):
        raise ValueError('fresh Mac auto-processing requires an empty ordinary selector')
    fresh_selector = getattr(thought_processing_selection, 'FRESH_MAC_ONLY', None)
    if auto_process_fresh_mac and fresh_selector is None:
        raise ValueError('fresh Mac auto-processing is not in this release')
    globally_paused, revision = read_control(connection)
    processing = 'paused' if globally_paused else ('unavailable' if not model_configured else
                                                    'paused' if ids == () else 'active')
    allowed = processing == 'active'
    source_mode = ('active' if model_configured else 'unavailable') if auto_process_fresh_mac and not globally_paused else processing
    page = dict(limit=limit, offset=offset, filters=filters)
    queues = []
    if queue_id in (None, 'source'):
        # Fresh-only mode replaces, rather than supplements, the ordinary source
        # selector. Its registration table is absent until the first opted-in stream.
        if auto_process_fresh_mac:
            selected, args = (source_predicate(fresh_selector, 'e')
                if _has(connection, 'memory_fresh_mac_events') else ('0', []))
        else:
            selected, args = source_predicate(ids, 'e')
        exists = _has(connection, 'memory_capture_jobs', 'memory_raw_events')
        session_check = '1' if session_id is None else _SOURCE_SESSION + '=?'
        session_args = [] if session_id is None else [session_id]
        queues.append(_page(connection, f"""
            SELECT j.id, CASE
                WHEN j.status='waiting_origin' THEN 'waiting_origin'
                WHEN j.status='running' AND (j.lease_until>? OR j.lease_until IS NULL) THEN 'running'
                WHEN NOT (? AND ({selected})) THEN 'paused'
                WHEN j.status='running' AND ? AND j.attempts>=? THEN 'expired'
                WHEN j.status IN ('running','waiting_model') OR j.available_at<=? THEN 'ready'
                ELSE 'delayed' END AS status,
                CASE WHEN j.phase IN ('extract','audit','link','refresh','done','partial_source') THEN j.phase
                     ELSE 'source' END AS phase,
                j.available_at AS available, ({session_check}) AS session_match
            FROM memory_capture_jobs j JOIN memory_raw_events e ON e.id=j.event_id
            WHERE j.status IN ('pending','running','waiting_model','waiting_origin')
        """, (stamp, source_mode == 'active', *args, source_expires_exhausted, max_attempts, stamp, *session_args),
            queue_id='source', status=source_mode, ordering='available_at_then_id',
            exists=exists, **page))
    if queue_id in (None, 'maintenance'):
        selected, args = maintenance_predicate(connection, ids, 'w')
        session_check, session_args = ('1', []) if session_id is None else maintenance_predicate(
            connection, (session_id,), 'w')
        queues.append(_page(connection, f"""
            SELECT w.thought_id AS id, CASE
                WHEN w.status='running' AND (w.lease_until>? OR w.lease_until IS NULL) THEN 'running'
                WHEN NOT (? AND ({selected})) THEN 'paused'
                WHEN w.status='running' AND w.attempts>=? THEN 'expired'
                WHEN w.status='running' OR w.available_at<=? THEN 'ready'
                ELSE 'delayed' END AS status,
                CASE WHEN w.action IN ('refresh','consolidate') THEN w.action
                     ELSE 'maintenance' END AS phase,
                w.available_at AS available, ({session_check}) AS session_match
            FROM memory_thought_work w WHERE w.status IN ('pending','running')
        """, (stamp, allowed, *args, max_attempts, stamp, *session_args),
            queue_id='maintenance', status=processing, ordering='available_at_then_id',
            exists=_has(connection, 'memory_thought_work'), **page))
    if queue_id in (None, 'interactive'):
        exists = _has(connection, 'memory_interactive_queries')
        queues.append(_page(connection, """
            SELECT id, CASE WHEN state='running' AND ? THEN 'running'
                WHEN ? THEN 'ready' ELSE 'paused' END AS status,
                'query' AS phase, created AS available, 0 AS session_match
            FROM memory_interactive_queries WHERE state IN ('queued','running')
        """, (interactive_enabled, interactive_enabled), queue_id='interactive',
            status=('active' if exists else 'unavailable') if interactive_enabled else 'disabled',
            ordering='created_then_id', exists=exists, epoch=True, **page))
    return dict(release_version=release_version, stage=stage, global_order=False,
                snapshot_at=stamp, queues=queues, queue_filters=True, applied_filters=applied_filters,
                processing_control={'paused':globally_paused,'revision':revision,'can_change':False})
