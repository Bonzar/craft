"""Explicit source generations: bounded admission, lease-bound audit receipts.

No material-gap request is fabricated. Operational receipts are separate from
source_revision semantic coverage; identical run deduplication is preserved.
"""
from __future__ import annotations
import json

def schema(c):
 c.execute('CREATE TABLE IF NOT EXISTS memory_source_generations(operation TEXT PRIMARY KEY,cursor INTEGER NOT NULL DEFAULT 0,context_count INTEGER NOT NULL DEFAULT 0)')
 c.execute('CREATE TABLE IF NOT EXISTS memory_source_generation_events(operation TEXT NOT NULL,event_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT \'pending\',input_fingerprint TEXT,lease_token TEXT,run_id INTEGER,completed_at TEXT,coverage TEXT,PRIMARY KEY(operation,event_id))')
 c.execute('CREATE INDEX IF NOT EXISTS memory_source_generation_pending ON memory_source_generation_events(operation,status,event_id)')

def exists(c):return c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_source_generation_events'").fetchone() is not None

def admit_page(queue,operation,*,limit=32):
 if not isinstance(operation,str) or not operation or len(operation)>128:raise ValueError('bounded operation identity required')
 if type(limit) is not int or not 1<=limit<=64:raise ValueError('generation page limit must be 1..64')
 from .source_reprocessing import _schedule
 with queue.store.transaction(write=True) as c:
  schema(c);c.execute('INSERT OR IGNORE INTO memory_source_generations(operation) VALUES(?)',(operation,))
  # Older generations counted assistant sources as skipped context. Revisit that
  # operation once; INSERT OR IGNORE preserves every existing source receipt.
  c.execute('UPDATE memory_source_generations SET cursor=0,context_count=0 WHERE operation=? AND context_count>0',(operation,))
  cursor=c.execute('SELECT cursor FROM memory_source_generations WHERE operation=?',(operation,)).fetchone()[0]
  rows=c.execute("SELECT e.id,e.actor,json_extract(e.metadata,'$.role') AS role,e.rowid AS intake,j.status FROM memory_raw_events e JOIN memory_capture_jobs j ON j.event_id=e.id WHERE e.rowid>? ORDER BY e.rowid LIMIT ?",(cursor,limit)).fetchall()
  context=0
  for row in rows:
   c.execute('INSERT OR IGNORE INTO memory_source_generation_events(operation,event_id) VALUES(?,?)',(operation,row['id']))
   _schedule(queue,c,row,force=True)
  if rows:c.execute('UPDATE memory_source_generations SET cursor=?,context_count=context_count+? WHERE operation=?',(rows[-1]['intake'],context,operation))
  # Work already linked/leased at admission receives its independent audit after
  # its owner finishes. Never revoke a lease or mistake that older work for a receipt.
  pending=c.execute("SELECT e.id,e.actor,json_extract(e.metadata,'$.role') AS role,j.status FROM memory_source_generation_events g JOIN memory_raw_events e ON e.id=g.event_id JOIN memory_capture_jobs j ON j.event_id=e.id WHERE g.operation=? AND g.status='pending' AND j.status IN ('completed','failed') ORDER BY g.event_id LIMIT ?",(operation,limit)).fetchall()
  for row in pending:_schedule(queue,c,row,force=True)
  return {'scanned':len(rows),'context':context,'cursor':rows[-1]['intake'] if rows else cursor}

def bind(queue,claim,frozen):
 with queue.store.transaction(write=True) as c:
  if not exists(c):return
  queue._assert_owned(c,claim)
  c.execute("UPDATE memory_source_generation_events SET status='running',lease_token=?,input_fingerprint=? WHERE event_id=? AND (status IN ('pending','running') OR (status='completed' AND input_fingerprint!=?))",(claim.token,frozen.fingerprint,frozen.event.id,frozen.fingerprint))

def finish(c,claim,frozen,*,run_id,coverage,completed_at,operational_errors):
 if not exists(c):return
 if operational_errors:return
 c.execute("UPDATE memory_source_generation_events SET status='completed',run_id=?,coverage=?,completed_at=?,lease_token=NULL WHERE event_id=? AND lease_token=? AND input_fingerprint=? AND status='running'",(run_id,coverage,completed_at,frozen.event.id,claim.token,frozen.fingerprint))

def progress(queue,operation):
 with queue.store.transaction() as c:
  if not exists(c):return {'completed':0,'pending':0,'context':0,'coverage':{}}
  counts=dict(c.execute('SELECT status,count(*) FROM memory_source_generation_events WHERE operation=? GROUP BY status',(operation,)))
  coverage=dict(c.execute("SELECT coverage,count(*) FROM memory_source_generation_events WHERE operation=? AND status='completed' GROUP BY coverage",(operation,)))
  row=c.execute('SELECT cursor,context_count FROM memory_source_generations WHERE operation=?',(operation,)).fetchone()
  return {'completed':counts.get('completed',0),'pending':sum(n for k,n in counts.items() if k!='completed'),'context':row['context_count'] if row else 0,'cursor':row['cursor'] if row else 0,'coverage':coverage}
