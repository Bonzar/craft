"""Declared, bounded affected-component dispatch, never a whole-base rebuild.

A release registers an explicit indexed scope/range recipe. Unknown policy or git
SHA changes do not infer a global source migration. The existing worker resumes
one durable cursor page per turn; no backup, source snapshot, or provider call.
"""
from __future__ import annotations
import hashlib
import json

COMPONENTS=frozenset(('source','relations','views','names','presentation'))

def schema(c):
 c.execute('CREATE TABLE IF NOT EXISTS memory_policy_work(id TEXT PRIMARY KEY,component TEXT NOT NULL,old_policy TEXT NOT NULL,new_policy TEXT NOT NULL,scope TEXT NOT NULL,cursor INTEGER NOT NULL,end_cursor INTEGER NOT NULL,status TEXT NOT NULL)')
 columns={r[1] for r in c.execute('PRAGMA table_info(memory_policy_work)')}
 if 'admission_cursor' not in columns:c.execute("ALTER TABLE memory_policy_work ADD COLUMN admission_cursor TEXT NOT NULL DEFAULT ''")
 c.execute('CREATE INDEX IF NOT EXISTS memory_policy_pending ON memory_policy_work(status,id)')
 c.execute('CREATE INDEX IF NOT EXISTS memory_policy_assertion_job ON memory_capture_jobs(assertion_id)')
 c.execute('CREATE INDEX IF NOT EXISTS memory_raw_scope ON memory_raw_events(scope)')
 c.execute('CREATE INDEX IF NOT EXISTS memory_policy_assertion_scope ON memory_assertions(scope)')
 # A cursor cannot represent a leased or missing job, nor prove its execution.
 # One indexed receipt per affected relation target preserves that obligation
 # without rewriting an ever-growing JSON map at every tick.
 c.execute('CREATE TABLE IF NOT EXISTS memory_policy_admissions(work_id TEXT NOT NULL REFERENCES memory_policy_work(id),assertion_id TEXT NOT NULL REFERENCES memory_assertions(assertion_id),status TEXT NOT NULL,job_id TEXT,lease_token TEXT,completed_at TEXT,PRIMARY KEY(work_id,assertion_id))')
 c.execute('CREATE INDEX IF NOT EXISTS memory_policy_admission_pending ON memory_policy_admissions(work_id,status,assertion_id)')
 c.execute('CREATE INDEX IF NOT EXISTS memory_policy_admission_job ON memory_policy_admissions(job_id,status)')

def register(queue,*,component,old,new,scope,start,end):
 if component not in COMPONENTS or not all(isinstance(v,str) and v for v in (old,new,scope)) or type(start) is not int or type(end) is not int or start<0 or end<start:raise ValueError('declared affected component/scope/range required')
 identity=hashlib.sha256(json.dumps([component,old,new,scope,start,end]).encode()).hexdigest()
 with queue.store.transaction(write=True) as c:
  schema(c);c.execute('INSERT OR IGNORE INTO memory_policy_work(id,component,old_policy,new_policy,scope,cursor,end_cursor,status) VALUES(?,?,?,?,?,?,?,?)',(identity,component,old,new,scope,start,end,'completed' if old==new or component=='presentation' else 'pending'))
 return identity

def _admit_relations(queue,c,work,limit):
 # Visit each deferred target fairly, including blocked owners. Both halves
 # seek the same (work_id,status,assertion_id) index and together read <= limit.
 cursor=work['admission_cursor']
 rows=c.execute("SELECT * FROM memory_policy_admissions WHERE work_id=? AND status='deferred' AND assertion_id>? ORDER BY assertion_id LIMIT ?",(work['id'],cursor,limit)).fetchall()
 if len(rows)<limit:
  rows+=c.execute("SELECT * FROM memory_policy_admissions WHERE work_id=? AND status='deferred' AND assertion_id<=? ORDER BY assertion_id LIMIT ?",(work['id'],cursor,limit-len(rows))).fetchall()
 if rows:c.execute('UPDATE memory_policy_work SET admission_cursor=? WHERE id=?',(rows[-1]['assertion_id'],work['id']))
 for target in rows:
  job=c.execute('SELECT * FROM memory_capture_jobs WHERE assertion_id=?',(target['assertion_id'],)).fetchone()
  if job is None:
   job_id='policy-relation-'+hashlib.sha256(target['assertion_id'].encode()).hexdigest()
   c.execute("INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,updated_at,available_at) VALUES(?,?,'pending','link',?,?)",(job_id,target['assertion_id'],queue._time(),queue._time()))
  elif job['status'] in ('completed','failed') and job['attempts']<queue._max_attempts:
   job_id=job['id']
   c.execute("UPDATE memory_capture_jobs SET status='pending',phase='link',error=NULL,available_at=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?",(queue._time(),queue._time(),job_id))
  else:continue # Never steal existing work or reset exhausted semantic attempts.
  c.execute("UPDATE memory_policy_admissions SET status='queued',job_id=? WHERE work_id=? AND assertion_id=?",(job_id,work['id'],target['assertion_id']))
 return len(rows)


def bind_claim(c,job_id,token,phase):
 if phase!='link' or not c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_policy_admissions'").fetchone():return
 c.execute("UPDATE memory_policy_admissions SET status='running',lease_token=? WHERE job_id=? AND status IN ('queued','running')",(token,job_id))


def finish_relations(c,claim,completed_at,*,waiting_model=False):
 if waiting_model or not c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_policy_admissions'").fetchone():return
 c.execute("UPDATE memory_policy_admissions SET status='completed',completed_at=?,lease_token=NULL WHERE job_id=? AND lease_token=? AND status='running'",(completed_at,claim.job.id,claim.token))


def tick(queue,engine,*,limit=32):
 if type(limit) is not int or not 1<=limit<=64:raise ValueError('policy page must be 1..64')
 from .source_reprocessing import _schedule
 with queue.store.transaction(write=True) as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_policy_work'").fetchone():return {'scanned':0,'status':'idle'}
  work=c.execute("SELECT * FROM memory_policy_work WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
  if work is None:return {'scanned':0,'status':'completed'}
  component=work['component'];cursor=work['cursor'];end=work['end_cursor'];scope=work['scope']
  if component=='source':
   rows=c.execute("SELECT e.rowid AS intake,e.id,e.actor,json_extract(e.metadata,'$.role') AS role,j.status FROM memory_raw_events e LEFT JOIN memory_capture_jobs j ON j.event_id=e.id WHERE e.scope=? AND e.rowid>? AND e.rowid<=? ORDER BY e.rowid LIMIT ?",(scope,cursor,end,limit)).fetchall()
   for row in rows:
    if row['status'] is not None:_schedule(queue,c,row,force=True)
  elif component=='relations':
   rows=c.execute('SELECT rowid AS intake,assertion_id FROM memory_assertions WHERE scope=? AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',(scope,cursor,end,limit)).fetchall()
   for row in rows:c.execute("INSERT OR IGNORE INTO memory_policy_admissions(work_id,assertion_id,status) VALUES(?,?,'deferred')",(work['id'],row['assertion_id']))
   _admit_relations(queue,c,work,limit)
  elif component=='names':
   rows=c.execute('SELECT rowid AS intake,id FROM memory_views WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',(cursor,end,limit)).fetchall()
   for row in rows:
    spec=engine._spec(row['id'])
    if spec.scopes==(scope,):
     frontier=c.execute('SELECT coalesce(max(id),0) FROM memory_view_names').fetchone()[0]
     from .view_name_work import NameWorkQueue
     NameWorkQueue(queue.store).enqueue(engine,spec,authorized_scopes=(scope,),operation=work['id'],after_name_id=frontier)
  elif component=='views':
   # The release supplies exact affected scope; one scope invalidation, then done.
   c.execute("UPDATE memory_derived_work SET desired_frontier=json_set(json_remove(desired_frontier,'$.rebuild_operation'),'$.policy_revision',?,'$.generation',coalesce(json_extract(desired_frontier,'$.generation'),0)+1),status=CASE WHEN status='running' THEN status ELSE 'pending' END,checkpoint=CASE WHEN status='running' THEN checkpoint ELSE '{}' END,error=NULL,error_count=0,available_at=? WHERE scope=?",(work['new_policy'],queue._time(),scope));rows=[]
  else:rows=[]
  cursor=rows[-1]['intake'] if rows else end;status='completed' if len(rows)<limit or cursor>=end else 'pending'
  if component=='relations' and c.execute("SELECT 1 FROM memory_policy_admissions WHERE work_id=? AND status IN ('deferred','queued','running') LIMIT 1",(work['id'],)).fetchone():status='pending'
  c.execute('UPDATE memory_policy_work SET cursor=?,status=? WHERE id=?',(cursor,status,work['id']))
  return {'scanned':len(rows),'cursor':cursor,'status':status,'component':component}
