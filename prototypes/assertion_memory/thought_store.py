"""One canonical, append-only semantic representation for every useful thought."""
from __future__ import annotations

import hashlib
import json
from .thought_payload import encode_payload, decode_payload
import re
import uuid
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone


_PUBLICATION_GUARD = ContextVar('thought_publication_guard', default=None)


@contextmanager
def publication_guard(check, *, on_publish=None):
    """Fence leased publishers and persist any follow-up work with the new version."""
    token = _PUBLICATION_GUARD.set((check, on_publish))
    try:
        yield
    finally:
        _PUBLICATION_GUARD.reset(token)


def _check_publication(connection):
    guard = _PUBLICATION_GUARD.get()
    if guard is None:
        return None
    check, on_publish = guard
    check(connection)
    return on_publish


class ThoughtConflict(RuntimeError):
    """A publication was prepared against a head that has since changed."""


@dataclass(frozen=True)
class Thought:
    id: str
    version: int
    text: str
    question: str | None
    origin: dict
    bases: list[dict]
    metadata: dict
    created_at: str


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS memory_thoughts (
        id TEXT PRIMARY KEY, current_version INTEGER NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS memory_thought_versions (
        thought_id TEXT NOT NULL, version INTEGER NOT NULL,
        payload TEXT NOT NULL, content_hash TEXT NOT NULL,
        PRIMARY KEY(thought_id,version))''',
    '''CREATE VIRTUAL TABLE IF NOT EXISTS memory_thought_fts USING fts5(
        thought_id UNINDEXED,text,question)''',
    '''CREATE TABLE IF NOT EXISTS memory_thought_links (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_version INTEGER NOT NULL,
        target_id TEXT NOT NULL, target_version INTEGER NOT NULL, payload TEXT NOT NULL,
        FOREIGN KEY(source_id,source_version) REFERENCES memory_thought_versions(thought_id,version),
        FOREIGN KEY(target_id,target_version) REFERENCES memory_thought_versions(thought_id,version))''',
    '''CREATE TABLE IF NOT EXISTS memory_thought_dependencies (
        dependent_id TEXT NOT NULL, dependency_id TEXT NOT NULL, dependency_version INTEGER NOT NULL,
        PRIMARY KEY(dependent_id,dependency_id,dependency_version))''',
    'CREATE INDEX IF NOT EXISTS memory_thought_dependency_lookup ON memory_thought_dependencies(dependency_id,dependent_id)',
    '''CREATE TABLE IF NOT EXISTS memory_thought_retirements (
        id INTEGER PRIMARY KEY,thought_id TEXT NOT NULL,version INTEGER NOT NULL,
        reason TEXT NOT NULL,proof TEXT NOT NULL,retired_at TEXT NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS memory_thought_link_source ON memory_thought_links(source_id)',
    'CREATE INDEX IF NOT EXISTS memory_thought_link_target ON memory_thought_links(target_id)',
)


def same_meaning_version(left, right):
    return (left is not None and right is not None and left.id == right.id
            and left.metadata.get('meaning_version', left.version)
            == right.metadata.get('meaning_version', right.version))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ' must be nonempty text')
    return value


def _object(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(field + ' must be an object')
    return json.loads(_json(value))


def _span(text, span):
    if span is None:
        return text
    if not isinstance(span, dict) or set(span) != {'start', 'end'}:
        raise ValueError('span must contain start and end')
    start, end = span['start'], span['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError('span is outside bound text')
    return text[start:end]


def _same_material(current, material):
    metadata = material['metadata']
    version = metadata.get('meaning_version')
    if (type(version) is int and version == current.version
            and 'meaning_version' not in current.metadata):
        # The absent marker already means the current version. Store it only
        # when a new publication needs to preserve that meaning's version.
        material = dict(material, metadata={
            key: value for key, value in metadata.items() if key != 'meaning_version'
        })
    return all(getattr(current, key) == value for key, value in material.items())


class ThoughtStore:
    """Shares MemoryStore's connection, transactions and durable raw intake.

    New meaning is written only here. Legacy assertion/view tables are migration
    sources, never fallback content. Exact immutable version bases form a DAG by
    construction: a version can only cite versions that already exist. Historical
    self-reference (v2 citing v1) is valid; semantic links may freely cycle.
    """

    def __init__(self, store):
        self.store = store
        self._source_policy_memo = {}
        with store.transaction(write=True) as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            from .thought_questions import ensure_schema
            ensure_schema(connection)

    def get(self, thought_id, version=None, *, include_ineligible=False):
        _text(thought_id, 'thought_id')
        with self.store.transaction() as c:
            if version is None:
                row = c.execute('SELECT v.payload FROM memory_thought_versions v '
                    'JOIN memory_thoughts h ON h.id=v.thought_id AND h.current_version=v.version '
                    'WHERE h.id=?', (thought_id,)).fetchone()
            else:
                row = c.execute('SELECT payload FROM memory_thought_versions '
                    'WHERE thought_id=? AND version=?', (thought_id, version)).fetchone()
            result = Thought(**decode_payload(row[0])) if row else None
            if result is not None and version is None and not include_ineligible and not self._policy_allowed(result):
                return None
            return result

    def versions(self, thought_id):
        with self.store.transaction() as c:
            return [Thought(**decode_payload(row[0])) for row in c.execute(
                'SELECT payload FROM memory_thought_versions WHERE thought_id=? ORDER BY version', (thought_id,))]

    def list_thoughts(self, *, limit=None, after_id=None, include_selections=False, include_ineligible=False):
        with self.store.transaction() as c:
            rows = c.execute('SELECT v.payload FROM memory_thoughts h JOIN memory_thought_versions v '
                'ON v.thought_id=h.id AND v.version=h.current_version WHERE h.id>? '
                "AND (? OR json_type(v.payload,'$.metadata.selection_context') IS NULL) ORDER BY h.id LIMIT ?",
                (after_id or '', include_selections, -1 if limit is None or not include_ineligible else limit))
            result = []
            for row in rows:
                thought = Thought(**decode_payload(row[0]))
                if include_ineligible or self._policy_allowed(thought):
                    result.append(thought)
                    if limit is not None and len(result) >= limit:
                        break
            return result

    def questions(self, thought):
        from .thought_questions import question_texts,proof_rows
        if not self._policy_allowed(thought):return []
        with self.store.transaction() as c:
            return question_texts(thought,proof_rows(c,thought.id,thought.version),thought.metadata.get('selection_context'))

    def question_checked(self, thought, question, frontier):
        from .thought_questions import checked,proof_rows
        with self.store.transaction() as c:
            return checked(proof_rows(c,thought.id,thought.version),question,frontier,thought.metadata.get('selection_context'))

    def save_question_check(self, thought, question, frontier):
        from .thought_questions import save_check
        with self.store.transaction(write=True) as c:
            if self.get(thought.id,thought.version) is None or not self._policy_allowed(thought):
                raise ValueError('question applicability requires an existing admitted thought version')
            save_check(c,thought,question,frontier,thought.metadata.get('selection_context'))
            current=self.get(thought.id)
            if current is not None and current.version==thought.version:
                body=json.dumps(self.questions(thought),ensure_ascii=False)
                c.execute('UPDATE memory_thought_fts SET question=? WHERE thought_id=? AND question!=?',(body,thought.id,body))

    def index_entries(self, *, include_selections=False):
        """Text is stored once; checked questions remain separate equal inputs."""
        from .thought_questions import decode_index_questions,proofs,applicable
        with self.store.transaction() as c:
            rows=c.execute('SELECT f.thought_id AS id,h.current_version AS version,f.text,f.question '
                'FROM memory_thought_fts f JOIN memory_thoughts h ON h.id=f.thought_id '
                'JOIN memory_thought_versions v ON v.thought_id=h.id AND v.version=h.current_version '
                "WHERE ? OR json_type(v.payload,'$.metadata.selection_context') IS NULL ORDER BY h.id",(include_selections,))
            entries={row['id']:{'id':row['id'],'version':row['version'],'text':row['text'],
                'questions':set(decode_index_questions(row['question']))} for row in rows}
            checks=c.execute('SELECT r.thought_id,r.question,r.frontier,r.proofs FROM memory_thought_reuse_checks r '
                'JOIN memory_thoughts h ON h.id=r.thought_id AND h.current_version=r.version')
            for row in checks:
                if row['thought_id'] in entries and any(applicable(proof,None) for proof in proofs(row)):
                    entries[row['thought_id']]['questions'].add(row['question'])
            return [dict(row,questions=sorted(row['questions'])) for row in entries.values()]

    def search_text(self, query, limit=10):
        terms = re.findall(r'\w+', _text(query, 'query'), flags=re.UNICODE)
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be positive')
        if not terms:
            return []
        expression = ' OR '.join('"' + term.replace('"', '""') + '"*' for term in terms)
        with self.store.transaction() as c:
            rows = c.execute('SELECT f.thought_id FROM memory_thought_fts f '
                'JOIN memory_thoughts h ON h.id=f.thought_id JOIN memory_thought_versions v '
                'ON v.thought_id=h.id AND v.version=h.current_version '
                "WHERE memory_thought_fts MATCH ? AND json_type(v.payload,'$.metadata.selection_context') IS NULL "
                'ORDER BY rank,f.thought_id', (expression,))
            result = []
            for row in rows:
                thought = self.get(row[0])
                if thought is not None:
                    result.append(thought)
                    if len(result) >= limit:
                        break
            return result

    def publish(self, text, question=None, origin=None, bases=(), metadata=None,
                thought_id=None, expected_version=None):
        _text(text, 'text')
        if question is not None:
            _text(question, 'question')
        thought_id = _text(thought_id, 'thought_id') if thought_id is not None else 'thought-' + uuid.uuid4().hex
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError('expected_version must be a nonnegative integer')
        origin, metadata = _object(origin, 'origin'), _object(metadata, 'metadata')
        from .thought_source_policy import event_eligibility
        if not event_eligibility(origin)['allowed']:
            raise ValueError('thought origin is excluded by visible dialogue source policy')
        if not isinstance(bases, (list, tuple)):
            raise ValueError('bases must be a list')
        with self.store.transaction(write=True) as c:
            on_publish = _check_publication(c)
            current = self.get(thought_id)
            actual = current.version if current else 0
            if expected_version is not None and expected_version != actual:
                raise ThoughtConflict(f'{thought_id}: expected {expected_version}, found {actual}')
            if current is not None and expected_version is None:
                raise ThoughtConflict('updating a thought requires expected_version')
            pinned = [self._pin_basis(c, basis) for basis in bases]
            material = dict(text=text, question=question, origin=origin, bases=pinned, metadata=metadata)
            if current and _same_material(current, material):
                return current
            thought = Thought(id=thought_id, version=actual + 1, created_at=datetime.now(timezone.utc).isoformat(), **material)
            self._insert(c, thought)
            if on_publish is not None:
                on_publish(current, thought)
            return thought

    def _insert(self, c, thought, *, activate=True):
        """Internal import seam; the migration supplies already pinned audit bases."""
        payload = asdict(thought)
        c.execute('INSERT INTO memory_thought_versions VALUES(?,?,?,?)',
            (thought.id, thought.version, encode_payload(payload), _hash(payload)))
        if activate:
            c.execute('INSERT INTO memory_thoughts VALUES(?,?) ON CONFLICT(id) '
                'DO UPDATE SET current_version=excluded.current_version', (thought.id, thought.version))
            c.execute('DELETE FROM memory_thought_fts WHERE thought_id=?', (thought.id,))
            c.execute('INSERT INTO memory_thought_fts VALUES(?,?,?)', (thought.id, thought.text, json.dumps([thought.question] if thought.question else [],ensure_ascii=False)))
            self._index_bases(c, thought)

    def _pin_basis(self, c, raw, *, _audit=False):
        basis = _object(raw, 'basis')
        is_thought = 'thought_id' in basis
        if is_thought == ('event_id' in basis):
            raise ValueError('basis must identify exactly one thought or raw event')
        allowed = {'thought_id', 'event_id', 'version', 'span', 'meaning', 'content_hash', 'observed_head_version'}
        if set(basis) - allowed:
            raise ValueError('unknown basis fields')
        if 'meaning' in basis:
            _text(basis['meaning'], 'basis meaning')
        if is_thought:
            current = self.get(_text(basis['thought_id'], 'basis thought_id'))
            if current is None:
                if self.get(basis['thought_id'], basis.get('version')) is not None:
                    raise ThoughtConflict('basis thought was retired')
                raise ValueError('unknown basis thought')
            version = basis.get('version', current.version)
            if type(version) is not int or version < 1:
                raise ValueError('basis version must be positive')
            observed = basis.pop('observed_head_version', None)
            if observed is None:
                if version != current.version:
                    raise ThoughtConflict('basis head changed: ' + current.id)
            elif type(observed) is not int or observed < 1:
                raise ValueError('observed_head_version must be positive')
            elif observed != current.version:
                raise ThoughtConflict('observed basis head changed: ' + current.id)
            selected = self.get(current.id, version)
            if selected is None:
                raise ValueError('unknown historical basis version')
            basis['version'] = version
            source = asdict(selected)
            text = selected.text
        else:
            _text(basis['event_id'], 'basis event_id')
            if 'version' in basis or 'observed_head_version' in basis:
                raise ValueError('raw event basis has no thought version')
            row = c.execute('SELECT * FROM memory_raw_events WHERE id=?', (basis['event_id'],)).fetchone()
            if row is None:
                raise ValueError('unknown raw event')
            source = dict(row)
            from .thought_source_policy import event_eligibility
            if not _audit and not event_eligibility(source)['allowed']:
                raise ValueError('raw source is excluded by visible dialogue source policy')
            text = source['prompt']
        selected = _span(text, basis.get('span'))
        fingerprint = _hash({'source': source, 'span': basis.get('span'), 'text': selected})
        if basis.get('content_hash', fingerprint) != fingerprint:
            raise ThoughtConflict('basis content changed')
        basis['content_hash'] = fingerprint
        return basis

    def connect(self, source_id, target_id, meaning, source_version=None, target_version=None,
                source_span=None, target_span=None, metadata=None):
        _text(meaning, 'meaning')
        with self.store.transaction(write=True) as c:
            _check_publication(c)
            source, target = self.get(source_id, source_version), self.get(target_id, target_version)
            if source is None or target is None:
                raise ValueError('link endpoints must be existing thoughts')
            _span(source.text, source_span)
            _span(target.text, target_span)
            material = dict(source_id=source.id, source_version=source.version, target_id=target.id,
                target_version=target.version, meaning=meaning, source_span=source_span,
                target_span=target_span, metadata=_object(metadata, 'metadata'))
            link = dict(id='thought-link-' + _hash(material),
                created_at=datetime.now(timezone.utc).isoformat(), **material)
            c.execute('INSERT OR IGNORE INTO memory_thought_links VALUES(?,?,?,?,?,?)',
                (link['id'], source.id, source.version, target.id, target.version, _json(link)))
            return json.loads(c.execute('SELECT payload FROM memory_thought_links WHERE id=?', (link['id'],)).fetchone()[0])

    def links(self, thought_id=None, *, include_inactive=False):
        with self.store.transaction() as c:
            rows = c.execute('SELECT payload FROM memory_thought_links WHERE ? IS NULL OR '
                'source_id=? OR target_id=? ORDER BY id', (thought_id, thought_id, thought_id))
            result = [json.loads(row[0]) for row in rows]
            return result if include_inactive else [link for link in result
                if self.get(link['source_id']) is not None and self.get(link['target_id']) is not None]

    def _policy_allowed(self, thought):
        from .thought_source_policy import thought_eligibility
        return thought_eligibility(self, thought, memo=self._source_policy_memo)['allowed']

    @staticmethod
    def _index_bases(c, thought):
        c.execute('DELETE FROM memory_thought_dependencies WHERE dependent_id=?', (thought.id,))
        c.executemany('INSERT OR IGNORE INTO memory_thought_dependencies VALUES(?,?,?)',
            [(thought.id, basis['thought_id'], basis['version']) for basis in thought.bases if 'thought_id' in basis])

    def index_dependencies(self, *, after_id='', limit=100):
        """Explicit bounded upgrade of existing canonical heads; no history rewrite."""
        if type(limit) is not int or limit < 1:
            raise ValueError('dependency index limit must be positive')
        with self.store.transaction(write=True) as c:
            heads = self.list_thoughts(after_id=after_id, limit=limit, include_selections=True)
            for head in heads:
                self._index_bases(c, head)
            return {'processed': len(heads), 'after_id': heads[-1].id if heads else after_id,
                    'complete': len(heads) < limit}

    def direct_dependents(self, thought_id, *, limit=100, after_id=''):
        if type(limit) is not int or limit < 1:
            raise ValueError('dependent page limit must be positive')
        with self.store.transaction() as c:
            rows = c.execute('SELECT DISTINCT v.payload,d.dependent_id FROM memory_thought_dependencies d '
                'JOIN memory_thoughts h ON h.id=d.dependent_id JOIN memory_thought_versions v '
                'ON v.thought_id=h.id AND v.version=h.current_version '
                'WHERE d.dependency_id=? AND d.dependent_id>? ORDER BY d.dependent_id LIMIT ?',
                (thought_id, after_id, limit))
            return [Thought(**decode_payload(row[0])) for row in rows]

    def retire(self, thought_ids, reason, guardproof, *, expected_version=None):
        _text(reason, 'retirement reason')
        proof = _object(guardproof, 'retirement proof')
        if proof.get('verification') != 'accepted' or not isinstance(proof.get('expected_versions', {}), dict):
            raise ValueError('retirement requires accepted verification and exact expected versions')
        single = isinstance(thought_ids, str)
        ids = [thought_ids] if single else list(thought_ids)
        expected = dict(proof.get('expected_versions', {}))
        if single and expected_version is not None:
            expected[thought_ids] = expected_version
        retired = []
        with self.store.transaction(write=True) as c:
            _check_publication(c)
            for key in dict.fromkeys(ids):
                _text(key, 'retired thought id')
                thought = self.get(key, include_ineligible=True)
                if thought is None:
                    continue
                if type(expected.get(key)) is not int or expected[key] != thought.version:
                    raise ThoughtConflict('retirement head changed or was not fenced: ' + key)
                c.execute('DELETE FROM memory_thoughts WHERE id=?', (key,))
                c.execute('DELETE FROM memory_thought_fts WHERE thought_id=?', (key,))
                c.execute('DELETE FROM memory_thought_dependencies WHERE dependent_id=?', (key,))
                c.execute('INSERT INTO memory_thought_retirements(thought_id,version,reason,proof,retired_at) '
                    'VALUES(?,?,?,?,?)', (key, thought.version, reason, _json(proof), datetime.now(timezone.utc).isoformat()))
                retired.append(thought)
        return (retired[0] if retired else None) if single else retired

    def expand_bases(self, thought_id, version=None, *, limit=100):
        """Bound work per call without imposing a maximum stored graph depth."""
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be positive')
        root = self.get(thought_id, version)
        pending = [(root.id, root.version)] if root else []
        seen, result = set(), []
        while pending and len(result) < limit:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            thought = self.get(*key)
            if thought is None:
                raise ValueError('missing pinned thought version')
            result.append(thought)
            pending.extend((basis['thought_id'], basis['version']) for basis in thought.bases if 'thought_id' in basis)
        return {'thoughts': result, 'remaining': [{'thought_id': key[0], 'version': key[1]} for key in pending if key not in seen]}
