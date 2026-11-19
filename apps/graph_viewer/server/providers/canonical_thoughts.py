"""Read-only canonical store adapter; compatibility SQLite is viewer-owned only."""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from .json_snapshot import JsonSnapshotSource
from .source_ir import materialize

SCHEMA = 'canonical-thought-graph/v1'


def readonly(path, **kwargs):
    connection = sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro', uri=True, timeout=5, **kwargs)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def is_canonical(connection):
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE name='viewer_source_info'").fetchone()
    return bool(row and connection.execute('SELECT 1 FROM viewer_source_info WHERE adapter_format=?', (SCHEMA,)).fetchone())


def canonical_enabled(path):
    connection = readonly(Path(path).resolve(strict=True))
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_thought_runtime'").fetchone()
        return bool(exists and connection.execute('SELECT 1 FROM memory_thought_runtime WHERE singleton=1').fetchone())
    finally:
        connection.close()


def source_for_path(path, state_dir, provider_id='assertion-memory'):
    if canonical_enabled(path):
        return CanonicalThoughtSource(path, state_dir, provider_id)
    from .assertion_memory import AssertionMemorySource
    return AssertionMemorySource(path, provider_id)


def capture(path, provider_id):
    from prototypes.assertion_memory.thought_graph import export_graph
    from prototypes.assertion_memory.thought_store import ThoughtStore
    connection = readonly(path)
    try:
        connection.execute('BEGIN')
        @contextmanager
        def transaction():
            yield connection
        # Bypass schema-writing constructors. These read methods share one pinned
        # query-only transaction, including all exact bases and raw provenance.
        thoughts = ThoughtStore.__new__(ThoughtStore)
        thoughts.store = SimpleNamespace(transaction=transaction)
        thoughts._source_policy_memo = {}
        return export_graph(thoughts, include_history=False, provider_id=provider_id)
    finally:
        connection.close()


def _overview_title(node, metadata):
    title = metadata.get('title')
    if isinstance(title, str) and title.strip():
        return title
    questions = node.get('questions', node['thought'].get('questions', ()))
    short = [question for question in questions if len(question) <= 80 and len(question.splitlines()) == 1]
    return short[0] if len(questions) == len(short) == 1 else 'Обзор без краткого названия'


def _thought_groups(nodes):
    exact={(node['thought_ref']['thought_id'],node['thought_ref']['version']):node['id'] for node in nodes}
    groups,children,unknowns=[],{},set()
    for node in nodes:
        origin=node.get('origin') or node['thought'].get('origin',{})
        metadata=node.get('metadata') or node['thought'].get('metadata',{})
        # Migrated assertions and native source interpretations stay leaves even
        # when source context references other thoughts.
        if origin.get('kind') in ('legacy_assertion','source_interpretation') or metadata.get('legacy',{}).get('kind')=='assertion':
            continue
        bases=[basis for basis in node.get('bases',[]) if 'thought_id' in basis]
        if not bases:
            continue
        published=node.get('recorded_at')
        if not published:
            unknowns.add('thought_group_publication_time_unknown')
            continue
        members=[]
        for basis in bases:
            key=(basis['thought_id'],basis['version'])
            if key not in exact:
                raise ValueError('canonical group basis has no exact thought version node')
            members.append(exact[key])
        children[node['id']]=list(dict.fromkeys(members))
        groups.append({'id':node['id'],'title':_overview_title(node, metadata),
            'question':None,'current_version_id':node['id'],
            'versions':[{'version_id':node['id'],'published_at':published,
                'member_ids':list(dict.fromkeys([node['id'],*members])),'summary':node['text']}]})
    grouped={group['id'] for group in groups}
    dates={group['id']:group['versions'][0]['published_at'] for group in groups}
    links=[{'id':parent+'::basis::'+child,'parent_id':parent,'child_id':child,
        'relation':'subquestion','recorded_at':dates[parent],
        'provenance':{'kind':'canonical_thought_basis','parent_thought_version':parent,'child_thought_version':child}}
        for parent,members in children.items() for child in members if child in grouped and child!=parent]
    return groups,links,sorted(unknowns)


def compatibility_document(document):
    if document.get('schema') != SCHEMA or document.get('semantic_model') != 'thought':
        raise ValueError('expected canonical thought graph')
    nodes = []
    for node in document['nodes']:
        if node.get('entity_type') != 'thought' or not isinstance(node.get('thought'), dict):
            raise ValueError('canonical graph contains a non-thought semantic node')
        nodes.append(dict(node, kind=None))
    groups,links,unknowns=_thought_groups(nodes)
    return dict(document,nodes=nodes,overviews=groups,topic_links=links,
                unknowns=list(dict.fromkeys([*document.get('unknowns',[]),*unknowns])))


class CanonicalThoughtSource(JsonSnapshotSource):
    def __init__(self, path, state_dir, provider_id='assertion-memory'):
        self.source_path = Path(path).resolve(strict=True)
        self.state_dir = state_dir
        self.provider_id = provider_id
        self._lock = threading.Lock()
        self._revision = None
        self._dirty = None
        self._semantic_stamp = None
        self._group_unknowns = []
        self._watch = readonly(self.source_path, check_same_thread=False)
        stat = self.source_path.stat()
        self._identity = (stat.st_dev, stat.st_ino)
        self.revision()

    def revision(self):
        with self._lock:
            stat = self.source_path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity != self._identity:
                self._watch.close()
                self._watch = readonly(self.source_path, check_same_thread=False)
                self._identity, self._dirty = identity, None
                self._semantic_stamp = None
            dirty = self._watch.execute('PRAGMA data_version').fetchone()[0]
            if dirty == self._dirty:
                return self._revision
            stamp = tuple(self._watch.execute('SELECT '
                '(SELECT coalesce(max(rowid),0) FROM memory_thought_versions),'
                '(SELECT count(*) FROM memory_thoughts),'
                '(SELECT coalesce(sum(current_version),0) FROM memory_thoughts),'
                '(SELECT coalesce(max(rowid),0) FROM memory_thought_links),'
                '(SELECT count(*) FROM memory_thought_links),'
                '(SELECT coalesce(sum(length(question)),0) FROM memory_thought_fts)').fetchone())
            if stamp == self._semantic_stamp:
                self._dirty = dirty
                return self._revision
            document = compatibility_document(capture(self.source_path, self.provider_id))
            self._group_unknowns = [code for code in document.get('unknowns',[]) if code.startswith('thought_group_')]
            fingerprint = hashlib.sha256(json.dumps({'adaptation':'canonical-thought-groups-v2',
                'document':document}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            revision = 'sha256:' + fingerprint
            if revision != self._revision:
                # Canonical schema is part of the fingerprint; publishing the
                # complete private IR never touches the source database.
                path = materialize(document, fingerprint, self.state_dir)
                self.path, self._revision = path, revision
            self._dirty = dirty
            self._semantic_stamp = stamp
            return self._revision

    def connect(self, **kwargs):
        self.revision()
        with self._lock:
            return super().connect(**kwargs)

    def assertion_membership(self, connection, recorded_cutoff):
        from .base import SOURCE_MEMBERSHIP_POLICY
        revision = connection.execute('SELECT source_revision FROM viewer_source_info').fetchone()[0]
        return frozenset(), [SOURCE_MEMBERSHIP_POLICY, SCHEMA, revision]

    def close(self):
        with self._lock:
            self._watch.close()

    def capabilities(self):
        result = super().capabilities()
        result['capabilities'].update(topics=True, topic_hierarchy=True, live=True)
        result['readiness'] = [{'code': 'canonical_thoughts', 'message': 'Canonical thoughts and exact version bases; unknown timestamps remain unknown'}]
        result['readiness'].extend({'code':code,'message':'A composite thought has no known publication time; its original node is retained without a visual group'} for code in self._group_unknowns)
        result['limits']['source_format'] = SCHEMA
        return result
