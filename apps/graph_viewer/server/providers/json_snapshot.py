"""Frozen source-neutral JSON input; no engine imports, source writes or refresh.

The validated input bytes are captured once. A new adapter instance is required to
accept a replacement file; all connections keep the same immutable IR generation.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

MAX_INPUT_BYTES = 64 * 1024 * 1024
KINDS = {None, 'assertion', 'decision', 'observation', 'implementation', 'inference',
         'proposal', 'requirement', 'plan', 'question', 'fact'}
PRECISIONS = {'unknown', 'instant', 'day', 'month', 'year'}


class SourceFormatError(ValueError):
    """Invalid source record. Messages identify schema locations, not private values."""


def fail(location, reason):
    raise SourceFormatError(f'{location}: {reason}')


def text(value, location, *, nullable=False, nonempty=True):
    if value is None and nullable:
        return
    if not isinstance(value, str) or (nonempty and not value.strip()):
        fail(location, 'expected a nonempty string' if nonempty else 'expected a string')
    try:
        value.encode('utf-8')
    except UnicodeError:
        fail(location, 'invalid Unicode')


def instant(value, location, *, nullable=False, date_allowed=False):
    if value is None and nullable:
        return
    text(value, location)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not (date_allowed and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)):
            if 'T' not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError()
    except (ValueError, OverflowError):
        fail(location, 'expected an ISO-8601 instant with timezone')


def array(value, location):
    if not isinstance(value, list):
        fail(location, 'expected an array')
    return value


def records(value, location):
    rows = array(value, location); found = {}
    for i, row in enumerate(rows):
        where = f'{location}[{i}]'
        if not isinstance(row, dict):
            fail(where, 'expected an object')
        text(row.get('id'), where + '.id')
        if row['id'] in found:
            fail(where + '.id', 'duplicate stable ID')
        found[row['id']] = row
    return found


def references(value, location, known):
    values = array(value, location)
    for i, id in enumerate(values):
        text(id, f'{location}[{i}]')
        if id not in known:
            fail(f'{location}[{i}]', 'unknown stable ID')
    if len(set(values)) != len(values):
        fail(location, 'duplicate stable ID')


def boolean(value, location):
    if value is not None and type(value) is not bool:
        fail(location, 'expected boolean or null')


def validate_document(doc):
    if not isinstance(doc, dict) or doc.get('schema') != 'graph-viewer-source/v1':
        fail('schema', 'expected graph-viewer-source/v1')
    text(doc.get('provider_id'), 'provider_id')
    text(doc.get('source_revision'), 'source_revision')
    nodes = records(doc.get('nodes'), 'nodes')
    records(doc.get('edges'), 'edges')
    episodes = records(doc.get('episodes'), 'episodes')
    overviews = records(doc.get('overviews'), 'overviews')
    records(doc.get('topic_links', []), 'topic_links')
    for i, episode in enumerate(doc['episodes']):
        p = f'episodes[{i}]'
        text(episode.get('text'), p + '.text', nonempty=False)
        instant(episode.get('recorded_at'), p + '.recorded_at')
        instant(episode.get('source_available_at'), p + '.source_available_at', nullable=True)
        for field in ['actor', 'source', 'session_id']:
            text(episode.get(field), p + '.' + field, nullable=True)
        position = episode.get('position')
        if position is not None and (type(position) is not int or not 0 <= position <= 2**31 - 1):
            fail(p + '.position', 'expected a nonnegative 32-bit integer')
        boolean(episode.get('context_truncated'), p + '.context_truncated')
        references(episode.get('context_episode_ids', []), p + '.context_episode_ids', episodes)
    for i, node in enumerate(doc['nodes']):
        p = f'nodes[{i}]'
        text(node.get('text'), p + '.text')
        if not isinstance(node.get('kind'), (str, type(None))) or node.get('kind') not in KINDS:
            fail(p + '.kind', 'unrecognized kind; use null for unknown')
        for field in ['actor', 'source', 'scope', 'episode_id']:
            text(node.get(field), p + '.' + field, nullable=True)
        instant(node.get('recorded_at'), p + '.recorded_at')
        instant(node.get('valid_at'), p + '.valid_at', nullable=True, date_allowed=True)
        precision = node.get('valid_precision', 'unknown')
        if not isinstance(precision, str) or precision not in PRECISIONS or (node.get('valid_at') is None and precision != 'unknown'):
            fail(p + '.valid_precision', 'invalid source precision')
        for field in ['source_recorded_at', 'source_available_at', 'interpreted_at']:
            instant(node.get(field), p + '.' + field, nullable=True)
        boolean(node.get('context_truncated'), p + '.context_truncated')
        references(node.get('context_episode_ids', []), p + '.context_episode_ids', episodes)
        for j, ref in enumerate(array(node.get('evidence_refs', []), p + '.evidence_refs')):
            r = f'{p}.evidence_refs[{j}]'
            if not isinstance(ref, dict):
                fail(r, 'expected an object')
            text(ref.get('episode_id'), r + '.episode_id')
            if ref['episode_id'] not in episodes:
                fail(r + '.episode_id', 'saved episode is unavailable')
            for field in ['start', 'end']:
                if type(ref.get(field)) is not int:
                    fail(r + '.' + field, 'expected a Unicode-codepoint offset')
            if not 0 <= ref['start'] < ref['end'] <= len(episodes[ref['episode_id']]['text']):
                fail(r + '.end', 'Unicode-codepoint span is outside saved text')
        if 'published_status' in node:
            status = node['published_status']
            if not isinstance(status, dict) or set(status) != {'published', 'disposition'} or status.get('published') is not True or status.get('disposition') != 'cancelled':
                fail(p + '.published_status', 'only explicit published cancellation is supported')
    for i, edge in enumerate(doc['edges']):
        p = f'edges[{i}]'
        for field in ['from_id', 'to_id']:
            text(edge.get(field), p + '.' + field)
            if edge[field] not in nodes:
                fail(p + '.' + field, 'unknown assertion ID')
        text(edge.get('kind'), p + '.kind')
        instant(edge.get('recorded_at'), p + '.recorded_at')
        instant(edge.get('effective_at'), p + '.effective_at', nullable=True, date_allowed=True)
        for field in ['source_quote', 'target_quote', 'condition', 'basis']:
            text(edge.get(field), p + '.' + field, nullable=True, nonempty=False)
        if edge['kind'] == 'supersedes' and any(not nodes[edge[f]].get('scope') for f in ['from_id', 'to_id']):
            fail(p + '.kind', 'supersedes requires explicit endpoint scopes')
    for i, overview in enumerate(doc['overviews']):
        p = f'overviews[{i}]'; text(overview.get('title'), p + '.title')
        text(overview.get('question'), p + '.question', nullable=True)
        for j, scope in enumerate(array(overview.get('scopes', []), p + '.scopes')):
            text(scope, f'{p}.scopes[{j}]')
        versions = array(overview.get('versions'), p + '.versions'); ids = set()
        for j, version in enumerate(versions):
            v = f'{p}.versions[{j}]'
            if not isinstance(version, dict):
                fail(v, 'expected an object')
            text(version.get('version_id'), v + '.version_id')
            if version['version_id'] in ids:
                fail(v + '.version_id', 'duplicate stable ID')
            ids.add(version['version_id'])
            instant(version.get('published_at'), v + '.published_at')
            text(version.get('summary'), v + '.summary', nullable=True, nonempty=False)
            references(version.get('member_ids'), v + '.member_ids', nodes)
            for field in ['valid_cutoff', 'recorded_cutoff']:
                instant(version.get(field), v + '.' + field, nullable=True)
        text(overview.get('current_version_id'), p + '.current_version_id')
        if overview['current_version_id'] not in ids:
            fail(p + '.current_version_id', 'unknown source version ID')
    adjacency = {id: [] for id in overviews}; indegree = dict.fromkeys(overviews, 0)
    for i, link in enumerate(doc.get('topic_links', [])):
        p = f'topic_links[{i}]'
        for field in ['parent_id', 'child_id']:
            text(link.get(field), p + '.' + field)
            if link[field] not in overviews:
                fail(p + '.' + field, 'unknown overview ID')
        if not isinstance(link.get('relation'), str) or link['relation'] not in {'subquestion', 'prerequisite', 'related'}:
            fail(p + '.relation', 'unsupported relationship')
        instant(link.get('recorded_at'), p + '.recorded_at')
        if not isinstance(link.get('provenance'), dict) or not link['provenance']:
            fail(p + '.provenance', 'explicit provenance object required')
        if link['relation'] == 'subquestion':
            adjacency[link['parent_id']].append(link['child_id']); indegree[link['child_id']] += 1
    ready = [id for id, n in indegree.items() if n == 0]
    for id in ready:
        for child in adjacency[id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(ready) != len(overviews):
        fail('topic_links', 'source subquestion cycle')
    return doc


def capture(path):
    """Read a bounded revision from one descriptor, rejecting a changing input."""
    def fingerprint(stat):
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    try:
        with Path(path).open('rb') as stream:
            before = fingerprint(os.fstat(stream.fileno()))
            raw = stream.read(MAX_INPUT_BYTES + 1)
            after = fingerprint(os.fstat(stream.fileno()))
        if before != after or after != fingerprint(Path(path).stat()) or len(raw) != after[2]:
            fail('input', 'source changed during capture')
    except OSError:
        raise SourceFormatError('input: source snapshot could not be read') from None
    if len(raw) > MAX_INPUT_BYTES:
        fail('input', 'snapshot exceeds 64 MiB limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail('input', 'duplicate JSON object key')
            result[key] = value
        return result
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed) or abs(parsed) > 2**53 - 1:
            fail('input', 'numeric value is outside interoperable finite range')
        return parsed
    def integer(value):
        parsed = int(value)
        if abs(parsed) > 2**53 - 1:
            fail('input', 'integer is outside interoperable range')
        return parsed
    try:
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_float=number,
                         parse_int=integer, parse_constant=lambda _: fail('input', 'nonfinite number'))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, SourceFormatError):
            raise
        raise SourceFormatError('input: invalid UTF-8 JSON document') from None
    # Provenance/unknown metadata is JSON too: validate all strings before any IR
    # write, including keys and uninterpreted values that SQLite must encode.
    pending = [doc]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            text(value, 'input', nonempty=False)
        elif isinstance(value, dict):
            pending.extend(value); pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return validate_document(doc), hashlib.sha256(raw).hexdigest()


class JsonSnapshotSource:
    def __init__(self, path, state_dir):
        from .source_ir import materialize
        doc, fingerprint = capture(path)
        self.source_path = Path(path).absolute()
        self.provider_id = doc['provider_id']
        self._revision = 'sha256:' + fingerprint
        self.path = materialize(doc, fingerprint, state_dir)

    def connect(self, **kwargs):
        uri = 'file:' + quote(str(self.path), safe='/') + '?mode=ro&immutable=1'
        connection = sqlite3.connect(uri, uri=True, timeout=5, **kwargs)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return connection

    def revision(self):
        return self._revision

    def assertion_membership(self, connection, recorded_cutoff):
        from .base import SOURCE_MEMBERSHIP_POLICY
        return frozenset(), [SOURCE_MEMBERSHIP_POLICY, self._revision]

    def close(self):
        pass

    def has_object(self, kind, id):
        with self.connect() as connection:
            if kind == 'node':
                return connection.execute('SELECT 1 FROM memory_assertions WHERE assertion_id=?', (id,)).fetchone() is not None
            if kind == 'topic' and id.startswith('topic:'):
                return connection.execute('SELECT 1 FROM memory_views WHERE id=?', (id[6:],)).fetchone() is not None
        return False

    def capabilities(self):
        return {'provider_id': self.provider_id, 'capabilities': {
            'full_graph': True, 'topics': True, 'topic_hierarchy': True,
            'source_context': True, 'whole_saved_source': True,
            'keyword_search': True, 'semantic_search': False, 'history': True, 'live': False},
            'readiness': [
                {'code': 'frozen_json_snapshot', 'message': 'Immutable JSON input captured at startup; restart adapter to accept a new input revision'},
                {'code': 'semantic_unavailable', 'message': 'Semantic index/query model not yet configured'},
                {'code': 'global_cancellation', 'message': 'Only explicit source-published cancellation is supported'}],
            'limits': {'incident_page': 2000, 'text_page': 64000, 'input_bytes': MAX_INPUT_BYTES,
                       'cancellation': 'explicit_published_only', 'source_format': 'graph-viewer-source/v1'}}
