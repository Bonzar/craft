"""Explicit bounded post-migration storage purge, while background work is paused.

Only provenance-excluded Codex material and technically rejected legacy versions
qualify. A retained version/ground always wins over deletion. No semantic model
runs; canonical useful payloads/hashes are never rewritten for compaction.
"""
from __future__ import annotations
import json
import sqlite3
from collections import Counter
from .thought_store import Thought
try:
    from .thought_payload import decode_payload
except ImportError:
    decode_payload = json.loads  # Compatibility with an uncompressed pre-cutover store.
from .thought_source_policy import event_eligibility, thought_eligibility

_PURGE = 'post-migration-storage-v1'
_BAD = {'empty_content', 'failed_review', 'service_scope_root', 'no_legacy_bases'}
_RAW_OWNERS = ('memory_event_positions', 'memory_extraction_runs', 'memory_capture_jobs',
    'memory_message_receipts', 'memory_ingest_receipts', 'native_occurrence_receipts',
    'memory_source_audits', 'memory_source_audit_requests', 'memory_source_context_changes',
    'memory_source_generation_events')


def _exists(c, table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _rows(c, sql, args=()):
    return c.execute(sql, args).fetchall()


def _bump(counts, name, count):
    counts[name] += max(0, count)


def _reason(thoughts, thought, c, memo):
    if not thought_eligibility(thoughts, thought, memo=memo)['allowed']:
        return 'excluded_source_policy'
    if not thought.text.strip():
        return 'empty_content'
    legacy = thought.metadata.get('legacy', {})
    if not legacy:
        return None
    if _exists(c, 'memory_thought_legacy'):
        row = c.execute('SELECT exclusion FROM memory_thought_legacy WHERE thought_id=? AND thought_version=?',
            (thought.id, thought.version)).fetchone()
        if row and row[0] in _BAD:
            return row[0]
    original = legacy.get('original', {})
    if isinstance(original, dict) and isinstance(original.get('snapshot'), str):
        review = json.loads(original['snapshot']).get('overview_review') or {}
        if review.get('status') in ('failed', 'unresolved', 'rejected', 'revise'):
            return 'failed_review'
    return None


def _init(c):
    if _exists(c, 'memory_thought_migration') and c.execute(
        'SELECT 1 FROM memory_thought_migration WHERE cursor<end_cursor LIMIT 1').fetchone():
        raise ValueError('finish and validate migration before purging its source copies')
    needs_index = not _exists(c, 'memory_thought_purge_refs')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_purge_state '
        '(policy TEXT PRIMARY KEY,phase TEXT NOT NULL,cursor INTEGER NOT NULL,end_cursor INTEGER NOT NULL,version_end INTEGER NOT NULL DEFAULT 0)')
    if 'version_end' not in {row[1] for row in c.execute('PRAGMA table_info(memory_thought_purge_state)')}:
        c.execute('ALTER TABLE memory_thought_purge_state ADD COLUMN version_end INTEGER NOT NULL DEFAULT 0')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_purge_candidates '
        '(thought_id TEXT NOT NULL,version INTEGER NOT NULL,reason TEXT NOT NULL,blocked TEXT,PRIMARY KEY(thought_id,version))')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_purge_blockers '
        '(kind TEXT NOT NULL,owner TEXT NOT NULL,detail TEXT NOT NULL,PRIMARY KEY(kind,owner))')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_purge_refs '
        '(owner_id TEXT NOT NULL,owner_version INTEGER NOT NULL,target_id TEXT NOT NULL,target_version INTEGER NOT NULL,'
        'PRIMARY KEY(owner_id,owner_version,target_id,target_version))')
    c.execute('CREATE INDEX IF NOT EXISTS memory_thought_purge_target ON memory_thought_purge_refs(target_id,target_version)')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_purge_raw_refs '
        '(owner_id TEXT NOT NULL,owner_version INTEGER NOT NULL,event_id TEXT NOT NULL,PRIMARY KEY(owner_id,owner_version,event_id))')
    c.execute('CREATE INDEX IF NOT EXISTS memory_thought_purge_event ON memory_thought_purge_raw_refs(event_id)')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_purge_legacy_refs '
        '(assertion_id TEXT NOT NULL,version_id INTEGER NOT NULL,PRIMARY KEY(assertion_id,version_id))')
    lookup_columns = [('memory_thought_legacy','thought_id,thought_version'),
        ('memory_assertions',"json_extract(metadata,'$.event_id')"),
        ('memory_view_checks','version_id'),('memory_historical_checks','version_id'),
        ('memory_view_names','version_id'),('memory_view_names','source_check_id'),
        ('memory_run_events','version_id'),('memory_run_events','check_id')]
    lookup_columns.extend((table,'event_id') for table in _RAW_OWNERS)
    for number,(table,columns) in enumerate(lookup_columns):
        if _exists(c,table):
            c.execute('CREATE INDEX IF NOT EXISTS memory_thought_purge_lookup_' + str(number) + ' ON ' + table + '(' + columns + ')')
    end = c.execute('SELECT coalesce(max(rowid),0) FROM memory_thought_versions').fetchone()[0]
    inserted = c.execute("INSERT OR IGNORE INTO memory_thought_purge_state VALUES(?,'classify',0,?,?)", (_PURGE, end, end)).rowcount
    if inserted or needs_index:
        for table in ('memory_thought_purge_refs','memory_thought_purge_raw_refs','memory_thought_purge_legacy_refs','memory_thought_purge_blockers'):
            c.execute('DELETE FROM ' + table)
        c.execute('UPDATE memory_thought_purge_candidates SET blocked=NULL')
        c.execute("UPDATE memory_thought_purge_state SET phase='classify',cursor=0,end_cursor=?,version_end=? WHERE policy=?", (end,end,_PURGE))
    return dict(c.execute('SELECT * FROM memory_thought_purge_state WHERE policy=?', (_PURGE,)).fetchone())


def _index_version(c, thought):
    c.executemany('INSERT OR IGNORE INTO memory_thought_purge_refs VALUES(?,?,?,?)',
        [(thought.id,thought.version,basis['thought_id'],basis['version']) for basis in thought.bases if 'thought_id' in basis])
    c.executemany('INSERT OR IGNORE INTO memory_thought_purge_raw_refs VALUES(?,?,?)',
        [(thought.id,thought.version,event_id) for event_id in _actual_raw_refs(thought)])


def _tail(c):
    end = c.execute('SELECT version_end FROM memory_thought_purge_state WHERE policy=?', (_PURGE,)).fetchone()[0]
    return c.execute('SELECT payload FROM memory_thought_versions WHERE rowid>?', (end,))


def _block(c, kind, owner, detail):
    c.execute('INSERT OR REPLACE INTO memory_thought_purge_blockers VALUES(?,?,?)', (kind, str(owner), json.dumps(detail, sort_keys=True)))


def _set_phase(c, state, phase, table=None):
    state.update(phase=phase, cursor=0, end_cursor=c.execute('SELECT coalesce(max(rowid),0) FROM ' + table).fetchone()[0]
                 if table and _exists(c, table) else 0)


def _incoming(c, thought_id, version):
    result = [dict(row) for row in c.execute('SELECT owner_id AS thought_id,owner_version AS version '
        'FROM memory_thought_purge_refs WHERE target_id=? AND target_version=? LIMIT 20', (thought_id,version))]
    for row in _tail(c):
        owner = decode_payload(row[0])
        if any(basis.get('thought_id') == thought_id and basis.get('version') == version for basis in owner['bases']):
            result.append({'thought_id':owner['id'],'version':owner['version']})
    return result


def _delete_failed_run_events(c, version_id, counts):
    if not _exists(c,'memory_run_events'):
        return
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='memory_run_events' "
        "AND name='memory_run_events_no_delete'").fetchone()
    original = row[0] if row else None
    if original and ('before delete' not in original.casefold() or 'execution provenance is append-only' not in original):
        raise ValueError('unexpected legacy append-only trigger definition; retained for inspection')
    if original:
        c.execute('DROP TRIGGER memory_run_events_no_delete')
    try:
        _bump(counts,'legacy_run_events',c.execute('DELETE FROM memory_run_events WHERE version_id=? OR check_id IN '
            '(SELECT id FROM memory_view_checks WHERE version_id=?)',(version_id,version_id)).rowcount)
    finally:
        if original:
            c.execute(original)


def _legacy_view_is_disposable(c, version_id):
    row = c.execute('SELECT snapshot FROM memory_view_versions WHERE id=?',(version_id,)).fetchone()
    if row is None:
        return True
    snapshot = json.loads(row[0])
    review = snapshot.get('overview_review') or {}
    if review.get('status') in ('failed','unresolved','rejected','revise'):
        return True
    if any(not event_eligibility(item)['allowed'] for item in snapshot.get('evidence',[]) if isinstance(item,dict)):
        return True
    mappings = _rows(c,"SELECT thought_id,thought_version,exclusion FROM memory_thought_legacy WHERE source_table='memory_view_versions' AND source_key=?",(str(version_id),))
    for mapping in mappings:
        if mapping['exclusion'] in _BAD | {'unresolved_legacy_bases','excluded_source_policy'}:
            return True
        if c.execute('SELECT 1 FROM memory_thought_versions WHERE thought_id=? AND version=?',
                (mapping['thought_id'],mapping['thought_version'])).fetchone():
            return True
    return False


def purge_legacy_view_owners(thoughts, version_ids):
    """Explicit repair of already-discarded legacy versions left by an older pass."""
    ids = list(dict.fromkeys(version_ids))
    if len(ids)>100 or any(type(key) is not int or key<1 for key in ids):
        raise ValueError('supply at most 100 positive legacy version IDs')
    counts, blockers = Counter(), []
    with thoughts.store.transaction(write=True) as c:
        _init(c)
        for key in ids:
            delta = Counter()
            try:
                with thoughts.store.transaction(write=True):
                    if not _legacy_view_is_disposable(c,key):
                        raise ValueError('accepted legacy view has no preserved canonical history')
                    _delete_legacy_view(c,key,delta)
                    c.execute("DELETE FROM memory_thought_purge_blockers WHERE kind='legacy_owner_retained' AND owner=?",('memory_view_versions:'+str(key),))
                counts.update(delta)
            except (sqlite3.DatabaseError,ValueError) as error:
                blockers.append({'version_id':key,'reason':str(error)})
    return {'counts':dict(counts),'blockers':blockers}


def _delete_legacy_view(c, version_id, counts):
    _delete_failed_run_events(c,version_id,counts)
    for table, column in (('memory_view_names', 'version_id'), ('memory_historical_checks', 'version_id'),
                          ('memory_view_checks', 'version_id')):
        if _exists(c, table):
            triggers = []
            if table == 'memory_view_names':
                triggers = _rows(c,"SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN "
                    "('memory_view_names_no_delete','memory_view_names_no_update')")
                for trigger in triggers:c.execute('DROP TRIGGER '+trigger['name'])
            try:
                if table == 'memory_view_names':
                    c.execute('UPDATE memory_view_names SET previous_name_id=NULL WHERE previous_name_id IN '
                              '(SELECT id FROM memory_view_names WHERE version_id=?)',(version_id,))
                _bump(counts, 'legacy_owner_rows', c.execute('DELETE FROM ' + table + ' WHERE ' + column + '=?', (version_id,)).rowcount)
            finally:
                for trigger in triggers:c.execute(trigger['sql'])
    _bump(counts, 'legacy_view_versions', c.execute('DELETE FROM memory_view_versions WHERE id=?', (version_id,)).rowcount)
    c.execute('DELETE FROM memory_thought_purge_legacy_refs WHERE version_id=?', (version_id,))
    if _exists(c, 'memory_views'):
        c.execute('UPDATE memory_views SET current_id=NULL WHERE current_id=?', (version_id,))


def _delete_legacy_assertion(c, assertion_id, counts):
    row = c.execute('SELECT version_id FROM memory_thought_purge_legacy_refs WHERE assertion_id=? LIMIT 1', (assertion_id,)).fetchone()
    if row:
        raise ValueError('legacy view still needs assertion: ' + str(row[0]))
    for table, clause in (('memory_edges', 'from_id=? OR to_id=?'), ('memory_capture_jobs', 'assertion_id=?'),
                          ('memory_branch_reviews', 'assertion_id=?'), ('memory_policy_admissions', 'assertion_id=?')):
        if _exists(c, table):
            args = (assertion_id, assertion_id) if table == 'memory_edges' else (assertion_id,)
            _bump(counts, 'legacy_owner_rows', c.execute('DELETE FROM ' + table + ' WHERE ' + clause, args).rowcount)
    _bump(counts, 'legacy_assertions', c.execute('DELETE FROM memory_assertions WHERE assertion_id=?', (assertion_id,)).rowcount)


def _delete_version(thoughts, c, candidate, counts):
    key, version = candidate['thought_id'], candidate['version']
    incoming = _incoming(c, key, version)
    if incoming:
        return False
    mappings = _rows(c, 'SELECT * FROM memory_thought_legacy WHERE thought_id=? AND thought_version=?', (key, version)) if _exists(c, 'memory_thought_legacy') else []
    for mapping in mappings:
        delta = Counter()
        try:
            with thoughts.store.transaction(write=True):
                if mapping['source_table'] == 'memory_view_versions' and _exists(c, 'memory_view_versions'):
                    _delete_legacy_view(c, int(mapping['source_key']), delta)
                elif mapping['source_table'] == 'memory_assertions':
                    _delete_legacy_assertion(c, mapping['source_key'], delta)
            counts.update(delta)
        except (sqlite3.DatabaseError, ValueError) as error:
            _block(c, 'legacy_owner_retained', mapping['source_table'] + ':' + mapping['source_key'], str(error))
    _bump(counts, 'links', c.execute('DELETE FROM memory_thought_links WHERE '
        '(source_id=? AND source_version=?) OR (target_id=? AND target_version=?)', (key, version, key, version)).rowcount)
    active = c.execute('DELETE FROM memory_thoughts WHERE id=? AND current_version=?', (key, version)).rowcount
    if active:
        c.execute('DELETE FROM memory_thought_fts WHERE thought_id=?', (key,))
        c.execute('DELETE FROM memory_thought_dependencies WHERE dependent_id=?', (key,))
    _bump(counts, 'semantic_versions', c.execute('DELETE FROM memory_thought_versions WHERE thought_id=? AND version=?', (key, version)).rowcount)
    c.execute('DELETE FROM memory_thought_purge_refs WHERE owner_id=? AND owner_version=?', (key,version))
    c.execute('DELETE FROM memory_thought_purge_raw_refs WHERE owner_id=? AND owner_version=?', (key,version))
    c.execute('DELETE FROM memory_thought_purge_candidates WHERE thought_id=? AND version=?', (key, version))
    return True


def _actual_raw_refs(thought):
    refs = {basis['event_id'] for basis in thought.bases if 'event_id' in basis}
    if thought.origin.get('event_id'):
        refs.add(thought.origin['event_id'])
    original = thought.metadata.get('legacy', {}).get('original', {})
    records = [original] if isinstance(original, dict) else []
    if isinstance(original, dict) and isinstance(original.get('snapshot'), str):
        records += json.loads(original['snapshot']).get('evidence', [])
    for record in records:
        metadata = record.get('metadata', {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if metadata.get('event_id'):
            refs.add(metadata['event_id'])
        grounds = [*metadata.get('evidence_refs', []), *metadata.get('temporal_evidence', [])]
        if isinstance(metadata.get('source_span'), dict):
            grounds.append(metadata['source_span'])
        refs.update(row['event_id'] for row in grounds if isinstance(row, dict) and row.get('event_id'))
    return refs


def _delete_raw(thoughts, c, raw, counts):
    if event_eligibility(dict(raw))['allowed']:
        return
    event_id = raw['id']
    referenced = [dict(row) for row in c.execute('SELECT owner_id AS thought_id,owner_version AS version '
        'FROM memory_thought_purge_raw_refs WHERE event_id=? LIMIT 20', (event_id,))]
    for row in _tail(c):
        thought = Thought(**decode_payload(row[0]))
        if event_id in _actual_raw_refs(thought):
            referenced.append({'thought_id':thought.id,'version':thought.version})
    if referenced:
        _block(c, 'raw_ground_retained', event_id, referenced)
        return
    delta = Counter()
    try:
        with thoughts.store.transaction(write=True):
            for row in _rows(c, "SELECT assertion_id FROM memory_assertions WHERE json_extract(metadata,'$.event_id')=?", (event_id,)):
                _delete_legacy_assertion(c, row[0], delta)
            if _exists(c,'memory_ingest_receipts'):
                from .ingest_streams import retire_delivery_receipts
                _bump(delta,'delivery_receipts_retired',retire_delivery_receipts(c,event_id,'codex-visible-dialogue-only-v1'))
            for table in _RAW_OWNERS:
                if _exists(c, table):
                    _bump(delta, 'raw_owner_rows', c.execute('DELETE FROM ' + table + ' WHERE event_id=?', (event_id,)).rowcount)
            _bump(delta, 'raw_events', c.execute('DELETE FROM memory_raw_events WHERE id=?', (event_id,)).rowcount)
        counts.update(delta)
    except (sqlite3.DatabaseError, ValueError) as error:
        _block(c, 'raw_owner_retained', event_id, str(error))


def _compact_receipt(c, row, counts):
    table = row['source_table']
    columns = {'memory_assertions': 'assertion_id', 'memory_views': 'id', 'memory_view_versions': 'id', 'memory_edges': 'edge_id'}
    if table not in columns or not _exists(c, table):
        return
    original = c.execute('SELECT 1 FROM ' + table + ' WHERE ' + columns[table] + '=?', (row['source_key'],)).fetchone()
    canonical = c.execute('SELECT 1 FROM memory_thought_versions WHERE thought_id=? AND version=?',
        (row['thought_id'], row['thought_version'])).fetchone() if row['thought_id'] else None
    if original:
        payload = json.dumps({'compacted': True, 'source_table': table, 'source_key': row['source_key'],
            'thought_id': row['thought_id'], 'version': row['thought_version']}, sort_keys=True)
        if row['payload'] != payload:
            c.execute('UPDATE memory_thought_legacy SET payload=? WHERE source_table=? AND source_key=?',
                (payload, table, row['source_key']))
            _bump(counts, 'legacy_payloads_compacted', 1)
    elif not canonical and not original:
        c.execute('DELETE FROM memory_thought_legacy WHERE source_table=? AND source_key=?', (table, row['source_key']))
        _bump(counts, 'legacy_receipts_removed', 1)


def purge_batch(thoughts, *, limit=100, retry_blockers=False):
    """One bounded pass; complete plus nonempty blockers is NOT a clean checkpoint."""
    if type(limit) is not int or limit < 1:
        raise ValueError('purge limit must be positive')
    counts, processed, memo = Counter(), 0, {}
    with thoughts.store.transaction(write=True) as c:
        state = _init(c)
        if retry_blockers and state['phase'] == 'done':
            c.execute('DELETE FROM memory_thought_purge_state WHERE policy=?', (_PURGE,))
            c.execute('DELETE FROM memory_thought_purge_blockers')
            c.execute('UPDATE memory_thought_purge_candidates SET blocked=NULL')
            state = _init(c)
        while processed < limit and state['phase'] != 'done':
            phase = state['phase']
            remaining = limit - processed
            if phase == 'classify':
                rows = _rows(c, 'SELECT rowid,payload FROM memory_thought_versions WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                    (state['cursor'], state['end_cursor'], remaining))
                for row in rows:
                    thought = Thought(**decode_payload(row['payload']))
                    _index_version(c, thought)
                    reason = _reason(thoughts, thought, c, memo)
                    if reason:
                        c.execute('INSERT OR IGNORE INTO memory_thought_purge_candidates VALUES(?,?,?,NULL)', (thought.id, thought.version, reason))
                    state['cursor'] = row['rowid']; processed += 1
                if len(rows) < remaining:
                    _set_phase(c, state, 'legacy_refs', 'memory_view_versions')
            elif phase == 'legacy_refs':
                rows = _rows(c, 'SELECT rowid AS purge_rowid,id,snapshot FROM memory_view_versions WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                    (state['cursor'],state['end_cursor'],remaining)) if _exists(c,'memory_view_versions') else []
                for row in rows:
                    evidence = json.loads(row['snapshot']).get('evidence',[])
                    ids = {item.get('id',item.get('assertion_id')) for item in evidence}
                    c.executemany('INSERT OR IGNORE INTO memory_thought_purge_legacy_refs VALUES(?,?)',
                        [(key,row['id']) for key in ids if isinstance(key,str)])
                    state['cursor'] = row['purge_rowid']; processed += 1
                if len(rows) < remaining:
                    _set_phase(c,state,'versions')
            elif phase == 'versions':
                candidates = _rows(c, "SELECT p.* FROM memory_thought_purge_candidates p WHERE p.blocked IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM memory_thought_purge_refs r WHERE r.target_id=p.thought_id AND r.target_version=p.version) LIMIT ?", (remaining,))
                if not candidates:
                    candidates = _rows(c, 'SELECT * FROM memory_thought_purge_candidates WHERE blocked IS NULL LIMIT ?', (remaining,))
                for candidate in candidates:
                    if not _delete_version(thoughts, c, candidate, counts):
                        incoming = _incoming(c, candidate['thought_id'], candidate['version'])
                        owner = candidate['thought_id'] + ':' + str(candidate['version'])
                        _block(c, 'semantic_basis_retained', owner, incoming)
                        c.execute('UPDATE memory_thought_purge_candidates SET blocked=? WHERE thought_id=? AND version=?',
                            ('remaining version references', candidate['thought_id'], candidate['version']))
                    processed += 1
                if not candidates:
                    _set_phase(c, state, 'raw', 'memory_raw_events')
            elif phase == 'raw':
                rows = _rows(c, 'SELECT rowid,id,actor,source,metadata FROM memory_raw_events WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                    (state['cursor'], state['end_cursor'], remaining)) if _exists(c, 'memory_raw_events') else []
                for row in rows:
                    _delete_raw(thoughts, c, row, counts)
                    state['cursor'] = row['rowid']; processed += 1
                if len(rows) < remaining:
                    _set_phase(c, state, 'receipts', 'memory_thought_legacy')
            elif phase == 'receipts':
                rows = _rows(c, 'SELECT rowid,* FROM memory_thought_legacy WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                    (state['cursor'], state['end_cursor'], remaining)) if _exists(c, 'memory_thought_legacy') else []
                for row in rows:
                    _compact_receipt(c, row, counts)
                    state['cursor'] = row['rowid']; processed += 1
                if len(rows) < remaining:
                    _set_phase(c, state, 'cache', 'memory_refresh_generations')
            else:
                rows = _rows(c, 'SELECT rowid FROM memory_refresh_generations WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                    (state['cursor'], state['end_cursor'], remaining)) if _exists(c, 'memory_refresh_generations') else []
                for row in rows:
                    _bump(counts, 'technical_cache_rows', c.execute('DELETE FROM memory_refresh_generations WHERE rowid=?', (row[0],)).rowcount)
                    state['cursor'] = row[0]; processed += 1
                if len(rows) < remaining:
                    _set_phase(c, state, 'done')
        c.execute('UPDATE memory_thought_purge_state SET phase=?,cursor=?,end_cursor=? WHERE policy=?',
            (state['phase'], state['cursor'], state['end_cursor'], _PURGE))
        blockers = [dict(row, detail=json.loads(row['detail'])) for row in c.execute('SELECT * FROM memory_thought_purge_blockers ORDER BY kind,owner')]
    thoughts._source_policy_memo.clear()
    return {'processed': processed, 'counts': dict(counts), 'complete': state['phase'] == 'done', 'phase': state['phase'],
        'protected': [row for row in blockers if row['kind']=='raw_ground_retained'],
        'blockers': [row for row in blockers if row['kind']!='raw_ground_retained']}
