"""Explicit, private, resumable full rebuild; never a startup/version trigger.

Uses a full WAL-consistent clone ONLY after an operator starts this operation.
Automatic policy work belongs to bounded component queues. No native intake is
run here. A cutover requires an externally established closed-writer barrier.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from .backup import snapshot_database

from .source_reprocessing import PROTOCOL as SOURCE_POLICY
from .background_overviews import REGISTRY_PROTOCOL, AUTOMATIC_LEAF_SCHEDULING_PROTOCOL
from .topic_discovery import DISCOVERY_PROTOCOL
from .view_names import PROTOCOL as NAME_POLICY, REVIEW_FORMAT_POLICY, BINDING_POLICY
from .answer_relation_gaps import FRESHNESS_PROTOCOL
POLICIES={'source':SOURCE_POLICY,'relations':FRESHNESS_PROTOCOL,
          'views':':'.join((REGISTRY_PROTOCOL,AUTOMATIC_LEAF_SCHEDULING_PROTOCOL,DISCOVERY_PROTOCOL)),
          'names':':'.join((NAME_POLICY,REVIEW_FORMAT_POLICY,BINDING_POLICY)),
          'presentation':'verified-thematic-maps-v3'}

# Mutable queues are retained by the clone, but cannot be history-prefix checked
# after stage processing. Every other pre-existing table is guarded row-for-row.
OPERATIONAL={'memory_state','memory_views','memory_capture_jobs','memory_source_audits','memory_source_audit_cursor','memory_source_context_changes','memory_queue_schedule','memory_refresh_schedule','memory_derived_work','memory_view_name_work','memory_fts','memory_fts_data','memory_fts_idx','memory_fts_docsize','memory_fts_config','memory_source_audit_requests','memory_source_generations','memory_source_generation_events','memory_policy_work','memory_policy_admissions'}
RAW={'memory_raw_events','memory_event_positions'}

def now():return datetime.now(timezone.utc).isoformat(timespec='microseconds')
def _json(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def _sha(v):return hashlib.sha256(v).hexdigest()
def _q(name):return '"'+name.replace('"','""')+'"'
def _tables(c):return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

@contextmanager
def _read(path):
 with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');yield c

def _rows(c,table):
 definition=c.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]
 if 'WITHOUT ROWID' in definition.upper():
  keys=[r[1] for r in c.execute('PRAGMA table_info('+_q(table)+')') if r[5]]
  return c.execute('SELECT * FROM '+_q(table)+' ORDER BY '+','.join(_q(k) for k in keys))
 return c.execute('SELECT rowid,* FROM '+_q(table)+' ORDER BY rowid')
def _encoded(row):
 return _json([{'bytes':v.hex()} if isinstance(v,bytes) else v for v in row]).encode()
def _hash_rows(rows):
 h=hashlib.sha256();count=0
 for row in rows:h.update(_encoded(row)+b'\n');count+=1
 return {'count':count,'sha256':h.hexdigest()}
def ledger_digest(path):
 with _read(path) as c:return {t:_hash_rows(_rows(c,t)) for t in _tables(c)}
def raw_digest(path):
 with _read(path) as c:return {t:_hash_rows(_rows(c,t)) for t in sorted(RAW) if t in _tables(c)}

def _write(path,state):
 path=Path(path);fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w') as f:f.write(_json(state)+'\n');f.flush();os.fsync(f.fileno())
  os.replace(name,path)
  directory=os.open(path.parent,os.O_RDONLY)
  try:os.fsync(directory)
  finally:os.close(directory)
 finally:
  if os.path.exists(name):os.unlink(name)

def status(operation):
 state=json.loads(Path(operation).read_text());state['paused']=Path(operation).with_suffix('.pause').exists();return state

def pause(operation):
 marker=Path(operation).with_suffix('.pause')
 fd=os.open(marker,os.O_WRONLY|os.O_CREAT,0o600);os.close(fd)
 return status(operation)

@contextmanager
def _lock(operation):
 import fcntl
 p=_plain_path(operation).with_suffix('.lock');_plain_path(p)
 with p.open('a') as f:
  p.chmod(0o600)
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise ValueError('operation already running') from None
  yield

def _provider_identity(settings):
 if settings.get('worker_service') is not None:
  from dataclasses import asdict
  from .mcp_server import ServiceConfig
  configured=asdict(ServiceConfig(db=str(Path(settings['db']).resolve()),**settings['worker_service']))
  configured.pop('db');configured.pop('worker_idle_seconds')
  # A scoped queue launch is independent of the provider used by a rebuild.
  configured.pop('batch_source_construction')
  configured.pop('source_event_ids')
  # The normalized timeout already carries any effective Codex limit.
  configured.pop('codex_job_timeout')
  if configured['review_reasoning_effort'] in (None,configured['reasoning_effort']):
   configured.pop('review_reasoning_effort')
  return _sha(_json(configured).encode())
 from .desktop import _environment
 from .runtime_config import model_identity
 env=_environment(settings)
 embedding=[env.get(key) for key in ('ASSERTION_MEMORY_EMBEDDING_MODE','ASSERTION_MEMORY_EMBEDDING_MODEL','ASSERTION_MEMORY_EMBEDDING_ENDPOINT')]
 return _sha(_json([model_identity(environ=env),embedding]).encode())

def plan(config):
 """Metadata-only explicit preflight. It never clones, schedules or calls Model."""
 from .desktop import load_config
 settings=load_config(Path(config));source=Path(settings['db']).resolve(strict=True)
 with _read(source) as c:
  scopes=[row[0] for row in c.execute('SELECT DISTINCT scope FROM memory_raw_events ORDER BY scope')]
  raw=c.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0]
 return {'scopes':scopes,'raw_events':raw,'stage_bytes':source.stat().st_size,
         'max_concurrency':1,'admission_page':32,'max_turns_per_resume':64}

def _plain_path(path,*,initial=False):
 path=Path(path).absolute()
 if initial:
  # macOS exposes its system temporary directories through these fixed aliases.
  # Normalize them once; arbitrary user-created parent links are still refused.
  for alias,target in (('/var','/private/var'),('/tmp','/private/tmp')):
   if str(path).startswith(alias+'/') and Path(alias).is_symlink() and Path(alias).resolve()==Path(target):
    path=Path(target)/path.relative_to(alias)

 # A parent redirect is as dangerous as a leaf link. Normalize only system
 # aliases before start; frozen operation paths must remain exact afterwards.
 for parent in (path,*path.parents):
  if parent.is_symlink():raise ValueError('operation path fence: symbolic link')
 if path.resolve()!=path:raise ValueError('operation path fence: noncanonical path')
 return path

def _file_identity(path):
 stat=Path(path).stat()
 return [stat.st_dev,stat.st_ino]

def _namespace(c):
 return [c.execute('PRAGMA application_id').fetchone()[0],c.execute('PRAGMA user_version').fetchone()[0]]

def _fence(state,operation=None):
 """Read-only proof before provider setup or any writable stage constructor."""
 root=_plain_path(state['operation_root']);op=_plain_path(operation or root/'operation.json')
 if op!=root/'operation.json' or _file_identity(root)!=state['root_identity']:
  raise ValueError('operation path identity divergence')
 paths={'source_db':_plain_path(state['source_db']),
        'baseline_db':_plain_path(state['baseline_db']),'stage_db':_plain_path(state['stage_db'])}
 for key,name in (('baseline_db','baseline.sqlite3'),('stage_db','stage.sqlite3')):
  if paths[key]!=root/name:raise ValueError('operation path fence: outside operation root')
 identities=[]
 for key,path in paths.items():
  identity=_file_identity(path);expected=state['file_identities'][key]
  # Only the durable closed-writer publication state authorizes replacement of
  # the live inode, by the exact preverified candidate recorded before replace.
  allowed=[expected]
  if key=='source_db' and state['phase'] in ('cutover','published','completed') and state.get('publication_identity'):
   allowed.append(state['publication_identity'])
  if identity not in allowed:raise ValueError('operation path identity divergence: '+key)
  if identity in identities or path.stat().st_nlink!=1:raise ValueError('operation path alias fence')
  identities.append(identity)
 for path in paths.values():
  for suffix in ('-wal','-shm','-journal'):
   sidecar=_plain_path(str(path)+suffix)
   if sidecar.exists() and (_file_identity(sidecar) in identities or sidecar.stat().st_nlink!=1):raise ValueError('SQLite sidecar alias fence')
 for key,name in (('precutover_db','precutover.sqlite3'),):
  path=_plain_path(state.get(key,root/name))
  if path!=root/name:raise ValueError('operation backup path fence')
  if path.exists() and (_file_identity(path) in identities or path.stat().st_nlink!=1):raise ValueError('operation backup alias fence')
 candidate=_plain_path(paths['source_db'].with_name('.'+paths['source_db'].name+'.rebuild-'+state['operation_id']))
 if candidate.exists() and (_file_identity(candidate) in identities or candidate.stat().st_nlink!=1):raise ValueError('operation candidate alias fence')
 if _sha(Path(state['baseline_db']).read_bytes())!=state['baseline_sha256']:raise ValueError('baseline namespace divergence')
 with _read(paths['baseline_db']) as baseline,_read(paths['stage_db']) as stage:
  if _namespace(baseline)!=state['namespace'] or _namespace(stage)!=state['namespace']:raise ValueError('stage namespace divergence')
  for table in _tables(baseline):
   if table not in OPERATIONAL and table!='memory_question_routes':_prefix_equal(baseline,stage,table)
  _stage_routes(state,baseline,stage)
 return _profile(state)

def start(config,workdir,*,operation_id=None):
 from .desktop import load_config
 config=Path(config).resolve();settings=load_config(config);source=Path(settings['db']).resolve(strict=True)
 estimate=plan(config)
 root=_plain_path(workdir,initial=True);root.mkdir(parents=True,exist_ok=True,mode=0o700)
 if source==root/'stage.sqlite3' or source==root/'baseline.sqlite3':raise ValueError('operation path alias fence')
 root.chmod(0o700);op=root/'operation.json'
 with _lock(op):
  identity=operation_id or (status(op)['operation_id'] if op.exists() else uuid4().hex)
  if not isinstance(identity,str) or not identity or len(identity)>128 or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in identity):raise ValueError('invalid operation identity')
  if op.exists():
   previous=status(op)
   if previous['operation_id']!=identity or previous['config']!=str(config):raise ValueError('existing operation identity differs')
   _fence(previous,op);return op
  baseline=root/'baseline.sqlite3';stage=root/'stage.sqlite3'
  # Refuse existing aliases before backup attempts (including dangling links).
  for path in (baseline,stage,root/'precutover.sqlite3'):
   _plain_path(path)
   if path.exists():raise ValueError('operation path already exists')
  if source.stat().st_nlink!=1:raise ValueError('source path alias fence')
  snapshot_database(source,baseline);baseline.chmod(0o600);snapshot_database(baseline,stage);stage.chmod(0o600)
  with _read(baseline) as c:
   scopes=[r[0] for r in c.execute('SELECT DISTINCT scope FROM memory_raw_events ORDER BY scope')]
   raw=c.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0];namespace=_namespace(c)
  state={'schema':1,'operation_id':identity,'phase':'source','created_at':now(),'config':str(config),'config_sha256':_sha(config.read_bytes()),'provider_identity':_provider_identity(settings),'source_db':str(source),'baseline_db':str(baseline),'stage_db':str(stage),'operation_root':str(root),'root_identity':_file_identity(root),'file_identities':{key:_file_identity(path) for key,path in (('source_db',source),('baseline_db',baseline),('stage_db',stage))},'baseline_sha256':_sha(baseline.read_bytes()),'namespace':namespace,'policies':POLICIES,'estimate':{**estimate,'scopes':scopes,'raw_events':raw,'stage_bytes':baseline.stat().st_size},'source':{},'limitations':['Full operation is explicitly authorized, never automatic.','DerivedWorkQueue still hydrates a whole frozen generation; 100k end-to-end delta scaling is not established.'],'derive_requested':False,'relation_cursor':0,'view_cursor':0,'name_frontier':None,'naming_requested':False,'updated_at':now()}
  _fence(state,op);_write(op,state);return op

def _profile(state):
 config=Path(state['config'])
 if _sha(config.read_bytes())!=state['config_sha256']:raise ValueError('profile divergence; rebase required')
 settings=json.loads(config.read_text())
 if state['policies']!=POLICIES:raise ValueError('target component policy divergence; rebase explicit operation')
 if _provider_identity(settings)!=state['provider_identity']:raise ValueError('provider configuration divergence')
 if str(Path(settings['db']).resolve())!=state['source_db']:raise ValueError('profile target divergence')
 return settings

def _prefix_equal(base,current,table,*,exact=False,allow_editorial_binding=False):
 # This explicit full-operation guard preserves every baseline column, PK and
 # value. Only the named nullable binding addition may differ in a staged copy.
 original=[tuple(row)[1:] for row in base.execute('PRAGMA table_info('+_q(table)+')')]
 present=[tuple(row)[1:] for row in current.execute('PRAGMA table_info('+_q(table)+')')]
 added=[]
 if present!=original:
  allowed={'version_id':'INTEGER','semantic_content_sha256':'TEXT','source_check_id':'INTEGER'}
  if not (allow_editorial_binding and table=='memory_view_names' and present[:len(original)]==original):raise ValueError('durable schema divergence: '+table)
  added=present[len(original):]
  if not added or any(name not in allowed or kind!=allowed[name] or notnull or default is not None or pk for name,kind,notnull,default,pk in added):raise ValueError('unapproved editorial schema divergence')
 a=_rows(base,table);b=_rows(current,table)
 for row in a:
  other=b.fetchone()
  if other is None:raise ValueError('durable ledger divergence: '+table)
  values=tuple(other)
  if added:
   if any(value is not None for value in values[len(row):]):raise ValueError('historical editorial backfill divergence')
   values=values[:len(row)]
  if _encoded(row)!=_encoded(values):raise ValueError('durable ledger divergence: '+table)
 if exact and b.fetchone() is not None:raise ValueError('durable ledger divergence: '+table)

def _route_reader(connection):
 from .engine import ViewSpec
 class Store:
  @contextmanager
  def transaction(self):yield connection
 class RegistryReader:
  store=Store()
  def _spec(self,key):
   row=connection.execute('SELECT definition FROM memory_views WHERE id=?',(key,)).fetchone()
   if row is None:raise ValueError('missing route child')
   return ViewSpec(**json.loads(row[0]))
 return RegistryReader()

def _stage_routes(state,baseline,stage):
 """Only an exact operation-owned append may move a staged current pointer."""
 from .background_overviews import registered_navigation_membership
 if 'memory_question_routes' not in _tables(stage):return
 base={r['request_id']:dict(r) for r in baseline.execute('SELECT * FROM memory_question_routes')} if 'memory_question_routes' in _tables(baseline) else {}
 maximum=baseline.execute('SELECT coalesce(max(rowid),0) FROM memory_question_route_versions').fetchone()[0] if 'memory_question_route_versions' in _tables(baseline) else 0
 present=set()
 for row in stage.execute('SELECT * FROM memory_question_routes'):
  current=dict(row);present.add(row['request_id'])
  if base.get(row['request_id'])==current:continue
  version=stage.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions WHERE request_id=? ORDER BY revision DESC LIMIT 1',(row['request_id'],)).fetchone()
  if (version is None or version['route_watermark']<=maximum or version['view_id']!=row['view_id'] or version['plan']!=row['plan']):raise ValueError('stage current route projection divergence')
  plan=json.loads(version['plan'])
  if plan.get('automatic',{}).get('rebuild_operation')!=state['operation_id'] or plan['automatic'].get('scope') not in state['estimate']['scopes']:raise ValueError('stage route lacks operation-owned append')
  try:registered_navigation_membership(_route_reader(stage),version)
  except ValueError as error:raise ValueError('stage route immutable registry divergence') from error
 if not set(base)<=present:raise ValueError('stage current route lost history pointer')

def _published_routes(state,stage,target):
 """Confirm runtime projections without confusing them with immutable history.

 The closed-stage version prefix is checked by the caller. A changed current
 pointer must resolve to this target's later immutable publication, with the
 exact producer request/materialization identity; historical rollback is refused.
 """
 from .background_overviews import registered_navigation_membership,REGISTRY_PROTOCOL
 from .question_routes import PLAN_PROTOCOL,_request_spec
 base={r['request_id']:dict(r) for r in stage.execute('SELECT * FROM memory_question_routes')} if 'memory_question_routes' in _tables(stage) else {}
 if 'memory_question_routes' not in _tables(target):
  if base:raise ValueError('postflip current route projection divergence')
  return
 maximum=stage.execute('SELECT coalesce(max(rowid),0) FROM memory_question_route_versions').fetchone()[0] if 'memory_question_route_versions' in _tables(stage) else 0
 reader=_route_reader(target);present=set()
 for row in target.execute('SELECT * FROM memory_question_routes'):
  present.add(row['request_id'])
  version=target.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions WHERE request_id=? ORDER BY revision DESC LIMIT 1',(row['request_id'],)).fetchone()
  if version is None or version['view_id']!=row['view_id'] or version['plan']!=row['plan']:raise ValueError('postflip current route projection divergence')
  if version['route_watermark']<=maximum:
   if base.get(row['request_id'])!=dict(row):raise ValueError('postflip current route projection divergence')
   continue
  plan=json.loads(version['plan'])
  try:
   if plan.get('automatic',{}).get('registry_protocol')==REGISTRY_PROTOCOL:
    registry=registered_navigation_membership(reader,version)
    if not state.get('pipeline_key') or registry['pipeline_key']!=state['pipeline_key']:raise ValueError('postflip registry pipeline divergence')
    continue
   # Ordinary runtime queries can also append valid request projections after
   # restart. Recompute the identities used by their existing producer.
   request=plan['request'];spec,normalized=_request_spec(request['question'],request['scopes'],request.get('valid_as_of'),request.get('recorded_as_of'))
   if spec.id!=row['request_id'] or normalized!=request or plan.get('protocol')!=PLAN_PROTOCOL:raise ValueError('postflip route owner divergence')
   material=reader._spec(row['view_id']);method=plan.get('method');reused=plan.get('reuse_view_id')
   if material.scopes!=spec.scopes:raise ValueError('postflip route scope divergence')
   if reused is not None:
    if reused!=row['view_id'] or method not in ('model','exact') or (method=='exact' and material.query!=spec.query):raise ValueError('postflip reuse projection divergence')
    if method=='model' and reused not in plan.get('candidate_view_ids',[]):raise ValueError('postflip reuse owner divergence')
    continue
   dependencies=tuple(plan.get('depends_on',()));roots=tuple(plan.get('discovery',{}).get('unassigned_source_ids',()))
   if material.query!=spec.query or material.depends_on!=dependencies or material.root_ids!=roots:raise ValueError('postflip route definition divergence')
   if method=='model':
    identity=[spec.id,spec.query,spec.scopes,dependencies,plan['related_view_ids'],plan['protocol'],plan['answer_metadata_requests'],plan['subquestions'],roots,plan.get('discovery',{}).get('corpus_revision'),plan.get('discovery',{}).get('pipeline_key')]
    expected=spec.id+'-plan-'+_sha(json.dumps(identity,ensure_ascii=False).encode())[:24]
   elif method=='direct':expected=spec.id
   elif method=='reuse_rejected':expected=spec.id+'-applicability-fallback-'+_sha(json.dumps(plan,sort_keys=True).encode())[:16]
   else:raise ValueError('postflip route producer divergence')
   if row['view_id']!=expected:raise ValueError('postflip route materialization divergence')
  except (KeyError,TypeError,ValueError) as error:raise ValueError('postflip current route projection divergence') from error
 if not set(base)<=present:raise ValueError('postflip current route lost history pointer')

def _live_guard(state,live):
 with _read(state['baseline_db']) as baseline:
  base_tables=set(_tables(baseline));live_tables=set(_tables(live))
  if live_tables!=base_tables:raise ValueError('durable table divergence')
  for table in sorted(base_tables):
   if table in RAW:_prefix_equal(baseline,live,table)
   elif table=='memory_capture_jobs':
    for row in _rows(baseline,table):
     other=live.execute('SELECT rowid,* FROM memory_capture_jobs WHERE id=?',(row['id'],)).fetchone()
     if other is None or _encoded(row)!=_encoded(other):raise ValueError('durable ledger divergence: capture jobs')
   elif table=='memory_source_context_changes':
    _prefix_equal(baseline,live,table) # New capture context markers are allowed.
   else:_prefix_equal(baseline,live,table,exact=True)

def _catchup(operation):
 from .storage import MemoryStore
 from .queue import CaptureQueue
 from .source_reprocessing import changed
 op=Path(operation);state=status(op);_fence(state,op)
 if state['phase'] in ('cutover','published','completed'):return {'imported':0}
 imported=0
 with _read(state['source_db']) as live:
  _live_guard(state,live)
  store=MemoryStore(state['stage_db']);queue=CaptureQueue(store)
  try:
   with store.transaction(write=True) as stage:
    # Exact existing stage raw prefix and acquired positions must match live.
    for table in RAW:_prefix_equal(stage,live,table)
    for position in live.execute('SELECT event_id,position FROM memory_event_positions'):
     if stage.execute('SELECT 1 FROM memory_raw_events WHERE id=?',(position['event_id'],)).fetchone() and stage.execute('SELECT 1 FROM memory_event_positions WHERE event_id=?',(position['event_id'],)).fetchone() is None:raise ValueError('existing source position divergence; rebase required')
    maximum=stage.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0]
    rows=live.execute('SELECT rowid,* FROM memory_raw_events WHERE rowid>? ORDER BY rowid',(maximum,))
    for row in rows:
     job=live.execute('SELECT * FROM memory_capture_jobs WHERE event_id=?',(row['id'],)).fetchone()
     if job is None or job['status'] not in ('waiting_model','waiting_origin','completed') or job['phase'] not in ('extract','done') or json.loads(job['extracted_ids']) or json.loads(job['proposals']) or job['lease_token']:
      raise ValueError('durable derived divergence on new raw source')
     # Authenticated immutable transfer, no guessed message identity or text match.
     columns=list(row.keys());stage.execute('INSERT INTO memory_raw_events('+','.join(_q(k) for k in columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row))
     position=live.execute('SELECT * FROM memory_event_positions WHERE event_id=?',(row['id'],)).fetchone()
     if position is not None:stage.execute('INSERT INTO memory_event_positions VALUES(?,?)',tuple(position))
     cols=list(job.keys());stage.execute('INSERT INTO memory_capture_jobs('+','.join(_q(k) for k in cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(job))
     changed(stage,row['id'],queue._time());imported+=1
    old_scopes=state['estimate']['scopes']
    scopes=[r[0] for r in stage.execute('SELECT DISTINCT scope FROM memory_raw_events ORDER BY scope')]
    state['estimate']['scopes']=scopes
    state['estimate']['raw_events']=stage.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0]
    # The imported raw suffix and its complete generation manifest commit together.
    # A crash before the sidecar write is reconstructed from this same store.
    from .source_generation import schema as generation_schema
    generation_schema(stage)
    columns={r[1] for r in stage.execute('PRAGMA table_info(memory_source_generations)')}
    if 'manifest' not in columns:stage.execute("ALTER TABLE memory_source_generations ADD COLUMN manifest TEXT NOT NULL DEFAULT '{}'")
    stage.execute('INSERT OR IGNORE INTO memory_source_generations(operation) VALUES(?)',(state['operation_id'],))
    manifest={'scopes':scopes,'raw_events':state['estimate']['raw_events'],'raw_watermark':stage.execute('SELECT coalesce(max(rowid),0) FROM memory_raw_events').fetchone()[0],
        'relation_cursor':0 if imported or old_scopes!=scopes else state['relation_cursor'],'view_cursor':0 if imported or old_scopes!=scopes else state['view_cursor']}
    stage.execute('UPDATE memory_source_generations SET manifest=? WHERE operation=?',(_json(manifest),state['operation_id']))
  finally:store.close()
 state['updated_at']=now();state['imported_raw']=state.get('imported_raw',0)+imported
 if imported or old_scopes!=scopes or state.get('imported_frontier')!=raw_digest(state['stage_db']):
  state.update(phase='source',derive_requested=False,relation_cursor=0,view_cursor=0,naming_requested=False)
 state['imported_frontier']=raw_digest(state['stage_db'])
 _write(op,state);return {'imported':imported}

def catchup(operation):
 with _lock(operation):return _catchup(operation)

def validate(operation):
 with _lock(operation):return _validate(operation)

def _worker(state):
 from .storage import MemoryStore
 from .queue import CaptureQueue
 from .engine import MemoryEngine
 from .worker import LinkWorker
 from .desktop import _environment
 from .runtime_config import build_model,model_identity,build_candidate_search
 settings=_fence(state);environment=_environment(settings)
 store=MemoryStore(state['stage_db'])
 try:
  if settings.get('worker_service') is not None:
   from .mcp_server import ServiceConfig
   engine=ServiceConfig(db=state['stage_db'],**settings['worker_service']).engine(store)
  else:
   model=build_model(environ=environment)
   if model is None:raise ValueError('full semantic rebuild requires configured Model')
   engine=MemoryEngine(store,model=model,naming_model=model,model_key=model_identity(environ=environment),candidate_search=build_candidate_search(store,environ=environment),verify_overviews=True)
  if engine.model is None:raise ValueError('full semantic rebuild requires configured Model')
  q=CaptureQueue(store)
  return store,LinkWorker(q,engine,source_first=True)
 except BaseException:store.close();raise

def _derive(state,worker,limit):
 # One bounded page re-audits existing relations via normal assertion jobs.
 # Existing independently verified edges are immutable last positives. No deletion.
 with worker.queue.store.transaction(write=True) as c:
  rows=c.execute('SELECT rowid,assertion_id FROM memory_assertions WHERE rowid>? ORDER BY rowid LIMIT ?',(state['relation_cursor'],limit)).fetchall()
  for row in rows:
   job=c.execute('SELECT status FROM memory_capture_jobs WHERE assertion_id=?',(row['assertion_id'],)).fetchone()
   if job is None:
    c.execute("INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,updated_at,available_at) VALUES(?,?,'pending','link',?,?)",(row['assertion_id'],row['assertion_id'],worker.queue._time(),worker.queue._time()))
   elif job['status']=='running':return False
   else:c.execute("UPDATE memory_capture_jobs SET status='pending',phase='link',attempts=0,error=NULL,available_at=? WHERE assertion_id=?",(worker.queue._time(),row['assertion_id']))
   state['relation_cursor']=row['rowid']
  if len(rows)==limit:return False
  # Explicit operation target participates in frontier/checkpoint compatibility,
  # without changing the provider identity or falsifying source protocol.
  for scope in state['estimate']['scopes']:
   c.execute("INSERT INTO memory_derived_work(scope,desired_frontier,updated_at,available_at) VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET desired_frontier=json_set(desired_frontier,'$.rebuild_operation',?,'$.generation',coalesce(json_extract(desired_frontier,'$.generation'),0)+1),status=CASE WHEN status='running' THEN status ELSE 'pending' END,checkpoint=CASE WHEN status='running' THEN checkpoint ELSE '{}' END,error=NULL,error_count=0,available_at=excluded.available_at",(scope,_json({'generation':1,'rebuild_operation':state['operation_id']}),worker.queue._time(),worker.queue._time(),state['operation_id']))
  # Provider response caches are operational memoization, not history. Explicit
  # regeneration must not reuse an old overview/name response as a fresh audit.
  pass  # Existing immutable response receipts are retained; fresh work uses its operation cache namespace.
 return True

def _names(state,worker,limit):
 from .background_overviews import registered_navigation_membership
 roots=set()
 with worker.queue.store.transaction() as c:
  if state.get('name_frontier') is None:state['name_frontier']=c.execute('SELECT coalesce(max(id),0) FROM memory_view_names').fetchone()[0]
  if 'memory_question_route_versions' in _tables(c):
   for route in c.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions'):
    try:registry=registered_navigation_membership(worker.engine,route)
    except ValueError:continue
    roots.add(registry['root_view_id'])
    alias=c.execute('SELECT definition FROM memory_views WHERE id=?',(registry['root_request_id'],)).fetchone()
    if alias is not None:
     definition=json.loads(alias['definition'])
     if definition.get('query')==registry['purpose'] and definition.get('scopes')==[registry['scope']]:roots.add(registry['root_request_id'])
  rows=c.execute('SELECT rowid,id,current_id FROM memory_views WHERE rowid>? ORDER BY rowid LIMIT ?',(state['view_cursor'],limit)).fetchall()
 for row in rows:
  spec=worker.engine._spec(row['id'])
  if row['current_id'] is not None and spec.id not in roots and spec.scopes and set(spec.scopes)<=set(state['estimate']['scopes']):
   worker.names.enqueue(worker.engine,spec,authorized_scopes=spec.scopes,operation=state['operation_id'],after_name_id=state['name_frontier'])
 if rows:state['view_cursor']=rows[-1]['rowid']
 return len(rows)<limit

def _refresh_controller_epochs(worker,limit):
 # Explicit full rebuild only: complete the latest processed source epoch,
 # including cross-scope revisions. Never cancel a running frozen generation.
 from .source_revision import watermark
 from .source_omission import pending
 with worker.queue.store.transaction(write=True) as c:
  if c.execute("SELECT 1 FROM memory_capture_jobs WHERE status!='completed' LIMIT 1").fetchone() or pending(c):return
  current=watermark(c)
  rows=c.execute("SELECT scope FROM memory_derived_work WHERE status='idle' AND "
   "json_extract(result,'$.query_snapshot.source_revision_watermark') IS NOT ? ORDER BY scope LIMIT ?",(current,limit)).fetchall()
  for row in rows:
   c.execute("UPDATE memory_derived_work SET desired_frontier=json_set(desired_frontier,"
    "'$.generation',coalesce(json_extract(desired_frontier,'$.generation'),0)+1,"
    "'$.source_revision_watermark',?),status='pending',checkpoint='{}',managed_ids='[]',"
    "error=NULL,error_count=0,available_at=?,updated_at=? WHERE scope=? AND status='idle'",
    (current,worker.queue._time(),worker.queue._time(),row['scope']))

def resume(operation,*,turns=32,limit=32):
 if type(turns) is not int or not 1<=turns<=64 or type(limit) is not int or not 1<=limit<=64:raise ValueError('bounded turns/page must be 1..64')
 from . import source_generation as generation
 op=Path(operation)
 with _lock(op):
  state=status(op)
  _fence(state,op)
  if state['phase'] in ('published','completed'):return state
  op.with_suffix('.pause').unlink(missing_ok=True)
  _catchup(op);state=status(op);store,worker=_worker(state)
  state['pipeline_key']=worker.engine.pipeline_key
  try:
   for _ in range(turns):
    if op.with_suffix('.pause').exists():break
    admission=generation.admit_page(worker.queue,state['operation_id'],limit=limit)
    state['source']=generation.progress(worker.queue,state['operation_id'])
    if admission['scanned']==0 and state['source']['pending']==0 and not state['derive_requested']:
     if _derive(state,worker,limit):state['derive_requested']=True;state['phase']='derive'
    if state['derive_requested']:
     _refresh_controller_epochs(worker,limit)
     # Include newly discovered domain views. Revisit the tail on every bounded turn.
     state['naming_requested']=_names(state,worker,limit)
    result=worker.run_once();state['worker_turns']=state.get('worker_turns',0)+1
    state['updated_at']=now();_write(op,state)
    if result is None:break
  finally:store.close()
  return _validate(op)

def _name_coverage(c):
 result={'operational':0,'semantic':0}
 if 'memory_view_name_work' not in _tables(c):return result
 for row in c.execute("SELECT status,error_kind,checkpoint FROM memory_view_name_work WHERE status!='completed'"):
  cp=json.loads(row['checkpoint'])
  if row['status']=='unresolved' and row['error_kind']=='obsolete_navigation_policy':continue
  if row['status']=='unresolved' and row['error_kind']=='deterministic' and cp.get('semantic_audit'):
   result['semantic']+=1
  else:result['operational']+=1
 return result

def _validate(operation):
 from .storage import MemoryStore
 from .queue import CaptureQueue
 from . import source_generation as generation
 from .source_reprocessing import freeze
 op=Path(operation);state=status(op);_fence(state,op)
 with _read(state['source_db']) as live:_live_guard(state,live)
 store=MemoryStore(state['stage_db']);queue=CaptureQueue(store);issues=[]
 try:
  with store.transaction() as c:
   if c.execute('PRAGMA integrity_check').fetchall()[0][0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchone():issues.append('integrity')
   with _read(state['baseline_db']) as baseline:
    for table in _tables(baseline):
     if table in OPERATIONAL:continue
     if table!='memory_question_routes':_prefix_equal(baseline,c,table,allow_editorial_binding=table=='memory_view_names')
    _stage_routes(state,baseline,c)
    # Mutable current pointer/definitions may legitimately change; IDs survive.
    for row in baseline.execute('SELECT id FROM memory_views') if 'memory_views' in _tables(baseline) else ():
     if c.execute('SELECT 1 FROM memory_views WHERE id=?',(row[0],)).fetchone() is None:issues.append('lost_view_id')
   if generation.exists(c):
    for row in c.execute("SELECT * FROM memory_source_generation_events WHERE operation=? AND status='completed'",(state['operation_id'],)):
     if freeze(queue,row['event_id'],c).fingerprint!=row['input_fingerprint']:issues.append('stale_source_receipt')
   state['source']=generation.progress(queue,state['operation_id'])
   eligible=c.execute("SELECT count(*) FROM memory_raw_events").fetchone()[0]
   if state['source']['completed']!=eligible or state['source']['pending']:issues.append('source_generation_pending')
   if c.execute("SELECT 1 FROM memory_capture_jobs WHERE status NOT IN ('completed') LIMIT 1").fetchone():issues.append('source_or_relation_backlog')
   semantic_partial=state['source']['coverage'].get('unresolved',0)+state['source']['coverage'].get('failed',0)
   derived=[]
   actual_scopes=[r[0] for r in c.execute('SELECT DISTINCT scope FROM memory_raw_events ORDER BY scope')]
   if actual_scopes!=state['estimate']['scopes']:issues.append('scope_manifest_divergence')
   for scope in actual_scopes:
    row=c.execute('SELECT * FROM memory_derived_work WHERE scope=?',(scope,)).fetchone()
    if row is None:issues.append('missing_derived_scope');continue
    frontier=json.loads(row['completed_frontier']) if row['completed_frontier'] else {}
    result=json.loads(row['result']);checkpoint=json.loads(row['checkpoint'])
    terminal=bool(checkpoint.get('overview',{}).get('failure',{}).get('terminal_discovery'))
    if terminal:
     # Parsed incomplete discovery preserves usable independent accepted children.
     accepted=checkpoint.get('overview',{}).get('accepted',{})
     if not accepted:issues.append('empty_current_themes')
     if checkpoint.get('_scheduler',{}).get('frontier',{}).get('rebuild_operation')!=state['operation_id']:issues.append('stale_derived_generation')
     semantic_partial+=1
    elif row['status']!='idle' or frontier.get('rebuild_operation')!=state['operation_id']:issues.append('derived_generation_pending')
    elif result.get('completion_protocol'):
     from .controller_completion import validate_stored_completion
     from .model import ModelError
     try:validate_stored_completion(store,state['pipeline_key'],scope,result,state['operation_id'])
     except (KeyError,TypeError,ValueError,ModelError):issues.append('derived_completion_proof_invalid')
     from .source_revision import watermark
     if result.get('query_snapshot',{}).get('source_revision_watermark')!=watermark(c):issues.append('derived_generation_pending')
    elif not result.get('accepted_children'):issues.append('derived_generation_pending')
    if result.get('semantic_coverage')=='partial':semantic_partial+=1
    derived.append({'scope':scope,'status':row['status'],'themes':len(result.get('theme_ids') or checkpoint.get('overview',{}).get('accepted',{})),'semantic_partial':terminal})
   if not state['derive_requested']:issues.append('derive_not_requested')
   if not state.get('naming_requested'):issues.append('naming_admission_pending')
   naming=_name_coverage(c)
   if naming['operational']:issues.append('naming_operational_backlog')
   semantic_partial+=naming['semantic']
   state['naming']=naming
   # Failed naming remains explicit unresolved editorial coverage, never a fake name.
   state['semantic_coverage']='partial' if semantic_partial else 'complete';state['derived']=derived
 finally:store.close()
 state['issues']=sorted(set(issues));state['phase']='ready' if not issues else ('source' if 'source_generation_pending' in issues else 'derive');state['updated_at']=now();_write(op,state);return state

def cutover(operation,*,writer_pids=None,profile_sha256=None):
 """Publish at the SAME closed store path, retaining every cursor/profile binding.

 Caller must stop all owned writers/readers first and supply their exact PIDs.
 No process is killed here; an unclosed PID rejects the barrier. The profile hash
 is a CAS guard. No DB inode is replaced while one declared process survives.
 """
 if writer_pids is None or profile_sha256 is None:raise ValueError('closed writer barrier and profile hash required')
 if not isinstance(writer_pids,list) or any(type(pid) is not int or pid<=0 for pid in writer_pids):raise ValueError('invalid writer barrier PIDs')
 for pid in writer_pids:
  try:os.kill(pid,0)
  except ProcessLookupError:continue
  raise ValueError('writer barrier PID is still alive')
 op=Path(operation)
 with _lock(op):
  previous=status(op);_fence(previous,op)
  if previous['phase'] in ('published','completed'):return previous
  if previous['phase']=='cutover' and ledger_digest(previous['source_db'])==ledger_digest(previous['stage_db']):
   previous['phase']='published';_write(op,previous);return previous
  state=_validate(op)
  if state['phase']!='ready' or state['issues']:raise ValueError('stage is not ready')
  if profile_sha256!=state['config_sha256']:raise ValueError('barrier profile divergence')
  if raw_digest(state['source_db'])!=raw_digest(state['stage_db']):raise ValueError('raw frontier divergence; catch up before barrier')
  source=Path(state['source_db']);backup=op.parent/'precutover.sqlite3'
  if not backup.exists():snapshot_database(source,backup);backup.chmod(0o600)
  elif ledger_digest(source)!=ledger_digest(backup):raise ValueError('precutover backup divergence')
  # Close cleanly into standalone stores before publishing; stale WAL must never
  # be replayed against a replacement inode. This executes only under the barrier.
  with closing(sqlite3.connect(source)) as c:
   c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.execute('PRAGMA journal_mode=DELETE')
  candidate=source.with_name('.'+source.name+'.rebuild-'+state['operation_id'])
  if candidate.exists():
   if ledger_digest(candidate)!=ledger_digest(state['stage_db']):raise ValueError('stale publication candidate')
  else:snapshot_database(Path(state['stage_db']),candidate)
  candidate.chmod(0o600)
  if ledger_digest(source)!=ledger_digest(backup) or raw_digest(backup)!=raw_digest(state['stage_db']):raise ValueError('final writer frontier divergence')
  state['phase']='cutover';state['precutover_db']=str(backup);state['published_raw']=raw_digest(source);state['publication_identity']=_file_identity(candidate);_write(op,state)
  os.replace(candidate,source)
  directory=os.open(source.parent,os.O_RDONLY)
  try:os.fsync(directory)
  finally:os.close(directory)
  state['phase']='published';state['published_at']=now();state['authoritative_db']=str(source);_write(op,state)
  return state

def confirm(operation,*,verification):
 """Record operator post-restart proof; publication alone never means completed."""
 op=Path(operation)
 with _lock(op):
  state=status(op);_fence(state,op)
  if state['phase']=='completed':return state
  if state['phase']!='published':raise ValueError('operation is not published')
  proof=json.loads(Path(verification).read_text())
  if proof.get('profile_sha256')!=state['config_sha256'] or proof.get('source_db')!=state['source_db'] or proof.get('topic_projection_policy')!=POLICIES['presentation'] or proof.get('runtime_rebound') is not True:
   raise ValueError('post-restart verification does not match published operation')
  with _read(state['stage_db']) as stage,_read(state['source_db']) as target:
   if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or target.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('postflip integrity')
   if _namespace(target)!=state['namespace']:raise ValueError('postflip namespace divergence')
   for table in _tables(stage):
    if table not in OPERATIONAL and table!='memory_question_routes':_prefix_equal(stage,target,table)
   _published_routes(state,stage,target)
  state['phase']='completed';state['completed_at']=now();state['verification_sha256']=_sha(Path(verification).read_bytes());_write(op,state);return state

def main():
 parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
 p=commands.add_parser('plan');p.add_argument('--config',type=Path,required=True)
 p=commands.add_parser('start');p.add_argument('--config',type=Path,required=True);p.add_argument('--workdir',type=Path,required=True);p.add_argument('--operation-id')
 for name in ('status','resume','validate','catchup','cutover','confirm','pause'):
  p=commands.add_parser(name);p.add_argument('--operation',type=Path,required=True)
  if name=='confirm':p.add_argument('--verification',type=Path,required=True)
  if name=='resume':p.add_argument('--turns',type=int,default=32);p.add_argument('--limit',type=int,default=32)
  if name=='cutover':p.add_argument('--closed-pid',type=int,action='append',required=True);p.add_argument('--profile-sha256',required=True)
 args=parser.parse_args();values=vars(args);command=values.pop('command')
 if command=='cutover':values['writer_pids']=values.pop('closed_pid')
 result=globals()[command](**values)
 print(_json({'operation':str(result)} if isinstance(result,Path) else result))
if __name__=='__main__':main()
