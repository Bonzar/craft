"""Official-SDK stdio server; database and providers are fixed at startup.

Run: python -m prototypes.assertion_memory.mcp_server --db /abs/memory.sqlite3
     --provider subprocess --model-command-json '["my-provider"]'
No optional capture adapter or harness configuration is required.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import sqlite3
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from .embeddings import CachedEmbedder, FastEmbedModel, MULTILINGUAL_MODEL
from .engine import MemoryEngine
from .model import CandidateSearch, CodexModel, LocalOpenAIModel, SubprocessModel
from .service import MemoryService
from .storage import MemoryStore
from .worker import LinkWorker


@dataclass(frozen=True)
class ServiceConfig:
    db: str
    provider: str
    command: tuple[str, ...] = ()
    endpoint: str | None = None
    model: str | None = None
    timeout: float | None = 120
    embedding_mode: str = 'lexical'
    embedding_model: str = MULTILINGUAL_MODEL
    embedding_cache: str | None = None
    worker_idle_seconds: float = 30
    source_workers: int = 2
    processing_session_ids: tuple[str, ...] | None = None
    review_model: str | None = None
    reasoning_effort: str | None = None
    review_reasoning_effort: str | None = None
    batch_source_construction: bool = False
    source_event_ids: tuple[str, ...] | None = None
    codex_job_timeout: float | None = None
    auto_process_fresh_mac: bool = False

    def __post_init__(self):
        from .thought_processing_selection import normalize_session_ids
        object.__setattr__(self, 'processing_session_ids', normalize_session_ids(self.processing_session_ids))
        object.__setattr__(self, 'source_event_ids', normalize_session_ids(self.source_event_ids))
        if (type(self.auto_process_fresh_mac) is not bool or self.auto_process_fresh_mac
                and (self.processing_session_ids != () or self.source_event_ids is not None)):
            raise ValueError('fresh Mac processing requires an empty legacy automatic selection')
        if not Path(self.db).is_absolute() or self.db == ':memory:':
            raise ValueError('db must be an absolute persistent SQLite path')
        if self.provider not in ('none', 'subprocess', 'local', 'codex'):
            raise ValueError('provider must be explicit: none, subprocess, local or codex')
        if self.reasoning_effort is not None:
            if not isinstance(self.reasoning_effort,str) or not self.reasoning_effort.strip():
                raise ValueError('reasoning_effort must be null or a nonempty effort name')
            if self.provider!='codex':
                raise ValueError('reasoning_effort is supported only by the Codex provider')
        if self.review_model is not None:
            if not isinstance(self.review_model,str) or not self.review_model.strip():
                raise ValueError('review_model must be null or a nonempty model name')
            if self.provider not in ('codex','local'):
                raise ValueError('review_model requires a provider with explicit model selection')
        if self.review_reasoning_effort is not None:
            if not isinstance(self.review_reasoning_effort,str) or not self.review_reasoning_effort.strip():
                raise ValueError('review_reasoning_effort must be null or a nonempty effort name')
            if self.provider!='codex' or self.review_model is None:
                raise ValueError('review_reasoning_effort requires an explicit Codex review_model')
        if self.provider == 'subprocess' and (not self.command or not all(
                isinstance(part, str) and part for part in self.command)):
            raise ValueError('subprocess provider requires a JSON command argument array')
        if self.provider == 'codex':
            if not isinstance(self.model, str) or not self.model.strip():
                raise ValueError('codex provider requires an explicit model')
        if self.provider == 'local' and (not self.endpoint or not self.model):
            raise ValueError('local provider requires endpoint and model')
        if self.timeout is not None and not 0 < self.timeout <= 600:
            raise ValueError('timeout must be null or positive and bounded')
        if self.codex_job_timeout is not None:
            if self.provider != 'codex':
                raise ValueError('codex_job_timeout requires the Codex provider')
            if (type(self.codex_job_timeout) not in (int, float)
                    or not 0 < self.codex_job_timeout <= 600):
                raise ValueError('codex_job_timeout must be greater than zero and at most 600 seconds')
        if self.provider == 'codex':
            # Preserve the legacy unbounded default. A dedicated per-service
            # setting lets a deployed Codex worker bound generation and review.
            object.__setattr__(self, 'timeout', self.codex_job_timeout)
        if not 0 < self.worker_idle_seconds <= 3600:
            raise ValueError('worker idle must be positive and bounded')
        if type(self.source_workers) is not int or not 1 <= self.source_workers <= 4:
            raise ValueError('source_workers must be between 1 and 4')
        if self.embedding_mode not in ('lexical', 'fastembed'):
            raise ValueError('embedding mode must be lexical or fastembed')
        if self.embedding_mode == 'fastembed' and not self.embedding_cache:
            raise ValueError('fastembed requires an explicit embedding cache directory')

    @property
    def route_profile_key(self):
        from .model_router import route_profile_key
        return route_profile_key({'kind':self.provider,'command':self.command,'endpoint':self.endpoint},
                                 self.model,self.review_model,self.reasoning_effort,
                                 review_reasoning_effort=self.review_reasoning_effort)

    def components(self, store: MemoryStore):
        model = None
        from .model_usage import UsageLedger
        usage = UsageLedger.for_database(self.db) if self.provider != 'none' else None
        if self.provider == 'subprocess':
            model = SubprocessModel(self.command, timeout=self.timeout, usage_ledger=usage)
        elif self.provider == 'local':
            model = LocalOpenAIModel(self.endpoint, self.model, timeout=self.timeout, usage_ledger=usage)
        elif self.provider == 'codex':
            model = CodexModel(model=self.model, timeout=self.timeout, usage_ledger=usage,
                               reasoning_effort=self.reasoning_effort)
        if model is not None:
            model.route_profile_key=self.route_profile_key
        if self.provider=='local':
            model.requested_model=self.model
        if self.review_model is not None:
            from .model_router import TaskModelRouter
            if self.provider=='codex':
                review=CodexModel(model=self.review_model,timeout=self.timeout,usage_ledger=usage,
                                  reasoning_effort=(self.review_reasoning_effort
                                      if self.review_reasoning_effort is not None else self.reasoning_effort))
            else:
                review=LocalOpenAIModel(self.endpoint,self.review_model,timeout=self.timeout,usage_ledger=usage)
                review.requested_model=self.review_model
            review.route_profile_key=self.route_profile_key
            model=TaskModelRouter(model,review,profile_key=self.route_profile_key)
        candidates = CandidateSearch()
        if self.embedding_mode == 'fastembed':
            backend = FastEmbedModel(self.embedding_model, self.embedding_cache)
            embedder = CachedEmbedder(store, backend, 'fastembed:' + self.embedding_model)
            candidates = CandidateSearch(embedder, identity='fastembed:' + self.embedding_model)
        identity = [self.provider, self.command, self.endpoint, self.model]
        if self.review_model is not None or self.reasoning_effort is not None:
            identity.append({'review_model':self.review_model,'route_profile_key':self.route_profile_key})
        model_key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        return model, candidates, model_key

    def engine(self, store: MemoryStore) -> MemoryEngine:
        model, candidates, model_key = self.components(store)
        return MemoryEngine(store, model=model, naming_model=model, candidate_search=candidates, model_key=model_key,
                            verify_overviews=model is not None)

    def service(self, store: MemoryStore):
        from .thought_runtime import initialize_empty
        if initialize_empty(store):
            from .thought_service import ThoughtService
            model, candidates, _ = self.components(store)
            return ThoughtService(store, model, candidates, processing_session_ids=self.processing_session_ids)
        if self.processing_session_ids is not None:
            raise ValueError('session processing selection requires the canonical thought runtime')
        return MemoryService(store, self.engine(store))


def _processing_selector(config):
    from .thought_processing_selection import FRESH_MAC_ONLY
    return FRESH_MAC_ONLY if config.auto_process_fresh_mac else config.processing_session_ids


def _eligible_processing_work(config):
    """Model-free read probe; capture from an unselected session cannot wake work."""
    if config.processing_session_ids is None:
        return True
    if _processing_selector(config) == () or not Path(config.db).is_file():
        return False
    from .thought_processing_selection import processing_deadline
    uri='file:'+quote(str(Path(config.db).resolve()),safe='/')+'?mode=ro'
    connection=sqlite3.connect(uri,uri=True,timeout=5)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        now=datetime.now(timezone.utc).isoformat(timespec='microseconds')
        return processing_deadline(connection,_processing_selector(config),now=now,with_model=True) is not None
    finally:
        connection.close()


class WorkerLauncher:
    """Independent child owns its own connection and survives stdio/server shutdown.

    flock serializes workers for this database. Durable queue leases recover a
    killed child's in-flight jobs on a later launch; no work lives only in RAM.
    """
    def __init__(self, config: ServiceConfig):
        self.config = config

    def start(self, limit: int | None = None) -> dict:
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
            raise ValueError('limit must be between 1 and 1000 attempts')
        if self.config.provider == 'none':
            return {'scheduled': False, 'reason': 'no model provider configured; raw events remain durable'}
        # Capture can arrive while the existing worker performs its idle handoff.
        # Its post-unlock probe sees already committed intake; an unlocked queue
        # is launched here. A pause owner also prevents needless child processes.
        with open(self.config.db+'.worker.lock','a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return {'scheduled':False,'reason':'processing lock already owned; raw events remain durable'}
        # Probe after releasing flock: an intake committed during this probe can
        # acquire the same lock and launch its own worker, preserving idle handoff.
        if not _eligible_processing_work(self.config):
            return {'scheduled':False,'reason':'no work eligible for startup processing selection'}
        args = [sys.executable, '-B', '-m', 'prototypes.assertion_memory.mcp_server', '--worker-config-json',
                json.dumps(asdict(self.config)), '--worker-limit', str(limit or 0)]
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        # The release is immutable; grandchildren must inherit the same rule.
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', '')
        log_path = self.config.db + '.worker.log'
        with open(log_path, 'ab', buffering=0) as log:
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True, env=env, cwd=root)
        return {'scheduled': True, 'worker_pid': child.pid, 'attempt_limit': limit,
                'log_path': log_path, 'durability': 'SQLite queue; independent worker process'}


def _idle_handoff(lock, next_deadline):
    """Close the capture/idle-exit race while preserving one worker owner.

    Captures before unlock are visible to our final probe. Captures after unlock
    can launch their own lock owner. If we find work and another owner won, that
    worker now owns the durable queue; otherwise we resume under our own lock.
    """
    fcntl.flock(lock, fcntl.LOCK_UN)
    if next_deadline() is None:
        return True
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    return False


def run_worker(config: ServiceConfig, limit: int = 0) -> None:
    if type(limit) is not int or limit < 0:
        raise ValueError('worker limit must be a nonnegative shared attempt budget')
    if config.processing_session_ids == () and (not config.auto_process_fresh_mac or not _eligible_processing_work(config)):
        return
    with open(config.db + '.worker.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with MemoryStore(config.db) as store:
            from .thought_runtime import initialize_empty
            if initialize_empty(store):
                from .thought_processing import run_pool
                return run_pool(config, lock, _idle_handoff, limit=limit)
            if config.processing_session_ids is not None:
                return  # Legacy workers cannot enforce canonical session/cohort admission.
            engine = config.engine(store)
            worker = LinkWorker(MemoryService(store, engine).queue, engine, source_first=True)
            # The exclusive server-worker flock proves its previous owner exited.
            # Never revoke source leases from clients or unlocked direct workers.
            now = worker.queue._time()
            with store.transaction(write=True) as connection:
                connection.execute("UPDATE memory_capture_jobs SET status='pending',lease_token=NULL,lease_until=NULL,"
                    "updated_at=?,available_at=?,error='Worker interrupted; source lease recovered under exclusive server lock' "
                    "WHERE status='running'", (now, now))
            worker.derived.recover_abandoned()
            worker.names.recover_abandoned()
            from .processing_pool import ActiveClaims,ProcessingPool
            claims=ActiveClaims()
            @contextmanager
            def lane_factory(lane,observer):
                # All SQLite objects, mutable engines and providers are born and
                # closed in their own thread. No model rewrite crosses lanes.
                with MemoryStore(config.db) as local_store:
                    local_engine=config.engine(local_store)
                    yield LinkWorker(MemoryService(local_store,local_engine).queue,local_engine,
                        source_first=True,lane=lane,claim_observer=observer)
            def heartbeat():
                if claims.snapshot():
                    with store.transaction(write=True) as connection:claims.renew(connection)
            completed=0
            while True:
                remaining=limit-completed if limit else 0
                pool=ProcessingPool(lane_factory,source_workers=config.source_workers,limit=remaining,
                    idle_seconds=config.worker_idle_seconds,claims=claims,heartbeat=heartbeat)
                result=pool.run()  # Includes stop-claims, heartbeat drain and join.
                completed+=result['completed']
                if limit and completed>=limit:return
                # No writing thread is alive when this boundary releases flock.
                if _idle_handoff(lock,lambda:worker.seconds_until_work(with_model=worker.model is not None)):
                    return


def create_server(config: ServiceConfig, *, background: bool = True, store: MemoryStore | None = None,
                  start_on_session: bool = True, service=None):
    from mcp.server.fastmcp import FastMCP

    if store is not None and Path(store.path).resolve() != Path(config.db).resolve():
        raise ValueError('caller-owned store must match the configured database')
    if service is not None and (store is None or service.store is not store):
        raise ValueError('Injected service must use the caller-owned store')
    owns_store = store is None
    store = store if store is not None else MemoryStore(config.db)
    service = config.service(store) if service is None else service
    launcher = WorkerLauncher(config)

    @asynccontextmanager
    async def lifespan(_server):
        if background and start_on_session:
            launcher.start()
        try:
            yield {}
        finally:
            if owns_store:
                store.close()

    from .thought_service import ThoughtService, INSTRUCTIONS
    mcp = FastMCP('assertion-memory', lifespan=lifespan, log_level='WARNING', instructions=INSTRUCTIONS if isinstance(service,ThoughtService) else (
        'Shared, harness-neutral assertion memory. Capture raw role-attributed messages using stable '
        'session/message IDs and known producer transcript positions; retain attributed assistant and tool reports. Do not '
        'manufacture kinds or graph edges. Capture acknowledges durable intake, not completed extraction. '
        'Query returns versioned evidence; always report pending/error counts and use source spans to '
        'check provenance. Read query_snapshot and freshness_check: refresh_required means newer eligible '
        'evidence or processing gaps remain unchecked; use that answer only as dated evidence and query '
        'again before authorizing a current-state action. transport_limit reports known incoming delivery '
        'gaps globally, independently of answer_accepted: accepted text describes only available evidence. '
        'Do not retry memory_query in a loop while transport_limit persists; wait for delivery frontier '
        'progress. reported_current is a producer delivery report, not knowledge of unseen offline changes. '
        'Snapshot cutoffs remain authoritative even '
        'when ingestion continues. An assistant proposal is not a verified user decision. Provider/database '
        'configuration belongs to server startup. No optional harness capture adapter is required. '
        'For memory_query, answer_payload_complete describes delivery of answer content, not factual '
        'completeness, acceptance or current permission. answer_accepted certifies this exact question and text; '
        'equivalent stored-question reuse can instead return false with answer_acceptance_reason, without '
        'regenerating the stored view. Its prior review is not a new exact-question review. '
        'A complete answer_metadata.notice with purpose=knowledge_limit_notice and notice_payload_complete=true '
        'may be shown as a base-knowledge limitation even when answer_accepted=false and answer_payload_complete=false. '
        'Those false flags still mean no reviewed domain answer. The complete notice has no missing answer page; '
        'do not retry or fetch optional proof merely to complete it. It never establishes world absence, an event '
        'date, a missing report or permission to act. '
        'Check answer_accepted, completeness, source_status '
        'and freshness independently. Follow ALL answer_continuations and their subsequent pages, preserving '
        'every condition, exception and qualification, before using missing answer content. If no answer pages '
        'remain but acceptance or material requirements failed, report the unresolved limit; fetching optional '
        'proof cannot make it an accepted answer. response_complete retains full-envelope/proof export meaning; '
        'proof_complete=false alone does not require ordinary callers to read all optional evidence/audit '
        'appendices. Explicit full-proof QA still reads all proof. Required source reads and unresolved material '
        'conditions are never optional. For any requested JSON section concatenate EVERY page in offset order '
        'and decode together; qualifications may appear on later pages. Snapshot version IDs remain pinned.'))
    _register_write_tools(mcp, service, launcher, background)
    _register_read_tools(mcp, service, config, launcher if background else None)
    return mcp


def _register_write_tools(mcp, service, launcher, background):
    @mcp.tool()
    async def memory_ingest_register(producer_id: str, session_id: str, replace_epoch: str | None = None) -> dict:
        """Register one authorized adapter installation's delivery stream for a stable session.
        Idempotently returns flat stream_id/epoch/frontier state. replace_epoch explicitly replaces
        that exact prior writer epoch, preserving known backlog/gaps. No model or worker work.
        Producer reports are operational claims, never source facts or world verification.
        """
        return service.ingest_register(producer_id, session_id, replace_epoch=replace_epoch)

    @mcp.tool()
    async def memory_ingest_report(stream_id: str, epoch: str, report_seq: int, observed_seq: int,
        scan_state: Literal['current','catching_up','gapped','unknown'], gap_count: int,
        lifecycle: Literal['active','closed'] = 'active') -> dict:
        """Report durable local delivery frontier before upload; heartbeat uses a new report_seq.
        observed_seq counts local durable outbox messages, not physical transcript positions.
        Reports must advance monotonically; exact replay is safe but does not prolong a heartbeat.
        gap_count is positive exactly for gapped state. Close only after current scan, no gaps and
        a fully acknowledged prefix. Server derives acked_through from committed capture receipts.
        A stale clean heartbeat means unobserved, not known pending or world-current evidence.
        """
        return service.ingest_report(stream_id, epoch, report_seq, observed_seq, scan_state, gap_count,
                                     lifecycle=lifecycle)

    @mcp.tool()
    async def memory_store_artifact(data_base64: str, mime_type: str, sha256: str | None = None) -> dict:
        """Store at most 2 MiB of strict base64 image bytes (PNG/JPEG/WebP/GIF), with optional
        expected byte SHA256. Returns immutable content ID and technical metadata, not_interpreted=true.
        No URLs, local paths, image interpretation or model calls. Upload before capture artifact_ids."""
        return service.store_artifact(data_base64,mime_type,sha256)

    @mcp.tool()
    async def memory_capture_message(
        session_id: str, message_id: str, actor_id: str, role: Literal['user', 'assistant', 'tool'],
        text: str, source: str, position: int | None = None, scope: str = 'default',
        source_message_at: str | None = None, source_message_clock_basis: dict | None = None,
        timezone: str | None = None, delivery: dict | None = None, artifact_ids: list[str] | None = None,
    ) -> dict:
        """Durably capture one raw message. Stable IDs are immutable/idempotent; position is zero-based
        producer session transcript order, or omitted when unknown; never use local file line numbers.
        actor_id names the speaker and role preserves user/assistant/tool attribution.
        source is a stable producer provenance URI. The receipt contains identifiers and
        status rather than echoing the source body. Optional source_message_at is an offset-aware
        producer-message timestamp, distinct from this base's capture/knowledge time. Optional basis
        attributes the producer claim; it does not authenticate it. timezone is an explicitly supplied
        speaker IANA calendar timezone, never inferred from a UTC transport offset. Missing origin
        clock stays unknown. Optional delivery={stream_id,epoch,seq} is an operational receipt
        committed atomically with this raw event; it does not change canonical message identity.
        The returned delivery state includes the server-derived acked_through prefix.
        Optional artifact_ids references at most 8 previously uploaded images, immutable on replay.
        Raw metadata contains technical descriptors only, never image bytes or inferred visual facts.
        Extraction/linking runs independently.
        """
        value = service.capture_message(session_id, message_id, actor_id, role, text, position, source, scope,
            source_message_at=source_message_at, source_message_clock_basis=source_message_clock_basis,
            timezone=timezone, delivery=delivery, artifact_ids=artifact_ids)
        if background:
            value['processing'] = launcher.start()
        return value

    @mcp.tool()
    async def memory_process(limit: int = 100, retry_job_id: str | None = None,
                             session_ids: list[str] | None = None,
                             batch_source_construction: bool = False,
                             source_event_ids: list[str] | None = None) -> dict:
        """Schedule up to 1..1000 worker attempts asynchronously. Returns immediately; poll status.
        An already-running database worker owns processing. Optionally retry one failed job by its ID.
        Optional session_ids selects exact source sessions and their causal follow-up jobs for
        this launch. It leaves the automatic selection and the worker lock in place.
        batch_source_construction shares initial draft generation among concurrent source jobs.
        Independent review, publication and follow-up jobs still use their normal processing.
        source_event_ids instead defines one exact group of queued messages for a single
        initial model call. Supply one source session and a limit covering all message IDs.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('limit must be between 1 and 1000 attempts')
        worker = launcher
        if batch_source_construction and session_ids is None:
            raise ValueError('batch_source_construction requires explicit session_ids')
        if source_event_ids is not None and (
                not source_event_ids or len(source_event_ids) > limit
                or len(set(source_event_ids)) != len(source_event_ids)
                or not session_ids or len(session_ids) != 1 or retry_job_id is not None):
            raise ValueError('source_event_ids requires a nonempty unique group from one session within limit, without retry')
        if session_ids is not None:
            from .thought_processing_selection import normalize_session_ids
            from .thought_service import ThoughtService
            selected = normalize_session_ids(session_ids)
            if not selected:
                raise ValueError('session_ids must not be empty')
            configured = launcher.config.processing_session_ids
            if configured and not set(selected).issubset(configured):
                raise ValueError('session_ids includes a session outside startup processing selection')
            if not isinstance(service, ThoughtService):
                raise ValueError('session processing selection requires the canonical thought runtime')
            if source_event_ids is not None:
                from .source_model_batch import pending_sources
                pending_sources(service.queue, selected[0], source_event_ids)
            # An empty automatic selection can coexist with an explicit one-time request.
            worker = WorkerLauncher(replace(launcher.config, processing_session_ids=selected,
                                            batch_source_construction=batch_source_construction,
                                            source_event_ids=source_event_ids, auto_process_fresh_mac=False))
        if retry_job_id is not None:
            if worker.config.processing_session_ids is not None:
                from .thought_processing_selection import source_predicate
                predicate,arguments=source_predicate(worker.config.processing_session_ids,'e')
                with service.store.transaction() as connection:
                    admitted=connection.execute('SELECT 1 FROM memory_capture_jobs j JOIN memory_raw_events e '
                        f'ON e.id=j.event_id WHERE j.id=? AND ({predicate})',
                        (retry_job_id,*arguments)).fetchone()
                if admitted is None:
                    selection = 'startup' if session_ids is None else 'requested'
                    raise ValueError(f'retry job is outside {selection} processing selection')
            service.queue.retry(retry_job_id)
        return {**worker.start(limit), **service.status()}



def _register_read_tools(mcp, service, config, launcher=None, query_jobs=None):
    _register_source_tools(mcp,service)
    from .thought_service import ThoughtService
    if isinstance(service, ThoughtService):
        @mcp.tool()
        async def memory_search(query: str, scope: str | None = None,
                                session_id: str | None = None, limit: int = 10) -> dict:
            """Find current stored thoughts by content and related questions using the configured
            ranker. Optional exact source session and scope filters narrow candidates before ranking.
            Returns previews and immutable thought/version IDs. Never generates an answer,
            publishes thoughts, schedules processing or writes embeddings to the database."""
            return service.search(query, scope=scope, session_id=session_id, limit=limit)

    @mcp.tool()
    async def memory_query(
        query: str | None = None, scope: str | None = None, valid_as_of: str | None = None,
        recorded_as_of: str | None = None, request_session_id: str | None = None,
        query_job_id: str | None = None, query_request_id: str | None = None,
    ) -> dict:
        """Answer from a versioned evidence view. Omitted/null scope searches every known scope in this
        connected database; an explicit scope restricts retrieval to that exact scope. Optional inclusive
        ISO cutoffs distinguish applicable
        time from knowledge recorded by that time. Unknown valid time is excluded by valid_as_of.
        Known incoming transport_limit applies globally even to scoped/historical queries; accepted
        text remains limited to available evidence. Do not retry queries in a loop before the delivery
        frontier advances. Current transport_status is separate from the immutable snapshot/check.
        Pending work means this answer is incomplete. query_snapshot pins the source membership and
        effective cutoffs; freshness_check names this invocation's exact check. refresh_required forbids
        using this dated snapshot to authorize current-state action; query again for a new frontier.
        answer_payload_complete reports inline answer delivery only; answer_accepted, completeness and
        refresh_required separately report review and limitations. Follow every answer_continuations
        page for missing answer content and qualifications. Optional proof pagination alone does not
        require reading all audit appendices. A complete answer_metadata.notice with purpose
        knowledge_limit_notice and notice_payload_complete=true explains only the base's knowledge limit.
        Show that notice even if answer_accepted and answer_payload_complete remain false; no missing
        notice page or automatic retry is implied. It cannot establish world absence, event dates or
        permission. With partial_answer=true, the answer is retained evidence for only part of the
        question. Preserve the limitation and missing_question; do not claim answer_accepted.
        With answer_error=semantic_review_rejected (in its code field),
        drafting ended without an accepted answer. No draft was published and
        this says nothing about whether the underlying facts exist.
        The same query/scope retains summary history. Optional request_session_id is an explicit
        caller-session claim for execution history, never source authentication, factual proof or user approval.
        Omit it when the actual current caller session is unknown.
        For a hosted interactive query, supply an optional unique query_request_id to retry
        a lost submission safely. If query_state is queued or running, call this tool again
        with only query_job_id. Never re-submit a pending query or present it as an answer.
        """
        if query_jobs is not None:
            return await query_jobs(query=query, scope=scope, valid_as_of=valid_as_of,
                                    recorded_as_of=recorded_as_of, request_session_id=request_session_id,
                                    query_job_id=query_job_id, query_request_id=query_request_id)
        if query_job_id is not None or query_request_id is not None or query is None:
            raise ValueError('This endpoint requires a query and does not support interactive job arguments')
        try:
            return await asyncio.to_thread(_query_with_connection, config, query, scope,
                                           valid_as_of, recorded_as_of, request_session_id)
        finally:
            if launcher is not None:
                from .thought_service import ThoughtService
                if isinstance(service, ThoughtService):
                    launcher.start()
                else:
                    ready=service.store._connection.execute("SELECT 1 FROM memory_view_name_work WHERE "
                        "status IN ('pending','running','retry','waiting_model') LIMIT 1").fetchone()
                    from .source_omission import pending as pending_omissions
                    if ready or pending_omissions(service.store._connection):launcher.start()

    @mcp.tool()
    async def memory_status(
        event_id: str | None = None, scope: str | None = None, offset: int = 0, limit: int = 100,
        section: Literal['overview','job','derived_overview','derived_overviews','navigation_catalog','view_names','model_usage','transport','policy_coverage'] = 'overview',
        catalog_id: str | None = None,
    ) -> dict:
        """Inspect exact job counts and bounded pending/error/unresolved detail pages. Scope=None
        explicitly requests global diagnostics; use scope for project-local details. Each list has
        independent total/next_offset metadata. Follow offset pages to retrieve all details.
        Oversized individual jobs use section=job, streamed as JSON with character offset/limit.
        navigation_catalog requires an explicit scope and lists read-only partial routing metadata,
        including planned questions and historical snapshots. Follow its catalog_id-pinned continuation;
        stale/restart_required means restart at offset zero. Catalog completeness is transport only,
        never domain acceptance, source clearance or permission. It never starts processing.
        transport returns global producer-delivery limits without model work; omit scope/event/catalog.
        Current known incoming gaps apply across query scopes; clean closed/stale streams do not
        permanently block queries. reported_current does not establish unseen offline changes.
        policy_coverage reports current versus historical audit policy coverage without scheduling it;
        it does not establish domain completeness. model_usage returns database-wide provider token accounting; it does not start model work.
        Cached input is a subset of input, reasoning output a subset of output. Unknown usage is
        not zero. This section requires scope=None and reports generation calls, not main-agent usage.
        """
        return service.status(event_id,scope=scope,offset=offset,limit=limit,section=section,catalog_id=catalog_id)




def _register_source_tools(mcp,service):
    @mcp.tool()
    async def memory_model_call(call_id: str, section: Literal['overview','request','response'] = 'overview',
                                offset: int = 0, limit: int = 6000) -> dict:
        """Read an exact canonical model call: program-bound thought/raw versions, timing, usage,
        actual request or acquired response. No model work. Text is paginated losslessly."""
        if not hasattr(service, 'model_call'):
            raise ValueError('exact canonical call trace is unavailable before thought migration')
        return service.model_call(call_id, section=section, offset=offset, limit=limit)

    @mcp.tool()
    async def memory_sessions(offset: int = 0, limit: int = 50, snapshot_id: int | None = None) -> dict:
        """List stored raw sessions without bodies or model work. Titles remain unknown. Counts
        describe captured messages, not complete provider transcripts. Follow pinned snapshot pages."""
        return service.sessions(offset=offset, limit=limit, snapshot_id=snapshot_id)

    @mcp.tool()
    async def memory_session_messages(session_id: str, turn_id: str | None = None,
        message_id: str | None = None, offset: int = 0, limit: int = 50,
        snapshot_id: int | None = None) -> dict:
        """Exact session/capture-turn/explicit-message lookup, no semantic search or model. No bodies.
        Generic capture turn_id may equal message_id; native_turn_id is separately explicit.
        Retrieve source text with memory_source(event_id). Limits 1..200, pinned append watermark."""
        return service.session_messages(session_id, turn_id=turn_id, message_id=message_id,
            offset=offset, limit=limit, snapshot_id=snapshot_id)

    @mcp.tool()
    async def memory_provenance(record_ids: list[str]) -> dict:
        """Resolve 1..100 raw/assertion IDs to real source-session membership without bodies/model.
        Mixed origins stay mixed; incomplete or missing anchors never imply exclusive membership.
        Session IDs express provenance, not topic relevance, speaker consent or factual acceptance."""
        return service.provenance(record_ids)

    @mcp.tool()
    async def memory_session_outputs(session_id: str, offset: int = 0, limit: int = 50,
                                     snapshot_id: int | None = None) -> dict:
        """Read recorded causal outputs for an exact source/native session or explicit caller context.
        Limit 1..100; snapshot_id pins append-only event pages. Legacy history remains unknown;
        scope membership or matching timestamps do not establish causality. No model work."""
        return service.session_outputs(session_id,offset=offset,limit=limit,snapshot_id=snapshot_id)

    @mcp.tool()
    async def memory_run_history(run_id: str, section: Literal['run','causes','events'] = 'run',
        offset: int = 0, limit: int = 6000, snapshot_id: int | None = None) -> dict:
        """Read an actual execution run, explicit causes or pinned lifecycle/output events. JSON is
        paged by characters (limit 1..6000); concatenate all content pages before decoding. Events
        snapshot_id freezes append-only history. Caller context alone is not authenticated source proof."""
        return service.run_history(run_id,section=section,offset=offset,limit=limit,snapshot_id=snapshot_id)

    @mcp.tool()
    async def memory_run_event(event_id: int, offset: int = 0, limit: int = 6000) -> dict:
        """Read one immutable execution event with exact version/check/upstream pins, never the
        view's later current pointer. Concatenate JSON content pages; limit 1..6000 characters."""
        return service.run_event(event_id,offset=offset,limit=limit)

    @mcp.tool()
    async def memory_artifact(artifact_id: str, include_content: bool = False):
        """Read immutable artifact metadata; include_content=true additionally returns typed MCP image
        content for authorized on-demand inspection. Bytes have not been interpreted by this service;
        technical hashes/metadata establish no depicted fact or user approval. No model work."""
        value=service.artifact(artifact_id,include_content=include_content)
        if not include_content or getattr(mcp,'neutral_results',False):return value
        from mcp.types import CallToolResult,TextContent,ImageContent
        data=value.pop('data_base64')
        return CallToolResult(structuredContent=value,content=[
            TextContent(type='text',text=json.dumps(value,ensure_ascii=False)),
            ImageContent(type='image',data=data,mimeType=value['mime_type'])])

    @mcp.tool()
    async def memory_source(
        record_id: str, start: int | None = None, end: int | None = None,
        section: Literal['overview','text','metadata','references','edges','extraction_history'] = 'overview',
        offset: int = 0, limit: int = 6000, snapshot_id: int | None = None,
    ) -> dict:
        """Retrieve original event/extraction history or an assertion's resolved evidence references and
        graph edges. Paired Python character offsets return only that raw span and compact source
        metadata. Spans are limited to6000 characters and also support normalized assertions. Large
        sources return text/metadata/reference/edge/history continuations. A completed requested span
        covers only that interval; source_text_complete distinguishes full-source coverage.
        """
        return service.source(record_id,start,end,section=section,offset=offset,limit=limit,snapshot_id=snapshot_id)

    @mcp.tool()
    async def memory_history(
        view_id: str, version_id: int | None = None,
        section: Literal['overview','summary','evidence','edges','metadata','versions','routes','checks'] = 'overview',
        offset: int = 0, limit: int = 6000, snapshot_id: int | None = None,
        presentation_cutoff: str | None = None, name_watermark: int | None = None,
    ) -> dict:
        """Read immutable versions, freshness checks and route revisions. Sections summary/evidence/edges/metadata require
        version_id; versions pins the maximum version ID. routes/checks pin their upper revision/check snapshot_id. checks optionally filters version_id.
        Section content is exact text or JSON streamed by character offset; concatenate every page
        before decoding JSON or interpreting conditions. current_transport_status is live invocation
        metadata outside the immutable content and pinned historical snapshot.
        Follow this requested section's continuation until complete;
        optional proof sections are not required solely because a query envelope is paged.
        """
        return service.history(view_id,version_id=version_id,section=section,offset=offset,
                               limit=limit,snapshot_id=snapshot_id,presentation_cutoff=presentation_cutoff,name_watermark=name_watermark)



def _query_with_connection(config, query, scope, valid_as_of, recorded_as_of, request_session_id=None):
    """A slow model call never borrows the stdio event loop's SQLite connection."""
    from . import query_timing as timing
    from contextlib import ExitStack
    with ExitStack() as stack:
        with timing.span('service_setup'):
            store = stack.enter_context(MemoryStore(config.db))
            service = config.service(store)
        return service.query(query, scope, valid_as_of, recorded_as_of,request_session_id=request_session_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('--provider', choices=('none', 'subprocess', 'local', 'codex'))
    parser.add_argument('--model-command-json', default='[]')
    parser.add_argument('--endpoint')
    parser.add_argument('--model')
    parser.add_argument('--review-model',help='Optional explicit model for semantic verification tasks')
    parser.add_argument('--reasoning-effort',help='Codex writer effort; reviewer inherits unless overridden')
    parser.add_argument('--review-reasoning-effort',help='Optional separate Codex reviewer effort')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--embedding-mode', choices=('lexical', 'fastembed'), default='lexical')
    parser.add_argument('--embedding-model', default=MULTILINGUAL_MODEL)
    parser.add_argument('--embedding-cache')
    parser.add_argument('--worker-idle-seconds', type=float, default=30)
    parser.add_argument('--source-workers', type=int, default=2)
    parser.add_argument('--processing-session-ids-json', default=None,
                        help='Startup scheduler allowlist; null=global, []=disabled. Does not filter knowledge.')
    parser.add_argument('--no-background', action='store_true')
    parser.add_argument('--worker-config-json', help=argparse.SUPPRESS)
    parser.add_argument('--worker-limit', type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker_config_json:
            config = ServiceConfig(**json.loads(args.worker_config_json))
            run_worker(config, args.worker_limit)
        else:
            if not args.db or not args.provider:
                parser.error('--db and --provider are required')
            command = json.loads(args.model_command_json)
            if not isinstance(command, list):
                raise ValueError('model-command-json must be an argument array')
            config = ServiceConfig(args.db, args.provider, tuple(command), args.endpoint, args.model,
                                   args.timeout, args.embedding_mode, args.embedding_model,
                                   args.embedding_cache, args.worker_idle_seconds, args.source_workers,
                                   review_model=args.review_model,reasoning_effort=args.reasoning_effort,
                                   review_reasoning_effort=args.review_reasoning_effort,
                                   processing_session_ids=json.loads(args.processing_session_ids_json)
                                       if args.processing_session_ids_json is not None else None)
            create_server(config, background=not args.no_background).run(transport='stdio')
    except (OSError, TypeError, ValueError) as error:
        parser.exit(1, f'error: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
