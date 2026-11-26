"""Owner cancellation of non-running queue items with bounded dependency checks."""
import hashlib
import json
import re

from .capture_batch import active_member
from .processing_control import Conflict
from .queue_session_control import _exists

PATH='/api/v1/queue-items/remove'
PREVIEW_PATH='/api/v1/queue-items/source-preview'
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}\Z')
SOURCE=('pending','waiting_model','waiting_origin')
MAINTENANCE=('pending',)
QUEUES=('source','maintenance')
ROLES={'user','assistant','system','developer','tool'}


def preview_revision(row,item_id,*,active_batch=False):
    if (row is None or (row[0] not in (*SOURCE,'running') and
            not (active_batch and row[0]=='completed' and row[3]==item_id)) or not row[3]):
        return None
    wire=json.dumps(['test-queue-preview-v1',item_id,*row],separators=(',',':')).encode()
    return hashlib.sha256(wire).hexdigest()[:32]


def _state(db,queue_id,item_id):
    if queue_id=='source' and _exists(db,'memory_capture_jobs'):
        return db.execute('SELECT status,lease_token,lease_until,event_id,phase,attempts,updated_at,available_at '
                          'FROM memory_capture_jobs WHERE id=?',(item_id,)).fetchone()
    if queue_id=='maintenance' and _exists(db,'memory_thought_work'):
        return db.execute('SELECT status,lease_token,lease_until,desired_generation,completed_generation,'
                          'desired_version,action,attempts,available_at FROM memory_thought_work WHERE thought_id=?',
                          (item_id,)).fetchone()
    return None


def revision(row,queue_id,item_id):
    if (row is None or row[0] not in (SOURCE if queue_id=='source' else MAINTENANCE)
            or row[1] is not None or row[2] is not None):
        return None
    if queue_id=='source' and (row[3]!=item_id or row[4]!='extract' or row[5]!=0):
        return None # assertion-side descendants are not raw-source queue entries
    if queue_id=='maintenance' and (type(row[3]) is not int or row[3]<=row[4]):
        return None
    wire=json.dumps(['test-queue-removal-v1',queue_id,item_id,*row],separators=(',',':')).encode()
    return hashlib.sha256(wire).hexdigest()[:32]


def check_request(queue_id,item_id):
    if queue_id not in QUEUES or type(item_id) is not str or not ID.fullmatch(item_id):
        raise ValueError('Invalid queue item identity')


def annotate(db,data):
    for queue in data['queues']:
        q=queue['id']
        if q not in QUEUES:
            continue
        for item in queue['items']:
            if q=='source' and 'members' in item:
                for member in item['members']:
                    if (preview:=preview_revision(_state(db,q,member['id']),member['id'],active_batch=True)) is not None:
                        member['preview_revision']=preview
                continue
            row=_state(db,q,item['id'])
            if (not (q=='source' and active_member(db,item['id']))
                    and (rev:=revision(row,q,item['id'])) is not None):
                item['remove_revision']=rev
            if q=='source' and (preview:=preview_revision(row,item['id'])) is not None:
                item['preview_revision']=preview


def _first_source_dependency_check(db, item_id):
    dependent = {
        'memory_extraction_runs':'event_id',
        'memory_source_audits':'event_id',
        'memory_source_audit_requests':'event_id',
        'memory_source_generation_events':'event_id',
        'memory_source_draft_checkpoints':'job_id',
        'memory_thought_purge_raw_refs':'event_id',
        'native_occurrence_receipts':'event_id',
    }
    for table,key in dependent.items():
        if _exists(db,table) and db.execute(f'SELECT 1 FROM {table} WHERE {key}=? LIMIT 1',(item_id,)).fetchone():
            raise Conflict('Source has dependent records')
    if _exists(db,'memory_assertions') and db.execute(
            'SELECT 1 FROM memory_assertions WHERE instr(metadata,?)>0 LIMIT 1',(item_id,)).fetchone():
        raise Conflict('Source has published records')
    if _exists(db,'memory_thought_versions'):
        from .thought_payload import decode_payload
        from .thought_purge import _actual_raw_refs
        from .thought_store import Thought
        # Scan entire version history, including inactive versions; never
        # guess when the bounded work exceeds this narrow TEST operation.
        rows=db.execute('SELECT payload FROM memory_thought_versions LIMIT 2001')
        for index,(payload,) in enumerate(rows):
            if index>=2000 or item_id in _actual_raw_refs(Thought(**decode_payload(payload))):
                raise Conflict('Source has retained or unverified thought evidence')
    required={'memory_event_positions','memory_capture_jobs','memory_ingest_receipts',
              'memory_message_receipts','memory_source_context_changes','memory_fresh_mac_events'}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if table in required:
            continue
        # Foreign-key metadata is independently checked for a new owner that
        # this feature does not know how to disassociate.
        for _,_,parent,column,target,*_ in db.execute('SELECT * FROM pragma_foreign_key_list(?)',(table,)):
            if parent not in ('memory_raw_events','memory_capture_jobs'):
                continue
            if target not in ('id',None) or not re.fullmatch(r'[A-Za-z0-9_]+',table) or not re.fullmatch(r'[A-Za-z0-9_]+',column):
                raise Conflict('Unverified source reference schema')
            if db.execute(f'SELECT 1 FROM "{table}" WHERE "{column}"=? LIMIT 1',(item_id,)).fetchone():
                raise Conflict('Source has dependent records')


def source_tombstoned(db,event_id):
    return (_exists(db,'memory_test_queue_removals') and db.execute(
        "SELECT 1 FROM memory_test_queue_removals WHERE queue_id='source' AND item_id=? LIMIT 1",
        (event_id,)).fetchone() is not None)


def remove(db,*,stage,queue_id,item_id,expected_revision,now):
    if stage not in ('test','prod') or not db.in_transaction:
        raise ValueError('Invalid TEST queue transaction')
    check_request(queue_id,item_id)
    if queue_id=='source' and active_member(db,item_id):
        raise Conflict('Active raw batch cannot be removed one item at a time')
    row=_state(db,queue_id,item_id)
    current=revision(row,queue_id,item_id)
    if current is None or current!=expected_revision:
        raise Conflict('Queue item changed or cannot be cancelled')
    db.execute('CREATE TABLE IF NOT EXISTS memory_test_queue_removals '
               '(queue_id TEXT NOT NULL,item_id TEXT NOT NULL,old_revision TEXT NOT NULL,'
               'generation_before INTEGER,generation_through INTEGER,cancelled_at TEXT NOT NULL,'
               'PRIMARY KEY(queue_id,item_id,old_revision))')
    low,hi=(row[4],row[3]) if queue_id=='maintenance' else (None,None)
    db.execute('INSERT INTO memory_test_queue_removals VALUES (?,?,?,?,?,?)',
               (queue_id,item_id,current,low,hi,now))
    if queue_id=='source':
        _first_source_dependency_check(db,item_id)
        if _exists(db,'memory_ingest_receipts'):
            from .ingest_streams import retire_delivery_receipts
            retire_delivery_receipts(db,item_id,f'{stage}-owner-removed-before-first-processing-v1')
        for table in ('memory_event_positions','memory_message_receipts','memory_source_context_changes','memory_fresh_mac_events'):
            if _exists(db,table):
                db.execute(f'DELETE FROM {table} WHERE event_id=?',(item_id,))
        result=db.execute("DELETE FROM memory_capture_jobs WHERE id=? AND event_id=? AND status=? "
                          "AND phase='extract' AND attempts=0 AND lease_token IS NULL AND lease_until IS NULL",
                          (item_id,item_id,row[0]))
        if result.rowcount!=1 or db.execute('DELETE FROM memory_raw_events WHERE id=?',(item_id,)).rowcount!=1:
            raise Conflict('Source changed')
    else:
        # Advance the examined watermark so past causes don't become new work.
        # Leave completed_version unchanged: no model work was performed.
        result=db.execute("UPDATE memory_thought_work SET status='cancelled',completed_generation=? "
                          "WHERE thought_id=? AND status='pending' AND desired_generation=? "
                          "AND completed_generation=? AND lease_token IS NULL AND lease_until IS NULL",
                          (hi,item_id,hi,low))
    if result.rowcount!=1:
        raise Conflict('Queue item changed')
    return dict(stage=stage,queue_id=queue_id,item_id=item_id,removed=True,
                effect='source_and_queue' if queue_id=='source' else 'queue_only')


def was_removed(db, thought_id, generation):
    if not _exists(db,'memory_test_queue_removals'):
        return False
    return db.execute("SELECT 1 FROM memory_test_queue_removals WHERE queue_id='maintenance' "
                      'AND item_id=? AND generation_before<? AND generation_through>=? LIMIT 1',
                      (thought_id,generation,generation)).fetchone() is not None


def source_preview(db,*,stage,queue_id,item_id,expected_revision):
    if stage not in ('test','prod') or queue_id!='source':
        raise ValueError('Invalid source preview route')
    check_request(queue_id,item_id)
    row=_state(db,queue_id,item_id)
    current=preview_revision(row,item_id,active_batch=(row is not None and row[0]=='completed' and active_member(db,item_id)))
    if current is None or current!=expected_revision:
        raise Conflict('Queue item changed')
    source=db.execute("SELECT substr(prompt,1,240),CASE WHEN json_valid(metadata) THEN "
                      "json_extract(metadata,'$.role') END FROM memory_raw_events WHERE id=?",
                      (row[3],)).fetchone()
    if source is None:
        raise Conflict('Queue item changed')
    prompt,role=source
    if not isinstance(prompt,str):
        raise ValueError('Invalid source preview')
    prompt=''.join(c if c.isprintable() or c in '\n\t' else ' ' for c in prompt)
    return dict(stage=stage,queue_id='source',item_id=item_id,
                source_preview=prompt,source_role=role if isinstance(role,str) and role in ROLES else None)
