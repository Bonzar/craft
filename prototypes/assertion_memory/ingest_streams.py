"""Operational producer delivery frontiers, separate from source/graph meaning.

Mutations require the caller's existing SQLite write transaction and never commit.
Capture must insert its raw event and call record_delivery in that SAME transaction.
Reports are authenticated producer claims, not semantic evidence or world truth.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

LEASE_SECONDS = 60
MAX_SEQUENCE = 2**53 - 1
SCAN_STATES = frozenset(('current', 'catching_up', 'gapped', 'unknown'))


def _time(now):
    value = now or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('server clock must be an offset-aware datetime')
    return value.astimezone(timezone.utc)


def _stamp(value):
    return value.isoformat(timespec='microseconds')


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError(field + ' must be a bounded nonempty identifier')
    return value


def _sequence(value, field, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_SEQUENCE:
        raise ValueError(field + ' must be a bounded nonnegative integer')
    return value


def _writing(db):
    if not db.in_transaction:
        raise ValueError('ingest mutations require the caller write transaction')


def schema(db):
    _writing(db)
    db.execute('''CREATE TABLE IF NOT EXISTS memory_ingest_streams (
        stream_id TEXT PRIMARY KEY, producer_id TEXT NOT NULL, session_id TEXT NOT NULL,
        epoch TEXT NOT NULL, report_seq INTEGER NOT NULL DEFAULT 0,
        observed_seq INTEGER NOT NULL DEFAULT 0, acked_through INTEGER NOT NULL DEFAULT 0,
        scan_state TEXT NOT NULL DEFAULT 'unknown', gap_count INTEGER NOT NULL DEFAULT 0,
        lifecycle TEXT NOT NULL DEFAULT 'active', last_seen_at TEXT NOT NULL,
        lease_until TEXT NOT NULL, revision INTEGER NOT NULL, report_json TEXT,
        UNIQUE(producer_id,session_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS memory_ingest_receipts (
        stream_id TEXT NOT NULL REFERENCES memory_ingest_streams(stream_id),
        delivery_seq INTEGER NOT NULL, event_id TEXT NOT NULL REFERENCES memory_raw_events(id),
        received_at TEXT NOT NULL, PRIMARY KEY(stream_id,delivery_seq), UNIQUE(stream_id,event_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS memory_ingest_retired_receipts (
        stream_id TEXT NOT NULL REFERENCES memory_ingest_streams(stream_id),
        delivery_seq INTEGER NOT NULL,event_id TEXT NOT NULL,received_at TEXT NOT NULL,
        policy TEXT NOT NULL,proof TEXT NOT NULL,PRIMARY KEY(stream_id,delivery_seq),UNIQUE(stream_id,event_id))''')


def _receipts(db):
    live = 'SELECT stream_id,delivery_seq,event_id FROM memory_ingest_receipts'
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_ingest_retired_receipts'").fetchone():
        return live + ' UNION SELECT stream_id,delivery_seq,event_id FROM memory_ingest_retired_receipts'
    return live


def retire_delivery_receipts(db,event_id,policy):
    """Move exact server receipts, never their source body, before raw deletion."""
    schema(db)
    _identifier(event_id,'event_id'); _identifier(policy,'retirement policy')
    rows = db.execute('SELECT stream_id,delivery_seq,event_id,received_at FROM memory_ingest_receipts WHERE event_id=?',(event_id,)).fetchall()
    for row in rows:
        stream,seq,event,received = tuple(row)
        old = db.execute('SELECT event_id FROM memory_ingest_retired_receipts WHERE stream_id=? AND delivery_seq=?',(stream,seq)).fetchone()
        if old and old[0]!=event:
            raise ValueError('retired delivery identity conflicts with original receipt')
        proof = json.dumps({'kind':'original_server_receipt','stream_id':stream,'delivery_seq':seq,'event_id':event,'received_at':received},sort_keys=True)
        db.execute('INSERT OR IGNORE INTO memory_ingest_retired_receipts VALUES(?,?,?,?,?,?)',(stream,seq,event,received,policy,proof))
    db.execute('DELETE FROM memory_ingest_receipts WHERE event_id=?',(event_id,))
    return len(rows)


def _restore_retired(db,proof):
    from .thought_source_policy import event_eligibility,POLICY
    if not isinstance(proof,dict) or proof.get('status')!='acknowledged':
        raise ValueError('repair requires an actual acknowledged outbox receipt')
    receipt = proof.get('receipt',{})
    event,delivery = receipt.get('event',{}),receipt.get('delivery',{})
    if set(event)-{'id','session_id','message_id','source','actor','metadata'}:
        raise ValueError('repair event must contain metadata only')
    event_id = _identifier(event.get('id'),'event_id')
    session = _identifier(proof.get('session_id'),'session_id')
    message = _identifier(proof.get('message_id'),'message_id')
    seq = _sequence(proof.get('delivery_seq'),'delivery_seq',1)
    if event.get('session_id')!=session or (event.get('message_id') or event.get('metadata',{}).get('message_id'))!=message:
        raise ValueError('outbox identity does not match its original server receipt')
    state = _load(db,_identifier(delivery.get('stream_id'),'stream_id'))
    _identifier(delivery.get('epoch'),'receipt epoch')
    if delivery.get('session_id')!=session or state['session_id']!=session or delivery.get('producer_id')!=state['producer_id']:
        raise ValueError('outbox acknowledgement belongs to another server stream/session')
    observed = _sequence(delivery.get('observed_seq'),'receipt observed_seq')
    acked = _sequence(delivery.get('acked_through'),'receipt acked_through')
    revision = _sequence(delivery.get('revision'),'receipt revision')
    if not seq<=state['acked_through'] or not seq<=observed<=state['observed_seq'] or acked>state['acked_through'] or revision>state['revision']:
        raise ValueError('receipt cannot establish or advance an unproven server delivery frontier')
    rows = db.execute('SELECT event_id,delivery_seq FROM ('+_receipts(db)+') WHERE stream_id=? AND (delivery_seq=? OR event_id=?)',
        (state['stream_id'],seq,event_id)).fetchall()
    if rows:
        if any(row[0]!=event_id or row[1]!=seq for row in rows):
            raise ValueError('outbox acknowledgement conflicts with retained server membership')
        return False
    if db.execute('SELECT 1 FROM memory_raw_events WHERE id=?',(event_id,)).fetchone():
        raise ValueError('raw source still exists; repair does not retire existing sources')
    if event_eligibility(event)['allowed']:
        raise ValueError('missing visible source cannot be repaired as policy-retired material')
    received = _identifier(delivery.get('last_seen_at'),'receipt timestamp')
    if datetime.fromisoformat(received).utcoffset() is None:
        raise ValueError('receipt timestamp must include timezone')
    compact = {'kind':'acknowledged_outbox','event_id':event_id,'session_id':session,'message_id':message,
        'stream_id':state['stream_id'],'delivery_seq':seq,'epoch':delivery['epoch'],
        'receipt_revision':revision,'source':event.get('source'),'actor':event.get('actor'),
        'original_server_receipt_sha256':proof.get('original_server_receipt_sha256')}
    db.execute('INSERT INTO memory_ingest_retired_receipts VALUES(?,?,?,?,?,?)',
        (state['stream_id'],seq,event_id,received,POLICY,json.dumps(compact,sort_keys=True)))
    return True


def restore_retired_deliveries(db,proofs):
    """Metadata-only repair; persisted server ACK/report frontiers never advance."""
    schema(db)
    if not isinstance(proofs,list) or len(proofs)>1000:
        raise ValueError('supply at most 1000 original acknowledged outbox proofs')
    result = {'restored':0,'already_present':0,'rejected':[]}
    for index,proof in enumerate(proofs):
        try:
            changed = _restore_retired(db,proof)
            result['restored' if changed else 'already_present'] += 1
        except (ValueError,TypeError,KeyError,AttributeError) as error:
            result['rejected'].append({'index':index,'reason':str(error)})
    return result


def _revision(db):
    return db.execute('SELECT coalesce(max(revision),0)+1 FROM memory_ingest_streams').fetchone()[0]


def _load(db, stream_id):
    cursor = db.execute('SELECT * FROM memory_ingest_streams WHERE stream_id=?', (stream_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError('unknown ingest stream')
    return dict(zip((column[0] for column in cursor.description), row))


def _owned(db, stream_id, epoch):
    _identifier(stream_id, 'stream_id'); _identifier(epoch, 'epoch')
    state = _load(db, stream_id)
    if state['epoch'] != epoch:
        raise ValueError('stale ingest writer epoch')
    return state


def _public(state):
    return {key: value for key, value in state.items() if key != 'report_json'}


def register(db, producer_id, session_id, *, replace_epoch=None, now=None):
    """Idempotent registration; explicit CAS epoch replacement retains all backlog/gaps."""
    _identifier(producer_id, 'producer_id'); _identifier(session_id, 'session_id')
    schema(db)
    found = db.execute('SELECT stream_id FROM memory_ingest_streams WHERE producer_id=? AND session_id=?',
                       (producer_id, session_id)).fetchone()
    instant = _time(now)
    if found:
        state = _load(db, found[0])
        if replace_epoch is None:
            return _public(state)
        if replace_epoch != state['epoch']:
            raise ValueError('stale ingest writer epoch')
        from .stream_clear_transition import require_ready
        require_ready(db,state)
        # Losing ownership cannot erase pending receipts or previously reported gaps.
        db.execute("UPDATE memory_ingest_streams SET epoch=?,report_seq=0,report_json=NULL,"
            "lifecycle='active',scan_state=CASE WHEN scan_state='current' THEN 'unknown' ELSE scan_state END,"
            'last_seen_at=?,lease_until=?,revision=? WHERE stream_id=?',
            (str(uuid.uuid4()), _stamp(instant), _stamp(instant + timedelta(seconds=LEASE_SECONDS)),
             _revision(db), state['stream_id']))
        return _public(_load(db, state['stream_id']))
    if replace_epoch is not None:
        raise ValueError('cannot replace an unregistered ingest writer')
    stream_id, epoch = str(uuid.uuid4()), str(uuid.uuid4())
    db.execute('INSERT INTO memory_ingest_streams(stream_id,producer_id,session_id,epoch,last_seen_at,lease_until,revision) '
               'VALUES(?,?,?,?,?,?,?)', (stream_id, producer_id, session_id, epoch, _stamp(instant),
                    _stamp(instant + timedelta(seconds=LEASE_SECONDS)), _revision(db)))
    return _public(_load(db, stream_id))


def report(db, stream_id, epoch, report_seq, observed_seq, scan_state, gap_count, *, lifecycle='active', now=None):
    """Monotonic producer report. Duplicate reports cannot prolong a dead heartbeat."""
    _sequence(report_seq, 'report_seq', 1); _sequence(observed_seq, 'observed_seq')
    _sequence(gap_count, 'gap_count')
    if scan_state not in SCAN_STATES or lifecycle not in ('active', 'closed'):
        raise ValueError('invalid ingest scan state or lifecycle')
    if (scan_state == 'gapped') != (gap_count > 0):
        raise ValueError('gapped scan state requires positive gap_count, and vice versa')
    schema(db)
    state = _owned(db, stream_id, epoch)
    payload = json.dumps([observed_seq, scan_state, gap_count, lifecycle], separators=(',', ':'))
    if report_seq < state['report_seq']:
        raise ValueError('stale ingest report sequence')
    if report_seq == state['report_seq']:
        if payload != state['report_json']:
            raise ValueError('ingest report identity has conflicting content')
        return _public(state)
    if observed_seq < state['observed_seq']:
        raise ValueError('observed delivery frontier cannot move backwards')
    if lifecycle == 'closed' and (scan_state != 'current' or gap_count or observed_seq != state['acked_through']):
        raise ValueError('cannot close an incomplete ingest stream')
    instant = _time(now)
    db.execute('UPDATE memory_ingest_streams SET report_seq=?,observed_seq=?,scan_state=?,gap_count=?,lifecycle=?,'
               'last_seen_at=?,lease_until=?,revision=?,report_json=? WHERE stream_id=?',
               (report_seq, observed_seq, scan_state, gap_count, lifecycle, _stamp(instant),
                _stamp(instant + timedelta(seconds=LEASE_SECONDS)), _revision(db), payload, stream_id))
    return _public(_load(db, stream_id))


def record_delivery(db, delivery, event_id, session_id, *, now=None):
    """Bind one durable raw event to transport order, without changing canonical identity."""
    if delivery is None:
        return None
    if not isinstance(delivery, dict) or set(delivery) != {'stream_id', 'epoch', 'seq'}:
        raise ValueError('delivery requires exactly stream_id, epoch and seq')
    seq = _sequence(delivery['seq'], 'delivery seq', 1)
    _identifier(event_id, 'event_id'); _identifier(session_id, 'session_id')
    schema(db)
    state = _owned(db, delivery['stream_id'], delivery['epoch'])
    from .stream_clear_transition import require_ready
    require_ready(db,state)
    if state['session_id'] != session_id:
        raise ValueError('delivery source does not belong to the registered session')
    old = db.execute('SELECT event_id FROM ('+_receipts(db)+') WHERE stream_id=? AND delivery_seq=?',
                     (state['stream_id'],seq)).fetchone()
    if old:
        if old[0]!=event_id:
            raise ValueError('delivery sequence already belongs to another raw event')
        return _public(state)
    if db.execute('SELECT 1 FROM ('+_receipts(db)+') WHERE stream_id=? AND event_id=?',
                  (state['stream_id'],event_id)).fetchone():
        raise ValueError('raw event already belongs to another delivery sequence in this stream')
    if db.execute('SELECT 1 FROM memory_raw_events WHERE id=? AND session_id=?',(event_id,session_id)).fetchone() is None:
        raise ValueError('delivery source does not belong to the registered session')
    if state['lifecycle'] == 'closed':
        raise ValueError('closed ingest stream must be explicitly reopened by report')
    instant = _time(now)
    db.execute('INSERT INTO memory_ingest_receipts VALUES(?,?,?,?)',
               (state['stream_id'], seq, event_id, _stamp(instant)))
    acked = state['acked_through']
    for row in db.execute('SELECT delivery_seq FROM ('+_receipts(db)+') WHERE stream_id=? AND delivery_seq>? '
                          'ORDER BY delivery_seq', (state['stream_id'], acked)):
        if row[0] != acked + 1:
            break
        acked = row[0]
    db.execute('UPDATE memory_ingest_streams SET observed_seq=max(observed_seq,?),acked_through=?,'
               'last_seen_at=?,lease_until=?,revision=? WHERE stream_id=?',
               (seq, acked, _stamp(instant), _stamp(instant + timedelta(seconds=LEASE_SECONDS)),
                _revision(db), state['stream_id']))
    return _public(_load(db, state['stream_id']))


def transport_status(db, *, now=None):
    """Read global known limits. No schema creation, writes, semantic filtering or model calls."""
    instant = _stamp(_time(now))
    result = {'checked_at': instant, 'revision': 0, 'stream_count': 0, 'closed_stream_count': 0,
              'known_pending_count': 0, 'source_gap_count': 0, 'incomplete_stream_count': 0,
              'disconnected_stream_count': 0, 'transport_limit': False, 'coverage': 'unobserved',
              'notice': 'Producer delivery reports do not establish unseen offline changes, source completeness or world truth.'}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_ingest_streams'").fetchone() is None:
        return result
    rows = db.execute('SELECT s.observed_seq,s.acked_through,s.scan_state,s.gap_count,s.lifecycle,s.lease_until,s.revision,'
        '(SELECT count(*) FROM ('+_receipts(db)+') r WHERE r.stream_id=s.stream_id) AS delivered '
        'FROM memory_ingest_streams s')
    unknown = False
    for observed, acked, scan, gaps, lifecycle, deadline, revision, delivered in rows:
        result['stream_count'] += 1
        result['revision'] = max(result['revision'], revision)
        pending = max(0, observed - delivered)
        result['known_pending_count'] += pending
        result['source_gap_count'] += gaps
        if lifecycle == 'closed':
            result['closed_stream_count'] += 1
            continue
        expired = deadline <= instant
        result['disconnected_stream_count'] += int(expired)
        unknown |= expired or scan == 'unknown'
        incomplete = pending > 0 or gaps > 0 or scan == 'catching_up' or (scan == 'unknown' and not expired)
        result['incomplete_stream_count'] += int(incomplete)
    result['transport_limit'] = result['incomplete_stream_count'] > 0
    result['coverage'] = ('incomplete' if result['transport_limit'] else 'unobserved' if unknown or not result['stream_count']
                          else 'reported_current')
    return result
