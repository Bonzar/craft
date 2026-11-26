"""Owner-confirmed logical reset. No filesystem target, SQL, or row input from clients."""
from contextlib import contextmanager
from hashlib import sha256
import fcntl
import hmac
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from .processing_control import Conflict

PATH='/api/v1/queue-database/clear'
STATE='memory_database_clear_state'
TOMBSTONES='memory_database_clear_replay_fence'
INITIAL='0'*32
PRESERVE={STATE,TOMBSTONES,'memory_processing_control','memory_state',
          'memory_thought_runtime','memory_test_queue_sessions','memory_queue_processing_policy','memory_queue_pause_nonce'}
PROVENANCE=('memory_run_provenance','memory_run_causes','memory_run_cause_links','memory_run_events')
IMMUTABLE_DELETE={'memory_artifacts_no_delete','memory_view_names_no_delete',
                  *(name+'_no_delete' for name in PROVENANCE)}
VIRTUAL={'memory_fts','memory_thought_fts'}
DATA=set("""
memory_artifacts memory_assertions memory_branch_reviews memory_capture_batch_members
memory_capture_batches memory_capture_jobs memory_derived_work memory_edges memory_embedding_cache
memory_event_positions memory_extraction_runs memory_fresh_mac_events memory_fresh_mac_streams
memory_historical_checks memory_ingest_receipts memory_ingest_retired_receipts memory_ingest_streams
memory_interactive_queries memory_message_receipts memory_one_message_run memory_policy_admissions
memory_policy_work memory_question_route_versions memory_question_routes memory_queue_schedule
memory_raw_events memory_refresh_generations memory_refresh_schedule memory_run_cause_links
memory_run_causes memory_run_events memory_run_provenance memory_source_admission
memory_source_admission_clock memory_source_audit_cursor memory_source_audit_requests memory_source_audits
memory_source_context_changes memory_source_draft_checkpoints memory_source_generation_events
memory_source_generations memory_test_queue_removals memory_thought_cause_sessions
memory_thought_dependencies memory_thought_draft_cycles memory_thought_generations memory_thought_legacy
memory_thought_legacy_compaction memory_thought_legacy_exclusions memory_thought_links memory_thought_migration
memory_thought_model_calls memory_thought_model_payloads memory_thought_purge_blockers memory_thought_purge_candidates
memory_thought_purge_legacy_refs memory_thought_purge_raw_refs memory_thought_purge_refs memory_thought_purge_state
memory_thought_retirements memory_thought_reuse_checks memory_thought_runs memory_thought_source_cleanup
memory_thought_trace_events memory_thought_versions memory_thought_work memory_thought_work_causes memory_thoughts
memory_view_checks memory_view_name_work memory_view_names memory_view_versions memory_views
native_occurrence_receipts
""".split())
# These identities are deliberately not persisted as readable documents: keyed
# opaque digests prevent a producer from recreating wiped messages via retries.
IDENTITIES={'messages':('memory_message_receipts',('session_id','message_id')),
            'native':('native_occurrence_receipts',('path','physical_session','turn_id','occurrence'))}


@contextmanager
def database_lease(database, *, exclusive=False, worker=False):
    suffix='.worker.lock' if worker else '.clear.lock'
    path=Path(str(database)+suffix)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=os.geteuid()
                or st.st_mode&0o022 or st.st_size!=0):
            raise OSError('invalid reset lease')
        fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        if not os.path.samestat(st,path.lstat()):
            raise OSError('changed reset lease')
        yield
    finally:
        os.close(fd)


def _inventory(db):
    # Table names are negotiated neither by callers nor by untrusted prefixes.
    # SQLite marks FTS shadow storage; only exact known parents are accepted.
    managed=set()
    for schema,name,kind,*_ in db.execute('PRAGMA table_list'):
        if schema!='main' or name in ('sqlite_schema','sqlite_sequence'):
            continue
        if kind=='shadow' and any(name in {v+'_'+s for s in ('config','content','data','docsize','idx')} for v in VIRTUAL):
            continue
        if (kind=='virtual' and name in VIRTUAL) or (kind=='table' and name in DATA|PRESERVE):
            managed.add(name)
            continue
        raise ValueError('unclassified database schema')
    if not {'memory_state','memory_raw_events'}<=managed:
        raise ValueError('not a canonical memory database')
    return managed


def revision(db):
    head=db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",(STATE,)).fetchone()
    if head:
        return db.execute(f'SELECT revision FROM {STATE} WHERE singleton=1').fetchone()[0]
    return INITIAL


def generation_scope(stage, generation):
    if stage not in ('test','prod') or len(generation)!=3:
        raise ValueError('invalid lane generation')
    return sha256(json.dumps(['database-clear-scope-v1',stage,*generation],
                            separators=(',',':')).encode()).hexdigest()


def request_revision(db, *, stage, binding=None):
    generation=revision(db)
    if binding is None:
        return generation
    payload=json.dumps(['memory-clear-revision-v1',stage,binding,generation],separators=(',',':')).encode()
    return sha256(payload).hexdigest()[:32]


def describe(db, *, stage, binding=None):
    rev=request_revision(db,stage=stage,binding=binding)
    try:
        _inventory(db)
        capable=True
    except (ValueError, IndexError):
        capable=False
    result={'stage':stage,'revision':rev,'can_clear':capable}
    if binding is not None:
        result['scope']=generation_scope(stage,(*binding[0],revision(db)))
    return result


def _fingerprint(salt, kind, values):
    return hmac.digest(salt,json.dumps([kind,*values],ensure_ascii=True,separators=(',',':')).encode('ascii'),sha256)


def was_cleared(db,kind,*values):
    if revision(db)==INITIAL:
        return False
    secret=db.execute(f'SELECT salt FROM {STATE} WHERE singleton=1').fetchone()[0]
    return db.execute(f'SELECT 1 FROM {TOMBSTONES} WHERE digest=?',
                      (_fingerprint(secret,kind,values),)).fetchone() is not None


def deny_replay(db,kind,*values):
    if was_cleared(db,kind,*values):
        raise ValueError('message belongs to a cleared memory generation')


def clear(db, *, stage, expected_revision, binding=None):
    if stage not in ('test','prod') or not db.in_transaction:
        raise ValueError('reset requires an owner transaction')
    if expected_revision!=request_revision(db,stage=stage,binding=binding):
        raise Conflict('Database clear revision changed')
    managed=_inventory(db)
    db.execute(f'CREATE TABLE IF NOT EXISTS {STATE}(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision TEXT NOT NULL,salt BLOB NOT NULL)')
    db.execute(f'CREATE TABLE IF NOT EXISTS {TOMBSTONES}(digest BLOB PRIMARY KEY) WITHOUT ROWID')
    row=db.execute(f'SELECT salt FROM {STATE} WHERE singleton=1').fetchone()
    salt=row[0] if row else os.urandom(32)
    for kind,(table,columns) in IDENTITIES.items():
        if table in managed:
            db.executemany(f'INSERT OR IGNORE INTO {TOMBSTONES} VALUES(?)',
                ((_fingerprint(salt,kind,tuple(v)),) for v in db.execute('SELECT '+','.join(columns)+' FROM '+table)))
    db.execute('PRAGMA defer_foreign_keys=ON')
    triggers=[(name,sql) for name,sql in db.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'") if name in IMMUTABLE_DELETE]
    for name,_ in triggers:
        db.execute('DROP TRIGGER '+name)
    # Delete membership/state first because the active-batch raw trigger is
    # intentionally independent of foreign-key deferral.
    data=managed & DATA
    for name in ('memory_capture_batch_members','memory_capture_batches'):
        if name in data:
            db.execute('DELETE FROM '+name)
            data.remove(name)
    for name in sorted(data):
        db.execute('DELETE FROM '+name)
    if 'memory_fts' in managed:
        db.execute("INSERT INTO memory_fts(memory_fts) VALUES ('delete-all')")
    if 'memory_thought_fts' in managed:
        db.execute('DELETE FROM memory_thought_fts')
        db.execute("INSERT INTO memory_thought_fts(memory_thought_fts) VALUES ('rebuild')")
    for _,sql in triggers:
        db.execute(sql)
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('invalid foreign keys after clear')
    db.execute('UPDATE memory_state SET version=version+1 WHERE singleton=1')
    new=uuid4().hex
    db.execute(f'INSERT INTO {STATE} VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision',(new,salt))
    return {'stage':stage,'revision':request_revision(db,stage=stage,binding=binding),'cleared':True}
