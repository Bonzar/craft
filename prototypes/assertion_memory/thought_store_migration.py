"""Resumable bounded import; legacy tables remain the lossless rollback source.

No extraction or models run here. A frozen rowid frontier is recorded once and
only that frontier is imported. Useful legacy prose remains explicitly unverified;
failed/empty/question-only records stay in audit, outside the canonical index.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from .thought_store import Thought, _hash, _json

_TABLES = ('memory_assertions', 'memory_views', 'memory_view_versions', 'memory_edges')


def _ensure(c):
    has_exclusions = c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_thought_legacy_exclusions'").fetchone()
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_legacy_exclusions (thought_id TEXT PRIMARY KEY)')
    if not has_exclusions:
        from .source_revision import exclusion
        clauses, arguments = exclusion(c, 'a')
        if clauses:
            rows = c.execute('SELECT a.assertion_id FROM memory_assertions a WHERE NOT (' +
                ' AND '.join(clauses) + ')', arguments)
            c.executemany('INSERT INTO memory_thought_legacy_exclusions VALUES(?)', [(row[0],) for row in rows])
        # Also repairs a pristine migration started before this exclusion policy.
        c.execute('DELETE FROM memory_thought_fts WHERE thought_id IN (SELECT thought_id FROM memory_thought_legacy_exclusions)')
        c.execute('DELETE FROM memory_thoughts WHERE id IN (SELECT thought_id FROM memory_thought_legacy_exclusions)')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_migration ('
        'source_table TEXT PRIMARY KEY,cursor INTEGER NOT NULL,end_cursor INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_legacy ('
        'source_table TEXT NOT NULL,source_key TEXT NOT NULL,payload TEXT NOT NULL,'
        'thought_id TEXT,thought_version INTEGER,exclusion TEXT,'
        'PRIMARY KEY(source_table,source_key))')
    for table in _TABLES:
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        end = c.execute('SELECT coalesce(max(rowid),0) FROM ' + table).fetchone()[0] if exists else 0
        c.execute('INSERT OR IGNORE INTO memory_thought_migration VALUES(?,0,?)', (table, end))


def migrate_batch(thoughts, *, limit=100):
    """Atomically import at most limit rows; safe to resume after any interruption."""
    if type(limit) is not int or limit < 1:
        raise ValueError('limit must be positive')
    processed = 0
    with thoughts.store.transaction(write=True) as c:
        _ensure(c)
        for table in _TABLES:
            cursor, end = c.execute('SELECT cursor,end_cursor FROM memory_thought_migration '
                'WHERE source_table=?', (table,)).fetchone()
            if cursor >= end or processed >= limit:
                continue
            rows = c.execute('SELECT rowid AS migration_rowid,* FROM ' + table +
                ' WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?', (cursor, end, limit - processed)).fetchall()
            for row in rows:
                _import_row(thoughts, c, table, dict(row))
                cursor = row['migration_rowid']
                processed += 1
            # Deleted rowids are harmless holes inside the frozen frontier.
            if not rows:
                cursor = end
            c.execute('UPDATE memory_thought_migration SET cursor=? WHERE source_table=?', (cursor, table))
        pending = [dict(row) for row in c.execute('SELECT * FROM memory_thought_migration WHERE cursor<end_cursor')]
        return {'processed': processed, 'complete': not pending, 'pending': pending}


def migration_status(thoughts):
    with thoughts.store.transaction(write=True) as c:
        _ensure(c)
        cursors = [dict(row) for row in c.execute('SELECT * FROM memory_thought_migration ORDER BY source_table')]
        exclusions = [dict(row) for row in c.execute('SELECT exclusion,count(*) AS count '
            'FROM memory_thought_legacy WHERE exclusion IS NOT NULL GROUP BY exclusion')]
        return {'complete': all(row['cursor'] >= row['end_cursor'] for row in cursors),
                'cursors': cursors, 'exclusions': exclusions}


def _audit(c, table, key, payload, thought=None, exclusion=None):
    c.execute('INSERT INTO memory_thought_legacy VALUES(?,?,?,?,?,?)',
        (table, str(key), _json(payload), thought.id if thought else None,
         thought.version if thought else None, exclusion))


def _import_row(thoughts, c, table, row):
    row.pop('migration_rowid')
    if table == 'memory_assertions':
        thought, exclusion = _assertion(thoughts, c, row)
        _insert_import(thoughts, c, thought, activate=exclusion is None)
        _audit(c, table, row['assertion_id'], row, thought, exclusion)
    elif table == 'memory_views':
        _audit(c, table, row['id'], row, exclusion='definition_only')
    elif table == 'memory_view_versions':
        thought, exclusion, active = _view_version(thoughts, c, row)
        _insert_import(thoughts, c, thought, activate=active)
        _audit(c, table, row['id'], row, thought, exclusion)
    else:
        source, target = thoughts.get(row['from_id']), thoughts.get(row['to_id'])
        exclusion = None
        if source is not None and target is not None:
            material = dict(id=row['edge_id'], source_id=source.id, source_version=source.version,
                target_id=target.id, target_version=target.version, meaning=row['kind'],
                source_span=None, target_span=None, metadata={'legacy': row})
            c.execute('INSERT INTO memory_thought_links VALUES(?,?,?,?,?,?)',
                (row['edge_id'], source.id, source.version, target.id, target.version, _json(material)))
        else:
            exclusion = 'inactive_link_endpoint'
        _audit(c, table, row['edge_id'], row, exclusion=exclusion)


def _insert_import(thoughts, c, thought, *, activate):
    exists = c.execute('SELECT 1 FROM memory_thought_versions WHERE thought_id=? LIMIT 1', (thought.id,)).fetchone()
    if exists and c.execute('SELECT 1 FROM memory_thought_legacy '
        'WHERE thought_id=?', (thought.id,)).fetchone() is None:
        raise RuntimeError('legacy identity collides with native thought: ' + thought.id)
    if c.execute('SELECT 1 FROM memory_assertions WHERE assertion_id=?', (thought.id,)).fetchone() and thought.origin['kind'] == 'legacy_view':
        raise RuntimeError('assertion and view identity collide: ' + thought.id)
    thoughts._insert(c, thought, activate=activate)


def _assertion(thoughts, c, row):
    metadata = json.loads(row['metadata'])
    origin = {key: row[key] for key in ('actor', 'source', 'episode', 'valid_at', 'recorded_at', 'scope')}
    origin['kind'] = 'legacy_assertion'
    bases = []
    event_id = metadata.get('event_id')
    has_raw = c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_raw_events'").fetchone()
    if isinstance(event_id, str) and has_raw and c.execute('SELECT 1 FROM memory_raw_events WHERE id=?', (event_id,)).fetchone():
        bases.append(thoughts._pin_basis(c, {'event_id': event_id}, _audit=True))
    thought = Thought(row['assertion_id'], 1, row['text'], None, origin, bases,
        {'legacy': {'kind': 'assertion', 'original': row}, 'verification': 'legacy_unverified'}, row['recorded_at'])
    from .thought_source_policy import thought_eligibility
    if not thought_eligibility(thoughts, thought)['allowed']:
        return thought, 'excluded_source_policy'
    if c.execute('SELECT 1 FROM memory_thought_legacy_exclusions WHERE thought_id=?', (thought.id,)).fetchone():
        return thought, 'inactive_source_revision'
    return thought, None if row['text'].strip() else 'empty_content'


def _binding(thought):
    return {'thought_id': thought.id, 'version': thought.version,
        'content_hash': _hash({'source': asdict(thought), 'span': None, 'text': thought.text})}


def _view_version(thoughts, c, row):
    snapshot = json.loads(row['snapshot'])
    view = c.execute('SELECT * FROM memory_views WHERE id=?', (row['view_id'],)).fetchone()
    definition = json.loads(view['definition'])
    number = c.execute('SELECT count(*) FROM memory_view_versions WHERE view_id=? AND id<=?',
        (row['view_id'], row['id'])).fetchone()[0]
    bases, unknown = [], []
    for evidence in snapshot.get('evidence', []):
        key = evidence.get('id')
        target = thoughts.get(key, 1) if isinstance(key, str) else None
        if target is None:
            unknown.append({'kind': 'assertion', 'id': key})
        else:
            bases.append(_binding(target))
            if thoughts.get(target.id) is None:
                unknown.append({'kind': 'inactive_assertion', 'id': target.id})
    for view_id, legacy_version in snapshot.get('upstream', {}).items():
        mapped = c.execute("SELECT thought_id,thought_version FROM memory_thought_legacy "
            "WHERE source_table='memory_view_versions' AND source_key=?", (str(legacy_version),)).fetchone()
        target = thoughts.get(mapped[0], mapped[1]) if mapped else None
        if target is None or target.id != view_id:
            unknown.append({'kind': 'view', 'id': view_id, 'version_id': legacy_version})
        else:
            bases.append(_binding(target))
    review = snapshot.get('overview_review') or {}
    summary = snapshot.get('summary', '')
    exclusion = 'empty_content' if not summary.strip() else None
    if review.get('status') in ('unresolved', 'revise', 'rejected', 'failed'):
        exclusion = 'failed_review'
    if not bases and exclusion is None:
        exclusion = 'no_legacy_bases'
    if unknown:
        exclusion = 'unresolved_legacy_bases'
    title = re.sub(r'\W+', '', definition.get('title', '').casefold())
    if row['view_id'].startswith('scope-overview') or title == 'sharedscopeoverview':
        exclusion = 'service_scope_root'
    metadata = {'legacy': {'kind': 'view', 'version_id': row['id'], 'original': row,
        'definition': dict(view), 'unresolved_bases': unknown}, 'verification': 'legacy_unverified'}
    thought = Thought(row['view_id'], number, summary, definition.get('query'),
        {'kind': 'legacy_view'}, list({(b['thought_id'], b['version']): b for b in bases}.values()),
        metadata, snapshot.get('created_at', ''))
    active = view['current_id'] == row['id'] and exclusion is None
    return thought, exclusion, active


def rollback_migration(thoughts):
    """Remove only a pristine import; refuse to discard any later native work.

    Legacy source tables never change. After this rollback the old runtime can
    read precisely its original content, identities, versions and technical log.
    """
    with thoughts.store.transaction(write=True) as c:
        _ensure(c)
        native = c.execute('SELECT 1 FROM memory_thought_versions v WHERE NOT EXISTS('
            'SELECT 1 FROM memory_thought_legacy a WHERE a.thought_id=v.thought_id '
            'AND a.thought_version=v.version) LIMIT 1').fetchone()
        native_link = c.execute("SELECT 1 FROM memory_thought_links l WHERE NOT EXISTS("
            "SELECT 1 FROM memory_thought_legacy a WHERE a.source_table='memory_edges' "
            'AND a.source_key=l.id) LIMIT 1').fetchone()
        retired = c.execute('SELECT 1 FROM memory_thought_retirements LIMIT 1').fetchone()
        if native or native_link or retired:
            raise RuntimeError('rollback refused: native thought publications or links exist; restore a backup instead')
        for table in ('memory_thought_links', 'memory_thought_dependencies', 'memory_thought_fts', 'memory_thoughts',
                      'memory_thought_versions', 'memory_thought_legacy', 'memory_thought_migration'):
            c.execute('DELETE FROM ' + table)
        c.execute('DROP TABLE memory_thought_legacy_exclusions')
