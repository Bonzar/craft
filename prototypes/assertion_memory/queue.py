"""Persist raw capture and its background work in the same SQLite transaction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from uuid import uuid4

from .capture import CaptureInput, RawEvent, declared_kind
from .storage import Assertion, MemoryStore, _timestamp


@dataclass(frozen=True)
class Job:
    id: str
    assertion_id: str | None
    event_id: str | None
    status: str
    phase: str
    attempts: int
    updated_at: str
    available_at: str
    error: str | None
    candidates: list[dict]
    proposals: list[dict]
    refreshed_views: dict[str, int]
    extracted_ids: list[str]


@dataclass(frozen=True)
class _Claim:
    job: Job
    token: str
    refresh_fairness: bool = False


class LeaseLost(RuntimeError):
    """Another worker can now recover this job."""


def _lane_filter(lane):
    if lane not in ('all','source','maintenance'):
        raise ValueError('worker lane must be all, source or maintenance')
    return {'all':'1', 'source':"(phase IN ('extract','audit','link') OR (phase='done' AND status='waiting_model'))",
            'maintenance':"phase='refresh'"}[lane]


class CaptureQueue:
    def __init__(
        self, store: MemoryStore, *, lease_seconds: float = 300,
        max_attempts: int = 3, clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds <= 0 or max_attempts < 1:
            raise ValueError("positive lease and retry limits are required")
        self.store = store
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        with store.transaction(write=True) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_raw_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_event_positions (
                    event_id TEXT PRIMARY KEY REFERENCES memory_raw_events(id),
                    position INTEGER NOT NULL CHECK (position >= 0)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_extraction_runs (
                    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES memory_raw_events(id),
                    fingerprint TEXT NOT NULL, completed_at TEXT NOT NULL, result TEXT NOT NULL,
                    UNIQUE(event_id, fingerprint)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_capture_jobs (
                    id TEXT PRIMARY KEY,
                    assertion_id TEXT REFERENCES memory_assertions(assertion_id),
                    event_id TEXT REFERENCES memory_raw_events(id),
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    error TEXT,
                    lease_token TEXT,
                    lease_until TEXT,
                    candidates TEXT NOT NULL DEFAULT '[]',
                    proposals TEXT NOT NULL DEFAULT '[]',
                    refreshed_views TEXT NOT NULL DEFAULT '{}',
                    extracted_ids TEXT NOT NULL DEFAULT '[]',
                    waiting_model INTEGER NOT NULL DEFAULT 0,
                    CHECK ((assertion_id IS NULL) != (event_id IS NULL))
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS memory_capture_ready
                ON memory_capture_jobs(status, available_at, id)
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_source_draft_checkpoints (
                    job_id TEXT PRIMARY KEY REFERENCES memory_capture_jobs(id) ON DELETE CASCADE,
                    progress TEXT NOT NULL
                )
            """)
            from .source_reprocessing import schema
            schema(connection)
            from .source_admission import install
            install(connection)

    def _scheduler_schema(self, connection) -> None:
        connection.execute("CREATE TABLE IF NOT EXISTS memory_queue_schedule "
                           "(id INTEGER PRIMARY KEY CHECK(id=1), source_streak INTEGER NOT NULL)")
        connection.execute("INSERT OR IGNORE INTO memory_queue_schedule(id,source_streak) VALUES (1, 0)")
        if 'last_source_phase' not in {row[1] for row in connection.execute('PRAGMA table_info(memory_queue_schedule)')}:
            # The existing streak cannot independently alternate three source
            # phases while maintenance updates its own last-kind cursor.
            connection.execute("ALTER TABLE memory_queue_schedule ADD COLUMN last_source_phase TEXT NOT NULL DEFAULT 'link'")
        connection.execute("CREATE TABLE IF NOT EXISTS memory_refresh_generations "
                           "(request_key TEXT PRIMARY KEY, response TEXT NOT NULL)")

    def _maintenance_choice(self, available, source_batch_limit, *, with_model, independent=False):
        with self.store.transaction(write=True) as c:
            self._scheduler_schema(c)
            c.execute('CREATE TABLE IF NOT EXISTS memory_refresh_schedule(id INTEGER PRIMARY KEY CHECK(id=1),last_kind TEXT NOT NULL)')
            c.execute("INSERT OR IGNORE INTO memory_refresh_schedule VALUES(1,'derived')")
            streak=c.execute('SELECT source_streak FROM memory_queue_schedule WHERE id=1').fetchone()[0]
            if not independent and streak<source_batch_limit and self._ready_sources(with_model=with_model):return None
            last=c.execute('SELECT last_kind FROM memory_refresh_schedule WHERE id=1').fetchone()[0]
            kinds=('source','derived','editorial') if independent else ('audit','source','derived','editorial')
            start=(kinds.index(last)+1)%len(kinds) if last in kinds else 0
            return next((kinds[(start+i)%len(kinds)] for i in range(len(kinds)) if kinds[(start+i)%len(kinds)] in available),None)

    def _audit_turn(self, source_batch_limit: int) -> bool:
        with self.store.transaction(write=True) as connection:
            self._scheduler_schema(connection)
            connection.execute("CREATE TABLE IF NOT EXISTS memory_refresh_schedule "
                               "(id INTEGER PRIMARY KEY CHECK(id=1), last_kind TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO memory_refresh_schedule VALUES (1, 'derived')")
            streak=connection.execute('SELECT source_streak FROM memory_queue_schedule WHERE id=1').fetchone()[0]
            kind=connection.execute('SELECT last_kind FROM memory_refresh_schedule WHERE id=1').fetchone()[0]
            ready=connection.execute("SELECT 1 FROM memory_capture_jobs WHERE phase='audit' AND "
                "((status='pending' AND available_at<=?) OR (status='running' AND lease_until<=?)) LIMIT 1",
                (self._time(),self._time())).fetchone()
            return bool(streak>=source_batch_limit and kind!='audit' and ready)

    def _derived_turn(self, source_batch_limit: int) -> bool:
        with self.store.transaction(write=True) as connection:
            self._scheduler_schema(connection)
            connection.execute("CREATE TABLE IF NOT EXISTS memory_refresh_schedule "
                               "(id INTEGER PRIMARY KEY CHECK(id=1), last_kind TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO memory_refresh_schedule VALUES (1, 'derived')")
            streak = connection.execute("SELECT source_streak FROM memory_queue_schedule WHERE id=1").fetchone()[0]
            kind = connection.execute("SELECT last_kind FROM memory_refresh_schedule WHERE id=1").fetchone()[0]
            legacy = connection.execute("""SELECT 1 FROM memory_capture_jobs WHERE phase='refresh' AND
                ((status='pending' AND available_at<=?) OR (status='running' AND lease_until<=?)) LIMIT 1""",
                (self._time(),self._time())).fetchone()
            return streak >= source_batch_limit and (kind != 'derived' or legacy is None)

    def _refresh_turn_taken(self, kind: str) -> None:
        with self.store.transaction(write=True) as connection:
            self._scheduler_schema(connection)
            connection.execute("CREATE TABLE IF NOT EXISTS memory_refresh_schedule "
                               "(id INTEGER PRIMARY KEY CHECK(id=1), last_kind TEXT NOT NULL)")
            connection.execute("INSERT OR REPLACE INTO memory_refresh_schedule VALUES (1, ?)", (kind,))
            connection.execute("UPDATE memory_queue_schedule SET source_streak=0 WHERE id=1")

    def capture(self, item: CaptureInput) -> Assertion:
        assertion_id = uuid4().hex if item.assertion_id is None else item.assertion_id
        with self.store.transaction(write=True) as connection:
            existing = self.store.get_assertion(assertion_id)
            now = self._time()
            recorded_at = item.recorded_at
            if recorded_at is None:
                recorded_at = existing.recorded_at if existing else now
            assertion = self.store.append_assertion(
                assertion_id=assertion_id, text=item.text, actor=item.actor,
                source=item.source, episode=item.episode, scope=item.scope,
                valid_at=item.valid_at, recorded_at=recorded_at, metadata=item.metadata,
            )
            connection.execute("""
                INSERT INTO memory_capture_jobs(id, assertion_id, status, phase, updated_at, available_at)
                VALUES (?, ?, 'pending', 'link', ?, ?)
                ON CONFLICT(id) DO NOTHING
            """, (assertion.assertion_id, assertion.assertion_id, now, now))
            if self.get(assertion_id).assertion_id != assertion_id:
                raise ValueError(f"capture job ID {assertion_id!r} belongs to a raw event")
        return assertion

    def capture_event(
        self, session_id: str, turn_id: str, prompt: str, *, source: str,
        scope: str = "default", actor: str = "user", metadata: Mapping[str, Any] | None = None,
        await_origin: bool = False, message_id: str | None = None, position: int | None = None,
        recorded_at: str | datetime | None = None,
    ) -> RawEvent:
        """Save raw intake; recorded_at optionally preserves authenticated source availability."""
        fields = (session_id, turn_id, prompt, actor, source, scope)
        if not all(isinstance(value, str) and value.strip() for value in fields):
            raise ValueError("event identity, prompt, actor, source and scope must be nonempty")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("event metadata must be a mapping")
        from .source_message_clock import normalize
        metadata = normalize(metadata, source=source)
        from .dialogue import source_role
        role = source_role(actor, metadata)
        if type(await_origin) is not bool:
            raise TypeError("await_origin must be a boolean")
        if message_id is not None and (not isinstance(message_id, str) or not message_id.strip()):
            raise ValueError("message_id must be a nonempty string")
        if position is not None and (type(position) is not int or position < 0):
            raise ValueError("position must be a nonnegative integer")
        identity = (session_id, turn_id, prompt)
        if message_id is not None or role in ("assistant", "tool"):
            identity += (actor, message_id or "")
        event_id = _identity("event", *identity)
        status = "waiting_origin" if await_origin else "waiting_model"
        with self.store.transaction(write=True) as connection:
            existing = self.get_event(event_id)
            previous_job = self.get(event_id)
            previous_position = connection.execute(
                "SELECT position FROM memory_event_positions WHERE event_id = ?", (event_id,),
            ).fetchone()
            now = self._time()
            recorded_at = (_timestamp(recorded_at) if recorded_at is not None
                           else existing.recorded_at if existing else now)
            values = (event_id, *fields, recorded_at, _json(dict(metadata or {})))
            connection.execute("""
                INSERT INTO memory_raw_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
            """, values)
            row = connection.execute("SELECT * FROM memory_raw_events WHERE id = ?", (event_id,)).fetchone()
            if tuple(row) != values:
                raise ValueError(f"raw event ID {event_id!r} already has different provenance")
            if position is not None:
                connection.execute("INSERT INTO memory_event_positions VALUES (?, ?) ON CONFLICT DO NOTHING",
                                   (event_id, position))
                previous = connection.execute("SELECT position FROM memory_event_positions WHERE event_id = ?",
                                              (event_id,)).fetchone()[0]
                if previous != position:
                    raise ValueError("raw event has a different immutable dialogue position")
            connection.execute("""
                INSERT INTO memory_capture_jobs(id, event_id, status, phase, updated_at, available_at)
                VALUES (?, ?, ?, 'extract', ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at,
                    available_at = excluded.available_at
                WHERE memory_capture_jobs.event_id = excluded.event_id
                  AND memory_capture_jobs.status = 'waiting_origin' AND excluded.status != 'waiting_origin'
            """, (event_id, event_id, status, now, now))
            if self.get(event_id).event_id != event_id:
                raise ValueError(f"capture job ID {event_id!r} belongs to an assertion")
            if (existing is None or (position is not None and previous_position is None)
                    or (previous_job is not None and previous_job.status == 'waiting_origin' and not await_origin)):
                from .source_reprocessing import schema, changed
                schema(connection)
                changed(connection, event_id, now)
            return _event(row)

    def freeze_input(self, event_id: str):
        from .source_reprocessing import freeze
        return freeze(self,event_id)

    def _freeze_claim_input(self, claim: _Claim):
        from .source_reprocessing import freeze, schema, PROTOCOL
        with self.store.transaction(write=True) as connection:
            self._assert_owned(connection,claim)
            schema(connection)
            frozen=freeze(self,claim.job.event_id,connection)
            from .source_omission import claim_requests
            from dataclasses import replace
            frozen=replace(frozen,requests=claim_requests(self,connection,frozen,claim))
            connection.execute("INSERT INTO memory_source_audits VALUES(?,?,?,NULL,'running',NULL) "
                'ON CONFLICT(event_id) DO UPDATE SET protocol=excluded.protocol,'
                "desired_input=excluded.desired_input,status='running',reason=NULL",
                (frozen.event.id,PROTOCOL,frozen.fingerprint))
            return frozen

    def request_source_audit(self, event_id: str, gap_identity: str) -> str:
        from .source_reprocessing import request_audit
        return request_audit(self,event_id,gap_identity)

    def _maintenance_tick(self):
        from .source_reprocessing import tick
        tick(self)

    def get_event(self, event_id: str) -> RawEvent | None:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_raw_events WHERE id = ?", (event_id,),
            ).fetchone()
        return _event(row) if row else None

    def list_events(self, session_id: str | None = None) -> list[RawEvent]:
        with self.store.transaction() as connection:
            rows = connection.execute("""
                SELECT * FROM memory_raw_events WHERE session_id = ? OR ? IS NULL
                ORDER BY recorded_at, id
            """, (session_id, session_id)).fetchall()
        return [_event(row) for row in rows]

    def context_events(self, event_id: str) -> list[RawEvent]:
        """Use authenticated transcript order, falling back to intake order for raw APIs."""
        with self.store.transaction() as connection:
            target = connection.execute("""
                SELECT e.rowid, e.session_id, p.position FROM memory_raw_events e
                LEFT JOIN memory_event_positions p ON p.event_id = e.id WHERE e.id = ?
            """, (event_id,)).fetchone()
            if target is None:
                raise ValueError(f"unknown raw event {event_id!r}")
            if target["position"] is None:
                order, cutoff = "e.rowid", target["rowid"]
            else:
                order, cutoff = "p.position", target["position"]
            rows = connection.execute(f"""
                SELECT e.* FROM memory_raw_events e
                JOIN memory_capture_jobs j ON j.event_id = e.id
                LEFT JOIN memory_event_positions p ON p.event_id = e.id
                WHERE e.session_id = ? AND {order} <= ? AND j.status != 'waiting_origin'
                ORDER BY {order}, e.rowid
            """, (target["session_id"], cutoff)).fetchall()
        return [_event(row) for row in rows]

    def resolve_reference(self, reference: Mapping[str, Any]) -> dict:
        """Resolve a source span while keeping its body out of stored assertions."""
        event = self.get_event(reference.get("event_id"))
        start, end = reference.get("start"), reference.get("end")
        if event is None or type(start) is not int or type(end) is not int:
            raise ValueError("evidence reference has no source or valid bounds")
        if not 0 <= start < end <= len(event.prompt):
            raise ValueError("evidence reference is outside its source")
        return {**dict(reference), "text": event.prompt[start:end], "source": event.source,
                "actor": event.actor}

    def context_order(self, event_id: str) -> dict:
        with self.store.transaction() as connection:
            target = connection.execute('SELECT e.session_id, p.position FROM memory_raw_events e '
                'LEFT JOIN memory_event_positions p ON p.event_id = e.id WHERE e.id = ?', (event_id,)).fetchone()
            if target is None:
                raise ValueError('unknown raw event')
            unpositioned = connection.execute('SELECT COUNT(*) FROM memory_raw_events e '
                'LEFT JOIN memory_event_positions p ON p.event_id = e.id '
                'JOIN memory_capture_jobs j ON j.event_id = e.id '
                "WHERE e.session_id = ? AND e.id != ? AND p.position IS NULL AND j.status != 'waiting_origin'",
                (target['session_id'], event_id)).fetchone()[0]
        positioned = target['position'] is not None
        return {'basis': 'transcript_position' if positioned else 'intake_order',
                'has_recorded_source_position': positioned,
                'excluded_unpositioned_event_count': unpositioned if positioned else 0}

    def extraction_history(self, event_id: str) -> list[dict]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT id, completed_at, result FROM memory_extraction_runs WHERE event_id = ? ORDER BY id",
                (event_id,),
            ).fetchall()
        return [{"id": row["id"], "completed_at": row["completed_at"], **json.loads(row["result"])} for row in rows]

    def _has_expired_claim(self, job_id: str, now: str) -> bool:
        with self.store.transaction() as db:
            return db.execute(
                "SELECT 1 FROM memory_capture_jobs WHERE id=? AND status='running' AND lease_until<=?",
                (job_id, now),
            ).fetchone() is not None

    def get(self, job_id: str) -> Job | None:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_capture_jobs WHERE id = ?", (job_id,),
            ).fetchone()
        return _job(row) if row else None

    def list(self, status: str | None = None) -> list[Job]:
        with self.store.transaction() as connection:
            rows = connection.execute("""
                SELECT * FROM memory_capture_jobs WHERE status = ? OR ? IS NULL
                ORDER BY available_at, id
            """, (status, status)).fetchall()
        return [_job(row) for row in rows]

    def retry(self, job_id: str) -> Job:
        with self.store.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM memory_capture_jobs WHERE id = ?", (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown capture job {job_id!r}")
            if row["status"] == "waiting_origin":
                raise ValueError("capture job is waiting for verified origin")
            now = self._time()
            if row["status"] == "running" and row["lease_until"] > now:
                raise ValueError("capture job is already running")
            phase = row["phase"]
            if phase == "done":
                phase = "extract" if row["event_id"] else "link"
            connection.execute("""
                UPDATE memory_capture_jobs
                SET status = 'pending', phase = ?, attempts = 0, error = NULL,
                    updated_at = ?, available_at = ?, lease_token = NULL, lease_until = NULL
                WHERE id = ?
            """, (phase, now, now, job_id))
            return self.get(job_id)

    def _source_order(self, connection):
        phases = ('extract','audit','link')
        last = connection.execute('SELECT last_source_phase FROM memory_queue_schedule WHERE id=1').fetchone()[0]
        start = (phases.index(last)+1) % len(phases) if last in phases else 0
        rotated = phases[start:] + phases[:start]
        return 'CASE phase ' + ' '.join("WHEN '"+phase+"' THEN "+str(index)
            for index,phase in enumerate(rotated)) + " WHEN 'done' THEN " + str(rotated.index('link')) + ' ELSE 3 END,available_at,id'

    def _claim(self, *, with_model: bool, source_first: bool = False, source_batch_limit: int = 32, maintenance_only: bool = False, maintenance_phase: str | None = None, lane: str = 'all') -> _Claim | None:
        lane_filter = _lane_filter(lane)
        with self.store.transaction(write=True) as connection:
            now = self._time()
            fairness = False
            ordering = "available_at, id"
            if source_first:
                self._scheduler_schema(connection)
                streak = connection.execute("SELECT source_streak FROM memory_queue_schedule WHERE id=1").fetchone()[0]
                fairness = streak >= source_batch_limit
                ordering = ("CASE WHEN phase IN ('refresh','audit') THEN -1 ELSE 0 END, " if fairness else "") + (
                    "CASE phase WHEN 'extract' THEN 0 WHEN 'audit' THEN 2 WHEN 'refresh' THEN 3 ELSE 1 END, available_at, id")
                connection.execute("""UPDATE memory_capture_jobs SET status='failed', error='Stage lease expired too many times',
                    lease_token=NULL, lease_until=NULL WHERE status='running' AND lease_until <= ? AND attempts >= ?""",
                    (now, self._max_attempts))
            if lane == 'source':
                self._scheduler_schema(connection)
                ordering = self._source_order(connection)
            row = connection.execute(f"""
                SELECT id, phase FROM memory_capture_jobs
                WHERE ((status = 'pending' AND available_at <= ?)
                    OR (status = 'running' AND lease_until <= ?)
                    OR (status = 'waiting_model' AND ?))
                  AND (phase NOT IN ('extract','audit') OR ?) AND (?=0 OR phase='audit') AND (? IS NULL OR phase=?)
                  AND {lane_filter}
                ORDER BY {ordering} LIMIT 1
            """, (now, now, with_model, with_model, maintenance_only,maintenance_phase,maintenance_phase)).fetchone()
            if row is None:
                return None
            job_id = row["id"]
            token = uuid4().hex
            if lane == 'source':
                phase = 'link' if row['phase'] == 'done' else row['phase']
                connection.execute('UPDATE memory_queue_schedule SET last_source_phase=? WHERE id=1',(phase,))
            if source_first:
                connection.execute("UPDATE memory_queue_schedule SET source_streak = "
                                   "CASE WHEN ? IN ('refresh','audit') THEN 0 ELSE source_streak + 1 END WHERE id=1", (row["phase"],))
            connection.execute("""
                UPDATE memory_capture_jobs
                SET status = 'running', attempts = CASE WHEN ? AND phase='done' THEN 1 ELSE attempts + 1 END, updated_at = ?,
                    phase = CASE WHEN phase = 'done' THEN 'link' ELSE phase END,
                    lease_token = ?, lease_until = ?
                WHERE id = ?
            """, (source_first, now, token, self._time(self._lease_seconds), job_id))
            from .policy_work import bind_claim
            bind_claim(connection,job_id,token,row['phase'])
            return _Claim(self.get(job_id), token, fairness and row["phase"] == "refresh")

    def _seconds_until_work(self, *, with_model: bool, lane: str = 'all') -> float | None:
        lane_filter = _lane_filter(lane)
        now = self._time()
        with self.store.transaction() as connection:
            deadline = connection.execute(f"""
                SELECT min(CASE status WHEN 'running' THEN lease_until
                                       WHEN 'waiting_model' THEN ?
                                       ELSE available_at END)
                FROM memory_capture_jobs
                WHERE (status IN ('pending', 'running') OR (status = 'waiting_model' AND ?))
                  AND (phase NOT IN ('extract','audit') OR ?)
                  AND {lane_filter}
            """, (now, with_model, with_model)).fetchone()[0]
            from .source_omission import actionable_pending
            if lane != 'maintenance' and with_model and actionable_pending(connection):return 0.0
        if deadline is None:
            return None
        return max(0.0, (datetime.fromisoformat(deadline) - datetime.fromisoformat(now)).total_seconds())

    def _assert_owned(self, connection, claim: _Claim) -> None:
        row = connection.execute("""
            SELECT 1 FROM memory_capture_jobs
            WHERE id = ? AND lease_token = ? AND lease_until > ?
        """, (claim.job.id, claim.token, self._time())).fetchone()
        if row is None:
            raise LeaseLost("capture job lease expired")

    def renew_claim(self, claim: _Claim) -> None:
        with self.store.transaction(write=True) as connection:
            self._assert_owned(connection, claim)
            connection.execute("UPDATE memory_capture_jobs SET lease_until = ? WHERE id = ?",
                               (self._time(self._lease_seconds), claim.job.id))

    def _save_links(
        self, connection, claim: _Claim, candidates: list[dict], proposals: list[dict],
        *, waiting_model: bool, defer_refresh: bool = False,
    ) -> None:
        self._assert_owned(connection, claim)
        connection.execute("""
            UPDATE memory_capture_jobs
            SET phase = 'refresh', candidates = ?, proposals = ?, updated_at = ?,
                waiting_model = ?, lease_until = ?
            WHERE id = ?
        """, (_json(candidates), _json(proposals), self._time(), waiting_model,
              self._time(self._lease_seconds), claim.job.id))
        from .policy_work import finish_relations
        finish_relations(connection,claim,self._time(),waiting_model=waiting_model)
        if defer_refresh:
            connection.execute("""UPDATE memory_capture_jobs SET status='pending', attempts=0, error=NULL,
                available_at=?, lease_token=NULL, lease_until=NULL WHERE id=?""", (self._time(), claim.job.id))

    def _ready_sources(self, *, with_model: bool) -> bool:
        with self.store.transaction() as connection:
            return connection.execute("""SELECT 1 FROM memory_capture_jobs
                WHERE phase != 'refresh' AND (phase NOT IN ('extract','audit') OR ?)
                  AND ((status='pending' AND available_at <= ?) OR (status='running' AND lease_until <= ?)
                       OR (status='waiting_model' AND ?)) LIMIT 1""",
                (with_model, self._time(), self._time(), with_model)).fetchone() is not None

    def _yield_refresh(self, claim: _Claim) -> Job:
        with self.store.transaction(write=True) as connection:
            self._assert_owned(connection, claim)
            connection.execute("""UPDATE memory_capture_jobs SET status='pending', attempts=max(attempts-1, 0),
                updated_at=?, available_at=?, lease_token=NULL, lease_until=NULL WHERE id=?""",
                (self._time(), self._time(), claim.job.id))
            return self.get(claim.job.id)

    def _refresh_generation(self, request_key: str) -> str | None:
        with self.store.transaction() as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_refresh_generations'").fetchone():
                return None
            row = connection.execute("SELECT response FROM memory_refresh_generations WHERE request_key=?",
                                     (request_key,)).fetchone()
            return row[0] if row else None

    def _save_refresh_generation(self, claim: _Claim, request_key: str, response: str) -> None:
        with self.store.transaction(write=True) as connection:
            self._assert_owned(connection, claim)
            self._scheduler_schema(connection)
            connection.execute("INSERT OR IGNORE INTO memory_refresh_generations VALUES (?, ?)", (request_key, response))

    def _finish_extraction(self, claim: _Claim, event: RawEvent, extraction: dict) -> Job:
        from .source_reprocessing import (freeze, schema, PROTOCOL,
            coverage_accepted, receipt)
        from .source_revision import SCHEMA, validate_result, mark_revision_changed
        from .model import ModelError
        with self.store.transaction(write=True) as connection:
            self._assert_owned(connection, claim)
            schema(connection)
            current = freeze(self,event.id,connection)
            used = extraction.get('input_fingerprint')
            if used is not None and used != current.fingerprint:
                # Keep the live lease until the actual publication boundary; a
                # newly visible antecedent cannot authorize stale model output.
                connection.execute("UPDATE memory_capture_jobs SET status='pending',phase=?,attempts=0,"
                    'error=?,lease_token=NULL,lease_until=NULL,updated_at=?,available_at=? WHERE id=?',
                    ('audit' if current.prior_ids else 'extract','Discarded changed source context',
                     self._time(),self._time(),claim.job.id))
                return self.get(claim.job.id)
            prior = set(current.prior_ids)
            if 'prior_ids' in extraction and set(extraction['prior_ids']) != prior:
                raise ValueError('source membership changed during interpretation')
            retained = extraction.get('retained',{})
            withdrawn = extraction.get('withdrawn',{})
            operational_errors = [row['operational_error'] for row in extraction.get('context_recovery',[])
                if row.get('operational_error')]
            complete = not operational_errors and coverage_accepted(extraction) and (not prior or
                (extraction.get('reconciliation_complete',False) and set(retained)|set(withdrawn)==prior))
            # Direct low-level callers cannot turn omission into removal either.
            status = 'accepted_complete' if complete else extraction.get('outcome','unresolved')
            if operational_errors:
                status = 'unresolved' if extraction['assertions'] or retained else 'failed'
            if status not in ('accepted_complete','unresolved','failed'):
                raise ValueError('invalid extraction outcome')
            now = self._time()
            context_order = extraction.get('context_order',current.order)
            historical = list(claim.job.extracted_ids)
            exact, reconciliation = [], []
            if status != 'failed':
                for key,verification in retained.items():
                    exact.append(key)
                    reconciliation.append({'assertion_id':key,'decision':'retained','verification':verification})
                for item in extraction['assertions']:
                    if item.get('_retained_id') in retained:
                        continue
                    assertion_id = self._publish_interpretation(connection,event,item,extraction,context_order,now)
                    if assertion_id not in exact:
                        exact.append(assertion_id)
                        verification=receipt(item['interpretation']['reason'],item['evidence_refs'])
                        reconciliation.append({'assertion_id':assertion_id,
                            'decision':'retained' if assertion_id in prior else 'introduced',
                            'verification':verification})
                    if assertion_id not in historical: historical.append(assertion_id)
            for key,verification in withdrawn.items():
                reconciliation.append({'assertion_id':key,'decision':'withdrawn','verification':verification})
            reviews = [{**review, 'evidence_refs': [_compact_reference(ref) for ref in review.get('evidence_refs',[])],
                        **({'temporal_evidence': [_compact_reference(ref) for ref in review['temporal_evidence']]}
                           if 'temporal_evidence' in review else {})}
                       for review in extraction['reviews']]
            if not complete and not any(review.get('status') in ('unresolved','pending') for review in reviews):
                reviews.append({'kind':'source_revision','status':'unresolved','evidence_refs':[],
                    'reason':'Prior interpretation reconciliation or source coverage remains unresolved.'})
            recovery=list(extraction.get('context_recovery',[]))
            if extraction.get('source_audit_request_ids'):
                from .source_omission import request_consumption
                continuation=request_consumption(self,connection,current,claim,extraction)
                if operational_errors and continuation:recovery.append(continuation)
            snapshot = {'assertion_ids':exact,'reviews':reviews,
                'context_event_ids':extraction['context_event_ids'],'context_order':context_order,
                'context_truncated':extraction['context_truncated'],
                'context_recovery':recovery,
                'source_revision':{'schema':SCHEMA,'event_id':event.id,'protocol':PROTOCOL,
                    'input_fingerprint':used or current.fingerprint,'status':status,
                    'assertion_ids':exact,'reconciliation':reconciliation}}
            validate_result(connection,event.id,snapshot,prior_ids=prior)
            # Repeated identical publication is idempotent. Explicit retries and
            # bounded gap requests may attest a changed result for the same input.
            fingerprint = _identity('source-revision',PROTOCOL,used or current.fingerprint,
                status,_json(sorted(exact)),_json(sorted(withdrawn)),
                *sorted(extraction.get('source_audit_request_ids',[])),
                *([f'operational-attempt:{claim.job.attempts}'] if operational_errors else []))
            inserted=connection.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) '
                'VALUES(?,?,?,?) ON CONFLICT(event_id,fingerprint) DO NOTHING',
                (event.id,fingerprint,now,_json(snapshot))).rowcount
            connection.execute('INSERT INTO memory_source_audits VALUES(?,?,?,?,?,?) '
                'ON CONFLICT(event_id) DO UPDATE SET protocol=excluded.protocol,desired_input=excluded.desired_input,'
                'attempted_input=excluded.attempted_input,status=excluded.status,reason=excluded.reason',
                (event.id,PROTOCOL,current.fingerprint,current.fingerprint,status,
                 None if complete else 'Source interpretation audit remains unresolved.'))
            from .source_omission import finish_requests
            run_id=connection.execute('SELECT id FROM memory_extraction_runs WHERE event_id=? AND fingerprint=?',
                (event.id,fingerprint)).fetchone()[0]
            origin_refresh=finish_requests(connection,extraction,run_id=run_id) if not operational_errors else False
            from .source_generation import finish as finish_generation
            finish_generation(connection,claim,current,run_id=run_id,coverage=status,completed_at=now,
                              operational_errors=operational_errors)
            # Gap-only publications change the review frontier of existing
            # knowledge, even when they add no claim. An empty new scope still
            # has nothing to materialize; repeated identical runs remain no-ops.
            if inserted and (complete or exact or withdrawn or _scope_has_published_knowledge(connection,event.scope)):
                mark_revision_changed(connection,event.id,now)
            if operational_errors:
                # The partial semantic receipt and operational retry share the
                # owned publication transaction. Keep the existing attempt cap,
                # delay and request lifecycle; a retry naturally audits prior IDs.
                failure = operational_errors[-1]
                connection.execute('UPDATE memory_capture_jobs SET extracted_ids=?,proposals=? WHERE id=?',
                    (_json(historical),_json(_current_reviews(reviews)),claim.job.id))
                return self._fail(claim, ModelError(failure['message']))
            connection.execute("UPDATE memory_capture_jobs SET status=?,phase=?,attempts=CASE WHEN ? THEN 0 ELSE attempts END,error=NULL,"
                'lease_token=NULL,lease_until=NULL,extracted_ids=?,proposals=?,updated_at=?,available_at=? WHERE id=?',
                ('pending' if origin_refresh else 'completed','refresh' if origin_refresh else 'done',int(origin_refresh),
                 _json(historical),_json(_current_reviews(reviews)),now,now,claim.job.id))
            return self.get(claim.job.id)

    def _publish_interpretation(self, connection, event, item, extraction, context_order, now):
        quote,text=item['quote'],item['text']
        kind=item.get('kind') or declared_kind(quote,event.metadata)
        sources=[event,*(self.get_event(ref['event_id']) for ref in item['evidence_refs'])]
        availability=max(source.recorded_at for source in sources if source is not None)
        valid_at=item.get('valid_at')
        metadata={**event.metadata,**({'kind':kind} if kind else {}),'excerpt':quote,
            'event_id':event.id,'turn_id':event.turn_id,'source_recorded_at':event.recorded_at,
            'source_span':item['source_span'],'evidence_refs':[_compact_reference(ref) for ref in item['evidence_refs']],
            'source_available_at':availability,'context_event_ids':extraction['context_event_ids'],
            'context_truncated':extraction['context_truncated'],'context_order':context_order,
            'context_recovery':extraction.get('context_recovery',[])}
        if valid_at is not None:
            metadata['temporal_precision']=item['temporal_precision']
            metadata['temporal_evidence']=[_compact_reference(ref) for ref in item['temporal_evidence']]
        if 'interpretation' in item:
            metadata['interpretation']={**item['interpretation'],'interpreted_at':now}
        # Immutable identity covers proposition, kind, attribution and exact proof.
        # Review reason/time and context-wide audit are attestation data, not a
        # changed proposition. Retaining an old ID occurs explicitly above.
        payload={'text':text,'actor':event.actor,'source':event.source,'episode':event.session_id,
            'scope':event.scope,'valid_at':_timestamp(valid_at) if valid_at is not None else None,
            'recorded_at':availability,'kind':kind,'excerpt':quote,'source_span':item['source_span'],
            'evidence_refs':metadata['evidence_refs'],
            'temporal_precision':metadata.get('temporal_precision'),
            'temporal_evidence':metadata.get('temporal_evidence')}
        assertion_id=_identity('assertion-proof-v1',_json(payload))
        previous=self.store.get_assertion(assertion_id)
        if previous is not None:
            previous_payload={'text':previous.text,'actor':previous.actor,'source':previous.source,
                'episode':previous.episode,'scope':previous.scope,'valid_at':previous.valid_at,
                'recorded_at':previous.recorded_at,**{key:previous.metadata.get(key) for key in
                    ('kind','excerpt','source_span','evidence_refs','temporal_precision','temporal_evidence')}}
            if previous_payload!=payload:
                raise ValueError('immutable interpretation ID has different canonical proof payload')
            return assertion_id
        self.capture(CaptureInput(text=text,actor=event.actor,source=event.source,episode=event.session_id,
            assertion_id=assertion_id,scope=event.scope,metadata=metadata,valid_at=valid_at,recorded_at=availability))
        return assertion_id

    def _finish(self, claim: _Claim, refreshed_views: dict[str, int]) -> Job:
        with self.store.transaction(write=True) as connection:
            self._assert_owned(connection, claim)
            now = self._time()
            connection.execute("""
                UPDATE memory_capture_jobs
                SET status = CASE WHEN waiting_model THEN 'waiting_model' ELSE 'completed' END,
                    phase = 'done', error = NULL, lease_token = NULL, lease_until = NULL,
                    refreshed_views = ?, updated_at = ?, available_at = ?
                WHERE id = ?
            """, (_json(refreshed_views), now, now, claim.job.id))
            from .policy_work import finish_relations
            finish_relations(connection,claim,now,waiting_model=bool(connection.execute('SELECT waiting_model FROM memory_capture_jobs WHERE id=?',(claim.job.id,)).fetchone()[0]))
            return self.get(claim.job.id)

    def _fail(self, claim: _Claim, error: Exception, *, retry_without_limit=False) -> Job:
        with self.store.transaction(write=True) as connection:
            self._assert_owned(connection, claim)
            from .model import ModelProviderUnavailable
            exhausted = isinstance(error, ModelProviderUnavailable) or (not retry_without_limit and claim.job.attempts >= self._max_attempts)
            connection.execute("""
                UPDATE memory_capture_jobs
                SET status = ?, error = ?, lease_token = NULL, lease_until = NULL,
                    updated_at = ?, available_at = ?
                WHERE id = ?
            """, (
                "failed" if exhausted else "pending", f"{type(error).__name__}: {error}"[:1000],
                self._time(), self._time(2 ** min(claim.job.attempts, 8)),
                claim.job.id,
            ))
            if exhausted and claim.job.event_id:
                connection.execute("UPDATE memory_source_audit_requests SET status='failed',"
                    "evidence_json=CASE WHEN evidence_json IS NULL THEN NULL ELSE json_set(evidence_json,'$.terminal_error',?) END "
                    "WHERE event_id=? AND status IN ('scheduled','refresh')",(str(error)[:1000],claim.job.event_id))
            return self.get(claim.job.id)

    def _time(self, after_seconds: float = 0) -> str:
        value = self._clock() + timedelta(seconds=after_seconds)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _scope_has_published_knowledge(connection, scope):
    from .source_revision import exclusion
    clauses,arguments=exclusion(connection,'a')
    where=' AND '.join(['a.scope=?',*clauses])
    if connection.execute('SELECT 1 FROM memory_assertions a WHERE '+where+' LIMIT 1',
                          (scope,*arguments)).fetchone():
        return True
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_views'").fetchone():
        return False
    return bool(connection.execute("SELECT 1 FROM memory_views v,json_each(v.definition,'$.scopes') s "
        'WHERE v.current_id IS NOT NULL AND s.value=? LIMIT 1',(scope,)).fetchone())


def _current_reviews(reviews: list[dict]) -> list[dict]:
    """Live diagnostics use the latest verdict; extraction history retains every attempt."""
    current = {}
    for position, review in enumerate(reviews):
        index = review.get("index")
        indexed = review.get("kind") == "interpretation" and type(index) is int and index >= 0
        key = ("interpretation", index) if indexed else ("review", position)
        current[key] = (position, review)
    return [review for _, review in sorted(current.values(), key=lambda item: item[0])]


def _compact_reference(reference: Mapping[str, Any]) -> dict:
    return {key: value for key, value in reference.items() if key != "quote"}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _identity(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256(_json(parts).encode("utf-8")).hexdigest()


def _event(row) -> RawEvent:
    return RawEvent(**{key: json.loads(row[key]) if key == "metadata" else row[key]
                       for key in RawEvent.__dataclass_fields__})


def _job(row) -> Job:
    return Job(
        id=row["id"], assertion_id=row["assertion_id"], event_id=row["event_id"],
        status=row["status"], phase=row["phase"],
        attempts=row["attempts"], updated_at=row["updated_at"],
        available_at=row["available_at"], error=row["error"],
        candidates=json.loads(row["candidates"]), proposals=json.loads(row["proposals"]),
        refreshed_views=json.loads(row["refreshed_views"]),
        extracted_ids=json.loads(row["extracted_ids"]),
    )
