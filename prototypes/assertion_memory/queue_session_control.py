"""Explicit owner session selection; never interprets null as all."""
import json
import re
from uuid import uuid4

from .processing_control import Conflict, INITIAL, REVISION
from .session_api import _SOURCE_SESSION

PATH='/api/v1/queue-sessions'
MAX_SELECTED=50
MAX_CHOICES=100
MAX_ID=256
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}\Z')


def _exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def normalize(values):
    if (type(values) not in (list,tuple) or len(values)>MAX_SELECTED
            or any(type(v) is not str or not ID.fullmatch(v) for v in values)
            or len(set(values))!=len(values)):
        raise ValueError('Expected explicit bounded session selection')
    return tuple(sorted(values))


def read(db, baseline=()):
    if not _exists(db,'memory_test_queue_sessions'):
        return normalize(baseline), INITIAL
    row=db.execute('SELECT selected,revision FROM memory_test_queue_sessions WHERE singleton=1').fetchone()
    if row is None or not REVISION.fullmatch(row[1]):
        raise ValueError('Invalid session selection')
    return normalize(json.loads(row[0])),row[1]


def _batch_member(db):
    if not all(_exists(db,t) for t in ('memory_capture_batches','memory_capture_batch_members')):
        return '0'
    return ('EXISTS (SELECT 1 FROM memory_capture_batch_members bm '
            'JOIN memory_capture_batches b ON b.batch_id=bm.batch_id '
            f'WHERE bm.event_id=e.id AND b.session_id={_SOURCE_SESSION})')


def choices(db, selected, *, fresh_only=False):
    if not all(_exists(db,t) for t in ('memory_raw_events','memory_capture_jobs','memory_event_positions')):
        return []
    found={}
    fresh = 'e.id IN (SELECT event_id FROM memory_fresh_mac_events)' if _exists(db,'memory_fresh_mac_events') else '0'
    batch = _batch_member(db)
    ordinary = f'p.position IS NOT NULL AND ({fresh})' if fresh_only else 'p.position IS NOT NULL'
    scope = f' AND (({ordinary}) OR ({batch}))'
    for sid,count in db.execute(
            f'SELECT {_SOURCE_SESSION} AS sid, sum(CASE WHEN j.status IN (\'pending\',\'running\',\'waiting_model\') THEN 1 ELSE 0 END) '
            'FROM memory_raw_events e JOIN memory_capture_jobs j ON j.event_id=e.id '
            'LEFT JOIN memory_event_positions p ON p.event_id=e.id WHERE j.status<>\'waiting_origin\' '
             + scope + ' GROUP BY sid ORDER BY 2 DESC,sid LIMIT ?', (MAX_CHOICES,)):
        if isinstance(sid,str) and ID.fullmatch(sid):
            found[sid]={'id':sid,'queued':int(count),'eligible':True}
    # Already selected IDs remain visible even if the matching data aged out.
    for sid in selected:
        found.setdefault(sid,{'id':sid,'queued':0,'eligible':False})
    return list(found.values())


def status(db, baseline=(), *, stage='test', fresh_only=False):
    selected,revision=read(db,baseline)
    if stage not in ('test','prod'):
        raise ValueError('Invalid queue stage')
    # Before an explicit override, existing fresh-only admission stays active.
    if fresh_only and revision==INITIAL:
        selected=tuple(x['id'] for x in choices(db,(),fresh_only=True)[:MAX_SELECTED])
    return dict(stage=stage,revision=revision,selected_session_ids=list(selected),
                sessions=choices(db,selected,fresh_only=fresh_only))


def update(db, *, selected_session_ids, expected_revision, baseline=(), stage='test', fresh_only=False):
    if not db.in_transaction or type(expected_revision) is not str or not REVISION.fullmatch(expected_revision):
        raise ValueError('Invalid session selection revision')
    wanted=normalize(selected_session_ids)
    current,rev=read(db,baseline)
    if rev!=expected_revision:
        raise Conflict('Session selection changed')
    known={x['id'] for x in choices(db,current,fresh_only=fresh_only) if x['eligible']}
    if not set(wanted)<=known|set(current):
        raise ValueError('Unknown session selection')
    if wanted!=current or fresh_only and rev==INITIAL:
        db.execute('CREATE TABLE IF NOT EXISTS memory_test_queue_sessions('
                   'singleton INTEGER PRIMARY KEY CHECK(singleton=1),selected TEXT NOT NULL,revision TEXT NOT NULL)')
        db.execute('INSERT INTO memory_test_queue_sessions VALUES (1,?,?) '
                   'ON CONFLICT(singleton) DO UPDATE SET selected=excluded.selected,revision=excluded.revision',
                   (json.dumps(wanted,separators=(',',':')),uuid4().hex))
    return status(db,baseline,stage=stage,fresh_only=fresh_only)


def hold_reason(*, live, status, position, selected, session_id, paused):
    if not live:
        return None
    if status=='waiting_origin' or position is None:
        return 'incomplete_source'
    if status=='running':
        return None
    if paused:
        return 'global_pause'
    if selected is not None and session_id not in selected:
        return 'session_excluded'
    return None


def read_file(path, baseline=()):
    from contextlib import closing
    from pathlib import Path
    import sqlite3
    with closing(sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,
                                 isolation_level=None,timeout=.2)) as db:
        db.execute('PRAGMA query_only=ON')
        return read(db,baseline)


def worker_selection_changed(config):
    revision=config.queue_sessions_revision
    if revision is None:
        return False
    try:
        return read_file(config.db)!=(config.processing_session_ids,revision)
    except (OSError, ValueError):
        return True
    except Exception:
        # A locked/corrupt policy DB stops new admission rather than widening it.
        return True


def follow_settings(config, launch, *, fresh_only=False):
    """Run alongside the existing TEST supervisor only when owner route is active."""
    from contextlib import asynccontextmanager
    from dataclasses import replace
    import asyncio
    import sqlite3
    import time

    @asynccontextmanager
    async def running():
        failed_until=0
        async def watch():
            nonlocal failed_until
            while True:
                try:
                    selected,rev=await asyncio.to_thread(read_file,config.db,config.processing_session_ids)
                    if selected and rev!=INITIAL and time.monotonic()>=failed_until:
                        # Existing WORKER flock prevents two workers; poll again
                        # after the previous revision drains and lets go.
                        next_=(config if fresh_only else replace(config,processing_session_ids=selected,queue_sessions_revision=rev))
                        started=await asyncio.to_thread(launch,next_)
                        if started.get('scheduled'):
                            failed_until=time.monotonic()+5
                except (OSError, ValueError, sqlite3.Error):
                    pass
                await asyncio.sleep(.5)
        work=asyncio.create_task(watch())
        try:
            yield
        finally:
            work.cancel()
            await asyncio.gather(work,return_exceptions=True)
    return running()


def annotate_source(db, data, selected, *, fresh_only=False):
    source=next((q for q in data['queues'] if q['id']=='source'),None)
    if not source or not source['items']:
        return
    position=("p.position" if _exists(db,'memory_event_positions') else 'NULL')
    join=("LEFT JOIN memory_event_positions p ON p.event_id=e.id"
          if position!='NULL' else '')
    items={x['id']:x for x in source['items']}
    from .thought_processing_selection import FRESH_MAC_ONLY, source_predicate
    gate,gate_args=(source_predicate(FRESH_MAC_ONLY,'e',db=db)
                    if fresh_only and _exists(db,'memory_fresh_mac_events') else ('0' if fresh_only else '1',[]))
    member=_batch_member(db)
    if fresh_only:
        batch_gate=member
        explicit,revision=read(db)
        if revision!=INITIAL:
            allowed,allowed_args=source_predicate(explicit,'e')
            batch_gate=f'({batch_gate}) AND ({allowed})'
            gate_args += allowed_args
        gate=f'({gate}) OR ({batch_gate})'
    rows=db.execute(f'SELECT j.id,j.status,CASE WHEN ({member}) THEN 0 ELSE {position} END,{_SOURCE_SESSION},({gate}) '
                    'FROM memory_capture_jobs j JOIN memory_raw_events e ON e.id=j.event_id '
                    f'{join} WHERE j.id IN (SELECT value FROM json_each(?))',
                    (*gate_args,json.dumps(list(items)))).fetchall()
    for key,state,pos,sid,in_scope in rows:
        item=items[key]
        item['session_id']=sid if isinstance(sid,str) and ID.fullmatch(sid) else None
        item['hold_reason']=hold_reason(live=True,status=item['status'] if item['status']=='running' else state,
            position=pos,selected=(None if in_scope else ()) if fresh_only else selected,session_id=sid,paused=data['processing_control']['paused'])


def annotate_maintenance(db,data,selected):
    queue=next((q for q in data['queues'] if q['id']=='maintenance'),None)
    if not queue or not queue['items']:
        return
    scope={}
    if all(_exists(db,t) for t in ('memory_thought_work','memory_thought_work_causes','memory_thought_cause_sessions')):
        page=json.dumps([item['id'] for item in queue['items']])
        for thought,session in db.execute(
            'SELECT DISTINCT w.thought_id,ps.session_id FROM memory_thought_work w '
            'JOIN memory_thought_work_causes pc ON pc.thought_id=w.thought_id AND pc.generation>w.completed_generation '
            'JOIN memory_thought_cause_sessions ps ON ps.thought_id=pc.thought_id AND ps.cause=pc.cause '
            'WHERE w.thought_id IN (SELECT value FROM json_each(?))', (page,)):
            scope.setdefault(thought,set()).add(session)
    for item in queue['items']:
        candidates=scope.get(item['id'],set())
        item['hold_reason'] = (None if item['status']!='paused' else
            'global_pause' if data['processing_control']['paused'] else
            'session_excluded' if selected is not None and not candidates.intersection(selected) else None)
