"""Append-only assertions and their evidence links in local SQLite."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from math import isfinite
from dataclasses import dataclass
from datetime import date, datetime, timezone
from time import monotonic, sleep
from typing import Any, Iterable, Iterator, Mapping

_BACKGROUND_WRITE_RETRY = ContextVar('memory_background_write_retry',default=None)
_LOG = logging.getLogger(__name__)
_BUSY_LOG_DELAY_SECONDS = .25


class BackgroundWriteCancelled(RuntimeError):
    """The owned background lane stopped before entering another write body."""


@dataclass
class _WriteRetryPolicy:
    is_cancelled: Any
    max_wait_seconds: float
    poll_seconds: float
    deadline: float | None = None
    last_error: Any = None
    has_warned: bool = False


@contextmanager
def background_write_retry(*,is_cancelled=None,max_wait_seconds=30,poll_seconds=.05):
    """Thread/context-local admission only; foreground default remains unchanged.

    A busy episode survives into failed-cleanup so catching the first refusal
    cannot silently reset its time budget. Successful BEGIN clears that episode.
    """
    if any(type(value) not in (int,float) or not isfinite(value) or value<=0 for value in (max_wait_seconds,poll_seconds)) or (is_cancelled is not None and not callable(is_cancelled)):
        raise ValueError('positive background retry intervals and callable cancellation required')
    policy=_WriteRetryPolicy(is_cancelled or (lambda:False),max_wait_seconds,poll_seconds)
    token=_BACKGROUND_WRITE_RETRY.set(policy)
    try:yield
    finally:_BACKGROUND_WRITE_RETRY.reset(token)


def is_sqlite_busy(error):
    code=getattr(error,'sqlite_errorcode',None)
    return isinstance(error,sqlite3.OperationalError) and code is not None and code & 0xff == sqlite3.SQLITE_BUSY


def _check_write_wait(policy):
    if policy.is_cancelled():raise BackgroundWriteCancelled('background write admission cancelled')
    if policy.deadline is not None and monotonic()>=policy.deadline:
        _LOG.error('Background SQLite write retry exhausted')
        raise policy.last_error


def _begin_transaction(connection,write):
    policy=_BACKGROUND_WRITE_RETRY.get()
    if not write or policy is None:
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        return
    _check_write_wait(policy)
    original=connection.execute('PRAGMA busy_timeout').fetchone()[0]
    connection.execute('PRAGMA busy_timeout=0')
    try:
        while True:
            _check_write_wait(policy)
            try:
                connection.execute('BEGIN IMMEDIATE')
                policy.deadline=None;policy.last_error=None;policy.has_warned=False
                return
            except sqlite3.OperationalError as error:
                if not is_sqlite_busy(error):raise
                if policy.deadline is None:policy.deadline=monotonic()+policy.max_wait_seconds
                policy.last_error=error
                _check_write_wait(policy)
                elapsed=policy.max_wait_seconds-(policy.deadline-monotonic())
                if not policy.has_warned and elapsed>=_BUSY_LOG_DELAY_SECONDS:
                    _LOG.warning('Background SQLite write deferred: writer busy');policy.has_warned=True
                sleep(min(policy.poll_seconds,max(0,policy.deadline-monotonic())))
    finally:
        # Restore before yielding any SQL/model body, also on refusal/cancellation.
        connection.execute('PRAGMA busy_timeout='+str(original))


_CURRENT_REVISIONS = object()


@dataclass(frozen=True)
class Assertion:
    assertion_id: str
    text: str
    actor: str
    source: str
    episode: str
    valid_at: str | None
    recorded_at: str
    scope: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Edge:
    edge_id: str
    from_id: str
    to_id: str
    kind: str
    recorded_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchHit:
    assertion: Assertion
    score: float


_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS memory_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL
);
INSERT INTO memory_state (singleton, version) VALUES (1, 0)
    ON CONFLICT(singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS memory_assertions (
    assertion_id TEXT PRIMARY KEY NOT NULL,
    text TEXT NOT NULL,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    episode TEXT NOT NULL,
    valid_at TEXT,
    recorded_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_assertion_scope_time
    ON memory_assertions(scope, valid_at, recorded_at);

CREATE TABLE IF NOT EXISTS memory_edges (
    edge_id TEXT PRIMARY KEY NOT NULL,
    from_id TEXT NOT NULL REFERENCES memory_assertions(assertion_id),
    to_id TEXT NOT NULL REFERENCES memory_assertions(assertion_id),
    kind TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_edge_from ON memory_edges(from_id);
CREATE INDEX IF NOT EXISTS memory_edge_to ON memory_edges(to_id);

-- A scope generation is work even before its first view exists. Source jobs and
-- immutable provider responses cannot own this coalesced recovery boundary.
CREATE TABLE IF NOT EXISTS memory_derived_work (
    scope TEXT PRIMARY KEY,
    desired_frontier TEXT NOT NULL,
    completed_frontier TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    lease_token TEXT,
    lease_until TEXT,
    checkpoint TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    updated_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    next_wake_at TEXT,
    turn INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    managed_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS memory_derived_ready
    ON memory_derived_work(status, available_at, updated_at);
DROP TRIGGER IF EXISTS memory_derived_assertion_insert;
CREATE TRIGGER memory_derived_assertion_insert
AFTER INSERT ON memory_assertions BEGIN
    INSERT INTO memory_derived_work(scope, desired_frontier, updated_at, available_at)
    VALUES(new.scope, json_object('generation',1,'assertion_watermark',(SELECT coalesce(max(rowid),0) FROM memory_assertions),
                         'edge_watermark',(SELECT coalesce(max(rowid),0) FROM memory_edges)),
           strftime('%Y-%m-%dT%H:%M:%f+00:00','now'), strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
    ON CONFLICT(scope) DO UPDATE SET
        desired_frontier=json_set(desired_frontier,'$.generation',coalesce(json_extract(desired_frontier,'$.generation'),0)+1,
            '$.assertion_watermark',(SELECT coalesce(max(rowid),0) FROM memory_assertions),
            '$.edge_watermark',(SELECT coalesce(max(rowid),0) FROM memory_edges)),
        status=CASE WHEN status='running' THEN status ELSE 'pending' END,
        error=NULL, error_count=0, available_at=excluded.available_at;
END;
DROP TRIGGER IF EXISTS memory_derived_edge_insert;
CREATE TRIGGER memory_derived_edge_insert
AFTER INSERT ON memory_edges BEGIN
    INSERT INTO memory_derived_work(scope, desired_frontier, updated_at, available_at)
    SELECT DISTINCT scope, json_object('generation',1,'assertion_watermark',(SELECT coalesce(max(rowid),0) FROM memory_assertions),
                         'edge_watermark',(SELECT coalesce(max(rowid),0) FROM memory_edges)),
           strftime('%Y-%m-%dT%H:%M:%f+00:00','now'), strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
    FROM memory_assertions WHERE assertion_id IN(new.from_id,new.to_id)
    ON CONFLICT(scope) DO UPDATE SET
        desired_frontier=json_set(desired_frontier,'$.generation',coalesce(json_extract(desired_frontier,'$.generation'),0)+1,
            '$.assertion_watermark',(SELECT coalesce(max(rowid),0) FROM memory_assertions),
            '$.edge_watermark',(SELECT coalesce(max(rowid),0) FROM memory_edges)),
        status=CASE WHEN status='running' THEN status ELSE 'pending' END,
        error=NULL, error_count=0, available_at=excluded.available_at;
END;
INSERT OR IGNORE INTO memory_derived_work(scope,desired_frontier,updated_at,available_at)
SELECT DISTINCT scope, json_object('generation',1,'assertion_watermark',(SELECT coalesce(max(rowid),0) FROM memory_assertions),
                     'edge_watermark',(SELECT coalesce(max(rowid),0) FROM memory_edges)),
       strftime('%Y-%m-%dT%H:%M:%f+00:00','now'), strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
FROM memory_assertions;

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    text, content='memory_assertions', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS memory_assertion_insert
AFTER INSERT ON memory_assertions BEGIN
    INSERT INTO memory_fts(rowid, text) VALUES (new.rowid, new.text);
    UPDATE memory_state SET version = version + 1 WHERE singleton = 1;
END;
CREATE TRIGGER IF NOT EXISTS memory_edge_insert
AFTER INSERT ON memory_edges BEGIN
    UPDATE memory_state SET version = version + 1 WHERE singleton = 1;
END;
COMMIT;
"""


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _timestamp(value: str | date) -> str:
    if isinstance(value, str):
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        value = datetime.fromisoformat(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be an ISO date or datetime")
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _metadata(value: Mapping[str, Any] | None) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
    metadata = dict(value)
    interpretation = metadata.get("interpretation")
    if isinstance(interpretation, Mapping) and interpretation.get("interpreted_at") is not None:
        metadata["interpretation"] = {**interpretation,
            "interpreted_at": _timestamp(interpretation["interpreted_at"])}
    return json.dumps(metadata, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    fields = dict(row)
    fields["metadata"] = json.loads(fields["metadata"])
    return fields


def _enable_wal(connection: sqlite3.Connection) -> None:
    # A concurrent journal-mode change can report SQLITE_BUSY without waiting.
    deadline = monotonic() + 5
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL").fetchone()
            return
        except sqlite3.OperationalError as error:
            if str(error) != "database is locked" or monotonic() >= deadline:
                raise
            sleep(0.01)


def _visibility(
    aliases: tuple[str, ...],
    valid_as_of: str | date | None,
    recorded_as_of: str | date | None,
    scopes: Iterable[str] | str | None,
    include_unknown_valid: bool = False,
) -> tuple[list[str], list[str]]:
    clauses = []
    values = []
    if isinstance(scopes, str):
        scopes = (scopes,)
    if scopes is not None:
        scopes = tuple(dict.fromkeys(_required(scope, "scope") for scope in scopes))
        if not scopes:
            return ["0"], []
    for alias in aliases:
        if valid_as_of is not None:
            cutoff = f"{alias}.valid_at <= ?"
            clauses.append(f"({cutoff} OR {alias}.valid_at IS NULL)" if include_unknown_valid else cutoff)
            values.append(_timestamp(valid_as_of))
        if recorded_as_of is not None:
            clauses.append(f"{alias}.recorded_at <= ?")
            values.append(_timestamp(recorded_as_of))
            interpreted_at = f"json_extract({alias}.metadata, '$.interpretation.interpreted_at')"
            clauses.append(f"({interpreted_at} IS NULL OR {interpreted_at} <= ?)")
            values.append(_timestamp(recorded_as_of))
        if scopes is not None:
            placeholders = ", ".join("?" for _ in scopes)
            clauses.append(f"{alias}.scope IN ({placeholders})")
            values.extend(scopes)
    return clauses, values


class MemoryStore:
    """A local store with one connection and inclusive as-of cutoffs.

    Timestamp arguments without an offset denote UTC. An assertion's valid time
    identifies when it applies, while its recorded time identifies when it became
    known to the store. A valid-time cutoff excludes assertions with unknown valid
    time. An omitted cutoff or scope filter leaves that axis open.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = os.fspath(path)
        self._connection = sqlite3.connect(self.path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._writing = False
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            _enable_wal(self._connection)
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.executescript(_SCHEMA)
            columns = {row[1] for row in self._connection.execute('PRAGMA table_info(memory_derived_work)')}
            for column in ('turn','error_count'):
                if column not in columns:
                    self._connection.execute(f'ALTER TABLE memory_derived_work ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
            if 'managed_ids' not in columns:
                self._connection.execute("ALTER TABLE memory_derived_work ADD COLUMN managed_ids TEXT NOT NULL DEFAULT '[]'")
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Share a snapshot or publish under the store's SQLite write lock."""
        connection = self._connection
        if connection.in_transaction:
            if write and not self._writing:
                raise RuntimeError("start a write transaction before appending or publishing")
            connection.execute("SAVEPOINT memory_nested")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK TO memory_nested")
                raise
            finally:
                connection.execute("RELEASE memory_nested")
            return

        _begin_transaction(connection,write)
        self._writing = write
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._writing = False

    @property
    def version(self) -> int:
        row = self._connection.execute(
            "SELECT version FROM memory_state WHERE singleton = 1"
        ).fetchone()
        return row["version"]

    def scope_version(
        self,
        scopes: Iterable[str] | str | None = None,
        *,
        follow_kinds: Iterable[str] | str = (),
        root_ids: Iterable[str] | str = (),
        active_at: str | date | None = None,
    ) -> int:
        """Count assertions and links reachable from scopes and explicit roots."""
        with self.transaction():
            from .source_revision import selection
            inactive = selection(self._connection, active_at).inactive_ids
            if scopes is None and active_at is None and not inactive:
                return self.version
            if isinstance(follow_kinds, str):
                follow_kinds = (follow_kinds,)
            kinds = tuple(dict.fromkeys(_required(kind, "kind") for kind in follow_kinds))
            scope_clauses, values = _visibility(("a",), None, None, scopes)
            where = " AND ".join(scope_clauses) or "1"
            if isinstance(root_ids, str):
                root_ids = (root_ids,)
            roots = tuple(dict.fromkeys(_required(root, "root ID") for root in root_ids))
            if roots:
                placeholders = ", ".join("?" for _ in roots)
                where = f"({where}) OR a.assertion_id IN ({placeholders})"
                values.extend(roots)
            expansion = ""
            if kinds:
                placeholders = ", ".join("?" for _ in kinds)
                expansion = f"""UNION
                    SELECT CASE WHEN e.from_id = v.id THEN e.to_id ELSE e.from_id END
                    FROM visible v JOIN eligible_edges e ON e.from_id = v.id OR e.to_id = v.id
                    WHERE e.kind IN ({placeholders})"""
            if active_at is None:
                eligible_assertions = "SELECT * FROM memory_assertions"
                eligible_edges = "SELECT * FROM memory_edges"
                cutoff_parameters = ()
            else:
                active = _timestamp(active_at)
                eligible_assertions = """SELECT * FROM memory_assertions
                    WHERE recorded_at <= ? AND (valid_at IS NULL OR valid_at <= ?)
                      AND (json_extract(metadata, '$.interpretation.interpreted_at') IS NULL
                           OR json_extract(metadata, '$.interpretation.interpreted_at') <= ?)"""
                eligible_edges = """SELECT e.* FROM memory_edges e
                    JOIN eligible_assertions a ON a.assertion_id = e.from_id
                    JOIN eligible_assertions b ON b.assertion_id = e.to_id
                    WHERE e.recorded_at <= ?"""
                cutoff_parameters = (active, active, active, active)
            if inactive:
                placeholders = "SELECT value FROM json_each(?)"
                eligible_assertions = "SELECT * FROM (" + eligible_assertions + ") WHERE assertion_id NOT IN (" + placeholders + ")"
                # The assertion CTE precedes the edge cutoff parameter.
                cutoff_parameters = (*cutoff_parameters[:3], json.dumps(sorted(inactive)), *cutoff_parameters[3:])
                if active_at is None:
                    eligible_edges = "SELECT e.* FROM memory_edges e JOIN eligible_assertions a ON a.assertion_id=e.from_id JOIN eligible_assertions b ON b.assertion_id=e.to_id"
            row = self._connection.execute(
                f"""WITH RECURSIVE eligible_assertions AS ({eligible_assertions}),
                    eligible_edges AS ({eligible_edges}), visible(id) AS (
                        SELECT a.assertion_id FROM eligible_assertions a WHERE {where}
                        {expansion}
                    )
                    SELECT (SELECT count(*) FROM visible) +
                           (SELECT count(*) FROM eligible_edges e
                            JOIN visible origin ON e.from_id = origin.id
                            JOIN visible destination ON e.to_id = destination.id) AS version""",
                (*cutoff_parameters, *values, *kinds),
            ).fetchone()
            return row["version"]

    def append_assertion(
        self,
        *,
        assertion_id: str,
        text: str,
        actor: str,
        source: str,
        episode: str,
        recorded_at: str | date,
        valid_at: str | date | None = None,
        scope: str = "default",
        metadata: Mapping[str, Any] | None = None,
    ) -> Assertion:
        values = (
            _required(assertion_id, "assertion_id"),
            _required(text, "text"),
            _required(actor, "actor"),
            _required(source, "source"),
            _required(episode, "episode"),
            _timestamp(valid_at) if valid_at is not None else None,
            _timestamp(recorded_at),
            _required(scope, "scope"),
            _metadata(metadata),
        )
        with self.transaction(write=True) as connection:
            connection.execute(
                """INSERT INTO memory_assertions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(assertion_id) DO NOTHING""",
                values,
            )
            row = connection.execute(
                "SELECT * FROM memory_assertions WHERE assertion_id = ?", (assertion_id,)
            ).fetchone()
            if tuple(row) != values:
                raise ValueError(f"assertion ID {assertion_id!r} already has different content")
        return Assertion(**_decode(row))

    def append_edge(
        self,
        *,
        edge_id: str,
        from_id: str,
        to_id: str,
        kind: str,
        recorded_at: str | date,
        metadata: Mapping[str, Any] | None = None,
    ) -> Edge:
        values = (
            _required(edge_id, "edge_id"),
            _required(from_id, "from_id"),
            _required(to_id, "to_id"),
            _required(kind, "kind"),
            _timestamp(recorded_at),
            _metadata(metadata),
        )
        with self.transaction(write=True) as connection:
            connection.execute(
                """INSERT INTO memory_edges VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(edge_id) DO NOTHING""",
                values,
            )
            row = connection.execute(
                "SELECT * FROM memory_edges WHERE edge_id = ?", (edge_id,)
            ).fetchone()
            if tuple(row) != values:
                raise ValueError(f"edge ID {edge_id!r} already has different content")
        return Edge(**_decode(row))

    def get_assertion(
        self,
        assertion_id: str,
        *,
        valid_as_of: str | date | None = None,
        recorded_as_of: str | date | None = None,
        scopes: Iterable[str] | str | None = None,
    ) -> Assertion | None:
        clauses, values = _visibility(("a",), valid_as_of, recorded_as_of, scopes)
        clauses.append("a.assertion_id = ?")
        values.append(assertion_id)
        row = self._connection.execute(
            "SELECT a.* FROM memory_assertions a WHERE " + " AND ".join(clauses), values
        ).fetchone()
        return Assertion(**_decode(row)) if row is not None else None

    def get_edge(self, edge_id: str) -> Edge | None:
        row = self._connection.execute("SELECT * FROM memory_edges WHERE edge_id = ?", (edge_id,)).fetchone()
        return Edge(**_decode(row)) if row is not None else None

    def _current_filter(self, aliases, recorded_as_of, source_revision_watermark):
        from .source_revision import exclusion, _CURRENT
        if source_revision_watermark is _CURRENT_REVISIONS:
            source_revision_watermark = _CURRENT
        clauses, values = [], []
        for alias in aliases:
            extra, arguments = exclusion(self._connection, alias, recorded_as_of, source_revision_watermark)
            clauses.extend(extra); values.extend(arguments)
        return clauses, values

    def list_assertions(
        self,
        *,
        valid_as_of: str | date | None = None,
        recorded_as_of: str | date | None = None,
        scopes: Iterable[str] | str | None = None,
        include_unknown_valid: bool = False,
        source_revision_watermark=_CURRENT_REVISIONS,
    ) -> list[Assertion]:
        with self.transaction():
            clauses, values = _visibility(("a",), valid_as_of, recorded_as_of, scopes, include_unknown_valid)
            extra, arguments = self._current_filter(("a",), recorded_as_of, source_revision_watermark)
            clauses.extend(extra); values.extend(arguments)
            where = " AND ".join(clauses) or "1"
            rows = self._connection.execute(
                f"""SELECT a.* FROM memory_assertions a WHERE {where}
                    ORDER BY a.valid_at IS NULL, a.valid_at, a.recorded_at, a.assertion_id""",
                values,
            )
            return [Assertion(**_decode(row)) for row in rows]

    def list_edges(
        self,
        assertion_id: str | None = None,
        *,
        valid_as_of: str | date | None = None,
        recorded_as_of: str | date | None = None,
        scopes: Iterable[str] | str | None = None,
        include_unknown_valid: bool = False,
        source_revision_watermark=_CURRENT_REVISIONS,
    ) -> list[Edge]:
        """Return directed links whose endpoints are both visible."""
        with self.transaction():
            clauses, values = _visibility(("origin", "destination"), valid_as_of, recorded_as_of, scopes, include_unknown_valid)
            extra, arguments = self._current_filter(("origin", "destination"), recorded_as_of, source_revision_watermark)
            clauses.extend(extra); values.extend(arguments)
            if assertion_id is not None:
                clauses.append("(e.from_id = ? OR e.to_id = ?)")
                values.extend((assertion_id, assertion_id))
            if recorded_as_of is not None:
                clauses.append("e.recorded_at <= ?")
                values.append(_timestamp(recorded_as_of))
            where = " AND ".join(clauses) or "1"
            rows = self._connection.execute(
                f"""SELECT e.* FROM memory_edges e
                    JOIN memory_assertions origin ON origin.assertion_id = e.from_id
                    JOIN memory_assertions destination ON destination.assertion_id = e.to_id
                    WHERE {where}
                    ORDER BY e.recorded_at, e.edge_id""",
                values,
            )
            return [Edge(**_decode(row)) for row in rows]

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        valid_as_of: str | date | None = None,
        recorded_as_of: str | date | None = None,
        scopes: Iterable[str] | str | None = None,
        include_unknown_valid: bool = False,
        assertion_watermark: int | None = None,
        source_revision_watermark=_CURRENT_REVISIONS,
    ) -> list[SearchHit]:
        """Find lexical matches with FTS5 and rank by distinct matching terms.

        The score is the negative number of matching query terms, with smaller
        scores first. Other documents do not affect the score.
        """
        with self.transaction():
            if not isinstance(limit, int) or limit < 0:
                raise ValueError("limit must be a nonnegative integer")
            tokens = dict.fromkeys(re.findall(r"[^\W_]+", query.lower()))
            if not tokens or limit == 0:
                return []
            clauses, values = _visibility(("a",), valid_as_of, recorded_as_of, scopes, include_unknown_valid)
            extra, arguments = self._current_filter(("a",), recorded_as_of, source_revision_watermark)
            clauses.extend(extra); values.extend(arguments)
            if assertion_watermark is not None:
                clauses.append("a.rowid <= ?")
                values.append(assertion_watermark)
            where = " AND ".join(clauses) or "1"
            term_rows = ", ".join("(?)" for _ in tokens)
            terms = tuple(f'"{token}"' for token in tokens)
            rows = self._connection.execute(
                f"""WITH terms(term) AS (VALUES {term_rows})
                    SELECT a.*, CAST(-count(*) AS REAL) AS score
                    FROM terms CROSS JOIN memory_fts(terms.term)
                    JOIN memory_assertions a ON a.rowid = memory_fts.rowid
                    WHERE {where}
                    GROUP BY a.assertion_id
                    ORDER BY score, a.assertion_id
                    LIMIT ?""",
                (*terms, *values, limit),
            )
            hits = []
            for row in rows:
                fields = _decode(row)
                score = fields.pop("score")
                hits.append(SearchHit(Assertion(**fields), score))
            return hits
