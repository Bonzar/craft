"""Atomic generic raw batch intake. Members remain individual source events."""
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar

BATCH_ID = re.compile(r'[0-9a-f]{64}')
MAX_MEMBERS = 128
MAX_RAW_CHARACTERS = 256000
OPTIONAL = frozenset(('position','scope','source_message_at','source_message_clock_basis','timezone','artifact_ids'))
REQUIRED = frozenset(('session_id','message_id','actor_id','role','text','source','delivery'))
_API_AUTHORIZED = ContextVar('capture_batch_api_authorized', default=False)


@contextmanager
def authorized_machine_batch():
    """The authenticated, lane-scoped API grants batch intake, not device identity."""
    token = _API_AUTHORIZED.set(True)
    try:
        yield
    finally:
        _API_AUTHORIZED.reset(token)


def digest(document):
    return hashlib.sha256(json.dumps(document,sort_keys=True,separators=(',',':'),
                               ensure_ascii=True,allow_nan=False).encode('ascii')).hexdigest()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS memory_capture_batches ('
               'batch_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, session_id TEXT NOT NULL, '
               'member_digest TEXT NOT NULL, request_digest TEXT NOT NULL, '
               'state TEXT NOT NULL, error_code TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS memory_capture_batch_state ON memory_capture_batches(state)')
    db.execute('CREATE TABLE IF NOT EXISTS memory_capture_batch_members ('
               'batch_id TEXT NOT NULL REFERENCES memory_capture_batches(batch_id) ON DELETE CASCADE, '
               'ordinal INTEGER NOT NULL, event_id TEXT UNIQUE NOT NULL '
               'REFERENCES memory_raw_events(id) ON DELETE CASCADE, PRIMARY KEY(batch_id,ordinal))')
    db.execute("CREATE TRIGGER IF NOT EXISTS memory_capture_batch_no_partial_delete "
               "BEFORE DELETE ON memory_raw_events WHEN EXISTS (SELECT 1 "
               "FROM memory_capture_batch_members m JOIN memory_capture_batches b "
               "ON b.batch_id=m.batch_id WHERE m.event_id=OLD.id AND b.state IN ('pending','dispatching')) "
               "BEGIN SELECT RAISE(ABORT,'active raw batch must be cancelled atomically'); END")


def batch_table_exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_capture_batches'").fetchone() is not None


def active_member(db,event_id):
    return (batch_table_exists(db) and db.execute(
        "SELECT 1 FROM memory_capture_batch_members m JOIN memory_capture_batches b "
        "ON b.batch_id=m.batch_id WHERE m.event_id=? AND b.state IN ('pending','dispatching')",
        (event_id,)).fetchone() is not None)


def capture(service, batch_id, messages):
    """Authorize once; commit the exact envelope and individual source receipts once."""
    if not _API_AUTHORIZED.get():
        raise ValueError('raw batches require the authenticated machine API')
    from .thought_service import ThoughtService
    if not isinstance(service,ThoughtService):
        raise ValueError('raw batches require the canonical thought runtime')
    if not isinstance(batch_id,str) or not BATCH_ID.fullmatch(batch_id):
        raise ValueError('invalid stable batch identity')
    if type(messages) is not list or not 1<=len(messages)<=MAX_MEMBERS:
        raise ValueError('unsupported batch member count')
    for row in messages:
        if (type(row) is not dict or not REQUIRED <= row.keys() <= REQUIRED|OPTIONAL
                or any(not isinstance(row[k],str) or not row[k].strip() for k in REQUIRED-{'delivery'})
                or type(row['delivery']) is not dict or set(row['delivery'])!={'stream_id','epoch','seq'}):
            raise ValueError('invalid batch member')
    if sum(len(row['text']) for row in messages)>MAX_RAW_CHARACTERS:
        raise ValueError('batch content exceeds the cumulative character limit')
    first=messages[0]
    session=first['session_id']
    stream,epoch,seq=(first['delivery'][k] for k in ('stream_id','epoch','seq'))
    members=[row['message_id'] for row in messages]
    if (type(seq) is not int or seq<1 or len(set(members))!=len(members)
            or any(row['session_id']!=session or row['delivery']!={'stream_id':stream,'epoch':epoch,'seq':seq+i}
                   for i,row in enumerate(messages))):
        raise ValueError('batch requires one stream, distinct messages and consecutive ordered delivery')
    # An ownership-validated epoch may rotate without changing the same envelope.
    member_digest=digest(members)
    request_digest=digest([row | {'delivery':{k:v for k,v in row['delivery'].items() if k!='epoch'}} for row in messages])
    with service.store.transaction(write=True) as db:
        schema(db)
        from .ingest_streams import _owned, schema as stream_schema
        stream_schema(db)
        if _owned(db,stream,epoch)['session_id'] != session:
            raise ValueError('batch does not belong to the registered session')
        prior=db.execute('SELECT stream_id,session_id,member_digest,request_digest FROM memory_capture_batches WHERE batch_id=?',
                         (batch_id,)).fetchone()
        identity=(stream,session,member_digest,request_digest)
        if prior is not None and tuple(prior)!=identity:
            raise ValueError('batch identity is immutable')
        if prior is not None:
            held=db.execute('SELECT count(*) FROM memory_capture_batch_members WHERE batch_id=?',
                            (batch_id,)).fetchone()[0]
            if held!=len(members):
                raise ValueError('cannot replay a batch after a member was removed')
        if prior is None:
            placeholders=','.join('?' for _ in members)
            if db.execute('SELECT 1 FROM memory_message_receipts WHERE session_id=? AND message_id IN ('+placeholders+') LIMIT 1',
                          (session,*members)).fetchone():
                raise ValueError('cannot rebundle older individually captured messages')
            db.execute("INSERT INTO memory_capture_batches VALUES(?,?,?,?,?,'pending',NULL)",(batch_id,*identity))
        receipts=[]
        for i,row in enumerate(messages):
            result=service.capture_message(**(row|{'position':row.get('position')}),_capture_batch_id=batch_id)
            event=result['event']['id']
            if prior is None:
                db.execute('INSERT INTO memory_capture_batch_members VALUES(?,?,?)',(batch_id,i,event))
            elif db.execute('SELECT event_id FROM memory_capture_batch_members WHERE batch_id=? AND ordinal=?',
                            (batch_id,i)).fetchone()[0]!=event:
                raise ValueError('batch receipt belongs to another raw event')
            # No N copies of possibly large status sections.
            receipts.append({'event':result['event'],'delivery':result['delivery']})
        return {'batch_id':batch_id,'receipts':receipts}


def pending(db):
    return (batch_table_exists(db) and db.execute(
        "SELECT 1 FROM memory_capture_batches WHERE state='pending' LIMIT 1").fetchone() is not None)


def _session_scope(db, *, alias=''):
    from .queue_session_control import read
    from .processing_control import INITIAL
    selected,revision=read(db)
    return ('',()) if revision==INITIAL else (f' AND {alias}session_id IN (SELECT value FROM json_each(?))',(json.dumps(selected),))

def needs_wake(db):
    if not batch_table_exists(db):
        return False
    scope,args=_session_scope(db)
    # Always allow recovery of a previously dispatched batch, even when its
    # session has since been excluded. This does not dispatch any provider work.
    return db.execute("SELECT 1 FROM memory_capture_batches WHERE state='dispatching' "
                      "OR (state='pending'"+scope+") LIMIT 1",args).fetchone() is not None


def fence_interrupted(db):
    if batch_table_exists(db):
        db.execute("UPDATE memory_capture_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
                   "error='Unknown batch provider outcome requires review' WHERE status!='completed' "
                   "AND event_id IN (SELECT m.event_id FROM memory_capture_batch_members m "
                   "JOIN memory_capture_batches b ON b.batch_id=m.batch_id WHERE b.state='dispatching')")
        db.execute("UPDATE memory_capture_batches SET state='blocked',error_code='provider_outcome_unknown' "
                   "WHERE state='dispatching'")


def run_next(config, store, is_pausing):
    """Use the existing exclusive worker. Unknown interrupted provider outcome is never retried."""
    if is_pausing():
        return False
    from .processing_control import paused as processing_paused
    from .thought_processing import TracedThoughtWorker
    with store.transaction(write=True) as db:
        if processing_paused(db) or not batch_table_exists(db):
            return False
        scope,args=_session_scope(db)
        batch=db.execute("SELECT batch_id,session_id,member_digest FROM memory_capture_batches "
                         "WHERE state='pending'"+scope+" ORDER BY rowid LIMIT 1",args).fetchone()
        if batch is None:
            return False
        batch_id,session,membership=batch
        rows=db.execute("SELECT m.event_id,json_extract(e.metadata,'$.message_id') "
                        'FROM memory_capture_batch_members m JOIN memory_raw_events e ON e.id=m.event_id '
                        'WHERE m.batch_id=? ORDER BY m.ordinal',(batch_id,)).fetchall()
        if not rows or digest([row[1] for row in rows])!=membership:
            db.execute("UPDATE memory_capture_batches SET state='blocked',error_code='batch_member_removed' "
                       "WHERE batch_id=?",(batch_id,))
            return True
        # Take the one dispatch permit before provider IO. On crash no other
        # worker can replay this uncertain call; the raw receipt remains durable.
        db.execute("UPDATE memory_capture_batches SET state='dispatching' WHERE batch_id=?",(batch_id,))
        event_ids=[r[0] for r in rows]
    current=config.service(store)
    worker=TracedThoughtWorker(current.queue,current.engine,lock_owned=True,
                                processing_session_ids=(session,),is_pausing=is_pausing)
    state,code='blocked','provider_outcome_or_review_unconfirmed'
    try:
        worker.process_source_group(event_ids,len(event_ids),capture_batch_id=batch_id)
        if event_ids and all(current.queue.get(e).status=='completed' for e in event_ids):
            state,code='completed',None
    except Exception as exc:
        # The group trace already keeps stage/call outcome. No bodies or arbitrary
        # exception text in group state or automatic per-message fallback.
        code='batch_'+type(exc).__name__[:70]
    with store.transaction(write=True) as db:
        db.execute('UPDATE memory_capture_batches SET state=?,error_code=? WHERE batch_id=?',
                   (state,code,batch_id))
        if state=='blocked':
            db.execute("UPDATE memory_capture_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
                       "error='Atomic source batch requires manual review' WHERE status!='completed' "
                       "AND event_id IN (SELECT event_id FROM memory_capture_batch_members WHERE batch_id=?)",
                       (batch_id,))
    return True
