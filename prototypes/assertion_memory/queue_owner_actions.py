"""TEST owner queue commands. Does not grant authorization or expose a route."""
from __future__ import annotations
from hashlib import sha256
from hmac import compare_digest
import json
import re
import secrets
import time
from uuid import uuid4

from .processing_control import paused
from .thought_processing_selection import normalize_session_ids, source_predicate, maintenance_predicate

ID=re.compile(r'[A-Za-z0-9_.:-]{1,160}\Z')
DIGEST=re.compile(r'[0-9a-f]{64}\Z')
MAX_ITEMS=25


class Conflict(ValueError):
    pass


def _table(db,name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def priority(db,queue,id_sql,revision_sql,status_sql):
    if queue not in ('source','maintenance'):
        raise ValueError('Unknown owner queue')
    if not _table(db,'memory_queue_owner_priority'):
        return 'CAST(0 AS INTEGER)'
    # Internal callers pass static SQL column identifiers only.
    return (f"EXISTS(SELECT 1 FROM memory_queue_owner_priority p WHERE p.queue='{queue}' "
            f"AND p.job_id={id_sql} AND p.revision=CAST({revision_sql} AS TEXT) AND {status_sql}='pending')")


def consume_priority(db,queue,id_):
    if _table(db,'memory_queue_owner_priority'):
        db.execute('DELETE FROM memory_queue_owner_priority WHERE queue=? AND job_id=?',(queue,id_))


def _schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS memory_queue_owner_priority ('
               'queue TEXT PRIMARY KEY,job_id TEXT NOT NULL,revision TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS memory_queue_owner_audit ('
               'id TEXT PRIMARY KEY,operation TEXT NOT NULL,actor TEXT NOT NULL,queue TEXT NOT NULL,'
               'job_id TEXT NOT NULL,stamp TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS memory_queue_cancel_confirmation ('
               'fingerprint TEXT PRIMARY KEY,actor TEXT NOT NULL,'
               'plan TEXT NOT NULL,expires INTEGER NOT NULL)')


def _selection(items):
    if type(items) is not list or not 1<=len(items)<=MAX_ITEMS:
        raise ValueError('Expected bounded job selection')
    selected=[]
    for row in items:
        if (type(row) is not dict or set(row)!={'queue','id'} or row['queue'] not in ('source','maintenance')
                or type(row['id']) is not str or not ID.fullmatch(row['id'])):
            raise ValueError('Invalid exact job reference')
        selected.append((row['queue'],row['id']))
    if len(set(selected))!=len(selected):
        raise ValueError('Duplicate job reference')
    return sorted(selected)


def _digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


class QueueOwnerActions:
    """Caller supplies a verified TEST owner plus deploy lease; none from body."""
    def __init__(self, queue, *, stage, owner_hash, session_ids, clock=time.time):
        if stage!='test' or type(owner_hash) is not str or not DIGEST.fullmatch(owner_hash):
            raise ValueError('Queue actions need verified TEST owner context')
        self.queue=queue
        self.owner=owner_hash
        self.ids=normalize_session_ids(session_ids)
        self.clock=clock

    def _lookup(self,db,kind,id_):
        if kind=='source':
            match,args=source_predicate(self.ids,'e')
            row=db.execute("SELECT j.status,j.phase,j.attempts,j.updated_at AS revision,j.available_at,"
                "j.lease_token,j.lease_until,j.extracted_ids,j.event_id FROM memory_capture_jobs j JOIN memory_raw_events e "
                "ON e.id=j.event_id WHERE j.id=? AND "+match,(id_,*args)).fetchone()
        else:
            match,args=maintenance_predicate(db,self.ids,'w')
            row=db.execute("SELECT w.status,w.action AS phase,w.attempts,w.desired_generation AS revision,"
                "w.available_at,w.lease_token,w.lease_until,NULL AS extracted_ids FROM memory_thought_work w "
                "WHERE w.thought_id=? AND w.desired_generation>w.completed_generation AND "+match,(id_,*args)).fetchone()
        if row is None:
            raise Conflict('Queue item not selectable')
        return dict(row)

    @staticmethod
    def _unclaimed(row):
        return row['lease_token'] is None and row['lease_until'] is None and row['status']!='running'

    def _audit(self,db,operation,kind,id_,stamp):
        db.execute('INSERT INTO memory_queue_owner_audit VALUES (?,?,?,?,?,?)',
                   (uuid4().hex,operation,self.owner,kind,id_,stamp))

    def take(self,item):
        (kind,id_),=_selection([item])
        with self.queue.store.transaction(write=True) as db:
            if paused(db):
                raise Conflict('Processing is paused')
            row=self._lookup(db,kind,id_)
            now=self.queue._time()
            if not self._unclaimed(row) or row['status']!='pending' or row['available_at']>now:
                raise Conflict('Only a ready unclaimed item can be prioritized')
            _schema(db)
            db.execute('INSERT INTO memory_queue_owner_priority VALUES (?,?,?) '
                       'ON CONFLICT(queue) DO UPDATE SET job_id=excluded.job_id,revision=excluded.revision',
                       (kind,id_,str(row['revision'])))
            self._audit(db,'prioritized',kind,id_,now)
        return {'stage':'test','queue':kind,'id':id_,'status':'prioritized'}

    def batch_enqueue(self,items):
        selected=_selection(items)
        with self.queue.store.transaction(write=True) as db:
            now=self.queue._time()
            records=[(kind,id_,self._lookup(db,kind,id_)) for kind,id_ in selected]
            if any(not self._unclaimed(row) or row['status'] not in ('pending','failed','cancelled')
                   or row['status']=='cancelled' and kind!='source' for kind,_,row in records):
                raise Conflict('Selection includes an ineligible or active item')
            _schema(db)
            queued=[]
            for kind,id_,row in records:
                if row['status']=='pending':
                    continue  # Idempotent, including future backoff.
                if kind=='source':
                    self.queue.retry(id_)  # Canonical retry, never takes a lease.
                else:
                    db.execute("UPDATE memory_thought_work SET status='pending',available_at=?,attempts=0,error=NULL "
                               "WHERE thought_id=? AND status='failed'",(now,id_))
                consume_priority(db,kind,id_)
                self._audit(db,'enqueued',kind,id_,now)
                queued.append({'queue':kind,'id':id_})
        return {'stage':'test','queued':queued,'already_pending':len(selected)-len(queued)}

    def _cancel_plan(self,db,selected):
        records=[]
        for kind,id_ in selected:
            row=self._lookup(db,kind,id_)
            if (kind!='source' or row['status']!='pending' or row['phase']!='extract'
                    or row['attempts']!=0 or row['extracted_ids']!='[]' or not self._unclaimed(row)):
                raise Conflict('Only an unstarted source job can be cancelled')
            if (db.execute('SELECT 1 FROM memory_source_draft_checkpoints WHERE job_id=?',(id_,)).fetchone()
                    or db.execute('SELECT 1 FROM memory_extraction_runs WHERE event_id=? LIMIT 1',(row['event_id'],)).fetchone()):
                raise Conflict('Cannot cancel work with an extraction receipt or draft checkpoint')
            records.append((kind,id_,row['revision'],row['available_at']))
        return _digest(json.dumps(records,separators=(',',':')))

    def prepare_cancel(self,items):
        selected=_selection(items)
        with self.queue.store.transaction(write=True) as db:
            plan=self._cancel_plan(db,selected)
            _schema(db)
            now=int(self.clock())
            db.execute('DELETE FROM memory_queue_cancel_confirmation WHERE expires<=? OR actor=?',(now,self.owner))
            token=secrets.token_hex(32)
            db.execute('INSERT INTO memory_queue_cancel_confirmation VALUES (?,?,?,?)',
                       (_digest(token),self.owner,plan,now+90))
        return {'stage':'test','confirmation_id':token,'phrase':'ОТМЕНИТЬ TEST','count':len(selected),'expires_in':90}

    def cancel(self,items,*,confirmation_id,confirmation):
        selected=_selection(items)
        if (type(confirmation_id) is not str or not DIGEST.fullmatch(confirmation_id)
                or confirmation!='ОТМЕНИТЬ TEST'):
            raise Conflict('Exact TEST confirmation required')
        with self.queue.store.transaction(write=True) as db:
            if not _table(db,'memory_queue_cancel_confirmation'):
                raise Conflict('Missing cancellation confirmation')
            key=_digest(confirmation_id)
            row=db.execute('SELECT actor,plan,expires FROM memory_queue_cancel_confirmation '
                           'WHERE fingerprint=?',(key,)).fetchone()
            if (row is None or not compare_digest(row[0],self.owner) or row[2]<=int(self.clock())
                    or not compare_digest(row[1],self._cancel_plan(db,selected))):
                raise Conflict('Cancellation confirmation is stale or belongs to another owner')
            db.execute('DELETE FROM memory_queue_cancel_confirmation WHERE fingerprint=?',(key,))
            now=self.queue._time()
            for kind,id_ in selected:
                db.execute("UPDATE memory_capture_jobs SET status='cancelled',updated_at=? WHERE id=?",(now,id_))
                consume_priority(db,kind,id_)
                self._audit(db,'cancelled',kind,id_,now)
        return {'stage':'test','cancelled':len(selected)}
