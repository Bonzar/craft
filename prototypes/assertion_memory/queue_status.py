"""Read-only hosted scheduler projection; call inside a short read transaction."""

from datetime import datetime, timezone

from . import thought_processing_selection
from .processing_control import read_control
from .queue_owner_actions import priority as owner_priority
from .thought_processing_selection import (
    maintenance_predicate,
    normalize_session_ids,
    source_predicate,
)


def _has(db, *tables):
    return all(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None for table in tables)


def _epoch(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='microseconds')


def _page(db, select, args, *, queue_id, status, ordering, limit, offset, exists, epoch=False):
    result = dict(id=queue_id, status=status, ordering=ordering, total=0, next_offset=None, items=[])
    if not exists:
        return result
    # Only immediately claimable work receives a position. Delayed and paused rows
    # have no promised position: additional events/leases can change the next claim.
    query = "WITH snapshot AS (" + select + ") "
    result['total'] = db.execute(query + 'SELECT count(*) FROM snapshot', args).fetchone()[0]
    order = (" ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'ready' THEN 1 "
             "WHEN 'delayed' THEN 2 WHEN 'waiting_origin' THEN 3 WHEN 'paused' THEN 4 ELSE 5 END,"
             " priority DESC,available,id")
    rows = db.execute(query +
        "SELECT id,status,phase,available,"
        "CASE WHEN status='ready' THEN row_number() OVER (PARTITION BY status ORDER BY priority DESC,available,id) END "
        "FROM snapshot" + order + ' LIMIT ? OFFSET ?', (*args, limit, offset))
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
                       auto_process_fresh_mac=False):
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
    if queue_id not in (None, 'source', 'maintenance', 'interactive'):
        raise ValueError('unknown queue')
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
    page = dict(limit=limit, offset=offset)
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
        priority=owner_priority(connection,'source','j.id','j.updated_at','j.status')
        queues.append(_page(connection, f"""
            SELECT j.id, CASE
                WHEN j.status='waiting_origin' THEN 'waiting_origin'
                WHEN j.status='running' AND (j.lease_until>? OR j.lease_until IS NULL) THEN 'running'
                WHEN NOT (? AND ({selected})) THEN 'paused'
                WHEN j.status='running' AND ? AND j.attempts>=? THEN 'expired'
                WHEN j.status IN ('running','waiting_model') OR j.available_at<=? THEN 'ready'
                ELSE 'delayed' END AS status,
                CASE WHEN j.phase IN ('extract','audit','link','refresh','done') THEN j.phase
                     ELSE 'source' END AS phase,
                j.available_at AS available,{priority} AS priority
            FROM memory_capture_jobs j JOIN memory_raw_events e ON e.id=j.event_id
            WHERE j.status IN ('pending','running','waiting_model','waiting_origin')
        """, (stamp, source_mode == 'active', *args, source_expires_exhausted, max_attempts, stamp),
            queue_id='source', status=source_mode, ordering=('available_at_then_id' if priority=='CAST(0 AS INTEGER)' else 'owner_priority_then_available_at_then_id'),
            exists=exists, **page))
    if queue_id in (None, 'maintenance'):
        selected, args = maintenance_predicate(connection, ids, 'w')
        priority=owner_priority(connection,'maintenance','w.thought_id','w.desired_generation','w.status')
        queues.append(_page(connection, f"""
            SELECT w.thought_id AS id, CASE
                WHEN w.status='running' AND (w.lease_until>? OR w.lease_until IS NULL) THEN 'running'
                WHEN NOT (? AND ({selected})) THEN 'paused'
                WHEN w.status='running' AND w.attempts>=? THEN 'expired'
                WHEN w.status='running' OR w.available_at<=? THEN 'ready'
                ELSE 'delayed' END AS status,
                CASE WHEN w.action IN ('refresh','consolidate') THEN w.action
                     ELSE 'maintenance' END AS phase,
                w.available_at AS available,{priority} AS priority
            FROM memory_thought_work w WHERE w.status IN ('pending','running')
        """, (stamp, allowed, *args, max_attempts, stamp),
            queue_id='maintenance', status=processing, ordering=('available_at_then_id' if priority=='CAST(0 AS INTEGER)' else 'owner_priority_then_available_at_then_id'),
            exists=_has(connection, 'memory_thought_work'), **page))
    if queue_id in (None, 'interactive'):
        exists = _has(connection, 'memory_interactive_queries')
        queues.append(_page(connection, """
            SELECT id, CASE WHEN state='running' AND ? THEN 'running'
                WHEN ? THEN 'ready' ELSE 'paused' END AS status,
                'query' AS phase, created AS available,0 AS priority
            FROM memory_interactive_queries WHERE state IN ('queued','running')
        """, (interactive_enabled, interactive_enabled), queue_id='interactive',
            status=('active' if exists else 'unavailable') if interactive_enabled else 'disabled',
            ordering='created_then_id', exists=exists, epoch=True, **page))
    return dict(release_version=release_version, stage=stage, global_order=False,
                snapshot_at=stamp, queues=queues,
                processing_control={'paused':globally_paused,'revision':revision,'can_change':False})
