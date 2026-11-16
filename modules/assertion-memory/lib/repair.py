"""Explicit known-session parser replay; never edits canonical messages or receipts."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import uuid
from datetime import datetime,timezone
from .native_capture import _header

REASON='service_context_structured_tools_and_visible_assistant_phase_v2'


def reset_parser_checkpoint(store,session_id,*,expected_header_sha256,reason):
    if reason!=REASON:raise ValueError('An explicit supported parser repair reason is required')
    state=store.session(session_id)
    if state is None:raise ValueError('Only an explicitly registered physical session may be replayed')
    if state.get('registration_source')!='startup' or state.get('fork_requires_current_session_proof') or state.get('blocked'):
        raise ValueError('This attachment boundary requires separate review; refusing history expansion')
    db_path=Path(next(row[2] for row in store.db.execute('PRAGMA database_list') if row[1]=='main'))
    with (db_path.parent/'worker.lock').open('a+') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Stop the existing outbox worker before explicit parser repair') from None
        with store.db:
            state=store.session(session_id)
            with Path(state['path']).open('rb') as source:
                header,_=_header(source,{'session_id':session_id,'cwd':state['cwd']})
                stat=os.fstat(source.fileno());digest=hashlib.sha256(header).hexdigest()
                if (digest!=expected_header_sha256 or digest!=state.get('header_hash')
                        or [stat.st_dev,stat.st_ino]!=state.get('identity') or stat.st_size<state['cursor']):
                    raise ValueError('Registered source identity/header changed; refusing repair')
                for witness in state.get('witnesses',[])+([state['checkpoint']] if state.get('checkpoint') else []):
                    source.seek(witness['start']);data=source.read(witness['end']-witness['start'])
                    if hashlib.sha256(data).hexdigest()!=witness['sha256']:raise ValueError('Retained source proof changed; refusing repair')
            store.db.execute('CREATE TABLE IF NOT EXISTS parser_repairs(repair_id TEXT PRIMARY KEY,session_id TEXT,metadata TEXT NOT NULL)')
            repair_id=str(uuid.uuid4())
            metadata={'reason':reason,'old_cursor':state['cursor'],'old_gap_count':len(state.get('gaps',[])),
                'header_sha256':digest,'requested_at':datetime.now(timezone.utc).isoformat(),
                'canonical_messages_preserved':store.db.execute('SELECT COUNT(*) FROM messages WHERE session_id=?',(session_id,)).fetchone()[0]}
            store.db.execute('INSERT INTO parser_repairs VALUES (?,?,?)',(repair_id,session_id,json.dumps(metadata,sort_keys=True)))
            state.update(cursor=0,line=0,current=None,turn=None,gaps=[],witnesses=[],checkpoint=None,
                skip_partial=False,exclusions={},parser_policy_revision=REASON)
            state['parser_repair_ids']=(state.get('parser_repair_ids',[])+[repair_id])[-8:]
            store.save_session(state)
        return {'repair_id':repair_id,'session_id':session_id,'status':'replay_pending',**metadata}


BOUNDED_REASON='validated_snapshot_media_and_visible_phase_v3'


def _file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        while True:
            block=f.read(65536)
            if not block:break
            digest.update(block)
    return digest.hexdigest()


def replay_from_snapshot(store,session_id,*,snapshot_path,expected_snapshot_sha256,
                         expected_snapshot_cursor,expected_header_sha256,reason):
    """Continue one registered source from a caller-pinned historical checkpoint.

    This restores only parser state, never the backup's queue/graph/receipts. A
    prior unresolved gap remains unresolved. Existing resume/fork history before
    the validated checkpoint is outside this replay's authority.
    """
    import sqlite3
    from urllib.parse import quote
    if reason!=BOUNDED_REASON:raise ValueError('An explicit supported bounded repair reason is required')
    if type(expected_snapshot_cursor) is not int or expected_snapshot_cursor<=0:
        raise ValueError('A positive explicit checkpoint is required; refusing history expansion')
    snapshot_path=Path(snapshot_path).resolve()
    if _file_sha256(snapshot_path)!=expected_snapshot_sha256:raise ValueError('Snapshot hash differs; refusing replay')
    snapshot=sqlite3.connect('file:'+quote(str(snapshot_path),safe='/')+'?mode=ro&immutable=1',uri=True)
    try:
        row=snapshot.execute('SELECT data FROM sessions WHERE session_id=?',(session_id,)).fetchone()
        if row is None:raise ValueError('The exact session is absent from the pinned snapshot')
        saved=json.loads(row[0])
    finally:snapshot.close()
    if saved.get('cursor')!=expected_snapshot_cursor:raise ValueError('Snapshot checkpoint differs; refusing replay')
    if saved.get('session_id')!=session_id or saved.get('blocked'):raise ValueError('Snapshot source is not eligible for bounded continuation')
    db_path=Path(next(row[2] for row in store.db.execute('PRAGMA database_list') if row[1]=='main'))
    with (db_path.parent/'worker.lock').open('a+') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Stop the existing outbox worker before bounded replay') from None
        with store.db:
            current=store.session(session_id)
            if current is None or current.get('blocked'):raise ValueError('Only the exact registered source may be continued')
            if any(current.get(key)!=saved.get(key) for key in ('path','cwd','header_hash','identity','registration_source')):
                raise ValueError('Current and saved physical source identity differ')
            if expected_snapshot_cursor>current['cursor']:raise ValueError('Snapshot is ahead of the registered parser')
            with Path(current['path']).open('rb') as source:
                header,_=_header(source,{'session_id':session_id,'cwd':current['cwd']})
                stat=os.fstat(source.fileno());digest=hashlib.sha256(header).hexdigest()
                if (digest!=expected_header_sha256 or digest!=current.get('header_hash')
                        or [stat.st_dev,stat.st_ino]!=current.get('identity') or stat.st_size<current['cursor']):
                    raise ValueError('Registered source identity/header changed')
                saved_rows=[]
                for value in (saved,current):
                    witnesses=value.get('witnesses',[])+([value['checkpoint']] if value.get('checkpoint') else [])
                    for witness in witnesses:
                        if witness['start']<0 or witness['end']<witness['start'] or witness['end']>stat.st_size:
                            raise ValueError('Source proof range invalid')
                        source.seek(witness['start']);data=source.read(witness['end']-witness['start'])
                        if hashlib.sha256(data).hexdigest()!=witness['sha256']:raise ValueError('Retained checkpoint proof changed')
                        if value is saved and witness in saved.get('witnesses',[]):
                            try:saved_rows.append(json.loads(data))
                            except ValueError:raise ValueError('Saved protocol witness is invalid') from None
                if not saved.get('checkpoint'):raise ValueError('A retained saved checkpoint is required')
                if saved.get('fork_requires_current_session_proof') and (saved.get('turn') or{}).get('verified'):
                    from .native_capture import _candidate
                    turn=(saved.get('turn') or{})['id'];candidate_r=None;paired=False
                    for record in saved_rows:
                        candidate=_candidate(record,session_id,turn,human=False)
                        if not candidate:continue
                        if candidate[0]=='R':candidate_r=candidate
                        elif candidate_r and candidate_r[1]==candidate[1]:paired=True
                    if not paired:raise ValueError('Saved fork state lacks exact current-session occurrence proof')
            preserved=store.db.execute('SELECT COUNT(*) FROM messages WHERE session_id=?',(session_id,)).fetchone()[0]
            repair_id=str(uuid.uuid4());metadata={'reason':reason,'snapshot_sha256':expected_snapshot_sha256,
                'replay_start_offset':expected_snapshot_cursor,'previous_cursor':current['cursor'],
                'retained_prior_gap_count':len(saved.get('gaps',[])),'header_sha256':digest,
                'canonical_messages_preserved':preserved,'requested_at':datetime.now(timezone.utc).isoformat()}
            store.db.execute('CREATE TABLE IF NOT EXISTS parser_repairs(repair_id TEXT PRIMARY KEY,session_id TEXT,metadata TEXT NOT NULL)')
            store.db.execute('INSERT INTO parser_repairs VALUES (?,?,?)',(repair_id,session_id,json.dumps(metadata,sort_keys=True)))
            saved['parser_policy_revision']=reason;saved['replay_start_offset']=expected_snapshot_cursor
            saved['fork_requires_current_session_proof']=bool(current.get('fork_requires_current_session_proof') or saved.get('fork_requires_current_session_proof'))
            saved['parser_repair_ids']=(current.get('parser_repair_ids',[])+[repair_id])[-8:]
            store.save_session(saved)
        return {'repair_id':repair_id,'session_id':session_id,'status':'bounded_replay_pending',**metadata}
