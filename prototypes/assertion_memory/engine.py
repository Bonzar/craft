"""Repeatable question views with separately retained evidence and history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Collection
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .capture import declared_kind
from .evidence import (_EVIDENCE_RELATIONS, _MANDATORY_RELATIONS, _expand_evidence,
                       _can_reach, _mandatory_adjacency, _adjacency, _reachable)
from .overview import _include_observation_date, _include_required_evidence, _request, _request_batches, PROJECTION_PROTOCOL
from .model import CandidateSearch, Model, ModelError, ModelRequest, PROMPT_SERIALIZATION, SemanticUnavailable
from .storage import MemoryStore, _timestamp
from .publication_fences import check_guard
from .query_snapshot import capture as capture_snapshot, freshness as snapshot_freshness, receipt as check_receipt, published_winner
from .question_routes import select_sources, related_sources, related_ids

if TYPE_CHECKING:
    from .queue import CaptureQueue


@dataclass(frozen=True)
class ViewSpec:
    id: str
    title: str
    query: str
    scopes: tuple[str, ...] = ()
    root_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    max_seeds: int = 8

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.id, self.title, self.query)):
            raise ValueError("view id, title and query are required")
        if isinstance(self.max_seeds, bool) or not isinstance(self.max_seeds, int) or self.max_seeds < 1:
            raise ValueError("max_seeds must be a positive integer")
        for field in ("scopes", "root_ids", "depends_on"):
            values = getattr(self, field)
            if not isinstance(values, (list, tuple)) or not all(isinstance(item, str) and item.strip() for item in values):
                raise ValueError(f"{field} must contain nonempty strings")
            object.__setattr__(self, field, tuple(dict.fromkeys(values)))
        if self.id in self.depends_on:
            raise ValueError("a view cannot depend on itself")


@dataclass(frozen=True)
class ViewVersion:
    id: int
    view_id: str
    summary: str
    evidence: list[dict]
    edges: list[dict]
    source_version: int
    created_at: str
    valid_as_of: str | None
    recorded_as_of: str | None
    upstream: dict[str, int]
    missing_roots: list[str]
    pipeline_key: str | None = None
    overview_review: dict | None = None


@dataclass(frozen=True)
class BranchReview:
    view_id: str
    assertion_id: str
    decision: str
    reviewer: str
    reason: str
    recorded_at: str
    proposal: dict


class MemoryEngine:
    def __init__(
        self, store: MemoryStore, model: Model | None = None,
        candidate_search: CandidateSearch | None = None, *, model_key: str | None = None,
        candidate_key: str | None = None, verify_overviews: bool = False,
        naming_model: Model | None = None,
    ) -> None:
        self.store = store
        if type(verify_overviews) is not bool:
            raise ValueError("verify_overviews must be a boolean")
        self.verify_overviews = verify_overviews
        self.model = model
        self.naming_model = naming_model
        self.candidates = candidate_search or CandidateSearch()
        model_key = model_key or (type(model).__qualname__ if model is not None else "extractive")
        candidate_key = candidate_key or getattr(self.candidates, "identity", type(self.candidates).__qualname__)
        review_key = []
        if verify_overviews:
            from .overview_review import PROTOCOL, DELIVERY_PROTOCOL
            from .overview_projection import REPAIR_SERIALIZATION
            review_key = [PROTOCOL, DELIVERY_PROTOCOL, REPAIR_SERIALIZATION]
        from .overview_source_proof import SOURCE_PROOF_PROTOCOL
        from .topic_discovery import DISCOVERY_PROTOCOL
        from .topic_hierarchy import HIERARCHY_PROTOCOL
        from .discovery_progress import PROGRESS_PROTOCOL
        from .background_overviews import AUTOMATIC_LEAF_SCHEDULING_PROTOCOL
        from .recorded_history import RECORDED_HISTORY_PROTOCOL
        from .answer_relation_gaps import FRESHNESS_PROTOCOL
        self.pipeline_key = hashlib.sha256(_json([
            model_key, candidate_key, getattr(self.candidates, "ranking_version", None), "temporal-composed-evidence-v26", PROMPT_SERIALIZATION, PROJECTION_PROTOCOL, DISCOVERY_PROTOCOL, HIERARCHY_PROTOCOL, PROGRESS_PROTOCOL, AUTOMATIC_LEAF_SCHEDULING_PROTOCOL, SOURCE_PROOF_PROTOCOL, RECORDED_HISTORY_PROTOCOL, FRESHNESS_PROTOCOL, *review_key,
        ]).encode()).hexdigest()
        with store.transaction(write=True) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_views (
                    id TEXT PRIMARY KEY, definition TEXT NOT NULL,
                    current_id INTEGER, checked_version INTEGER, error TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_view_versions (
                    id INTEGER PRIMARY KEY, view_id TEXT NOT NULL REFERENCES memory_views(id),
                    fingerprint TEXT NOT NULL, snapshot TEXT NOT NULL,
                    UNIQUE (view_id, fingerprint)
                )
            """)
            # Material identity excludes validation runtime metadata. Existing
            # immutable snapshots gain an indexed identity without being rewritten.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_view_versions)")}
            if "material_fingerprint" not in columns:
                connection.execute("ALTER TABLE memory_view_versions ADD COLUMN material_fingerprint TEXT")
                for row in connection.execute("SELECT id, snapshot FROM memory_view_versions").fetchall():
                    connection.execute("UPDATE memory_view_versions SET material_fingerprint = ? WHERE id = ?",
                                       (_material_fingerprint(_load_version(row)), row[0]))
            connection.execute("CREATE INDEX IF NOT EXISTS memory_view_material_identity "
                               "ON memory_view_versions(view_id, material_fingerprint)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_view_checks (
                    id INTEGER PRIMARY KEY, view_id TEXT NOT NULL REFERENCES memory_views(id),
                    version_id INTEGER NOT NULL REFERENCES memory_view_versions(id),
                    source_version INTEGER NOT NULL, coverage_version INTEGER NOT NULL,
                    valid_cutoff TEXT NOT NULL, recorded_cutoff TEXT NOT NULL,
                    pipeline_key TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
                    checked_at TEXT NOT NULL, overview_review TEXT
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_view_checks)")}
            for name in ("query_snapshot", "freshness"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE memory_view_checks ADD COLUMN {name} TEXT")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_views)")}
            if "current_check_id" not in columns:
                connection.execute("ALTER TABLE memory_views ADD COLUMN current_check_id INTEGER")
            connection.execute("CREATE INDEX IF NOT EXISTS memory_view_check_corpus ON memory_view_checks "
                "(view_id,valid_cutoff,recorded_cutoff,pipeline_key,json_extract(freshness,'$.covered_corpus_revision'),id)")
            connection.execute("CREATE INDEX IF NOT EXISTS memory_view_check_inputs "
                               "ON memory_view_checks(view_id, input_fingerprint)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_historical_checks (
                    view_id TEXT NOT NULL REFERENCES memory_views(id),
                    valid_cutoff TEXT NOT NULL, recorded_cutoff TEXT NOT NULL, pipeline_key TEXT NOT NULL,
                    version_id INTEGER NOT NULL REFERENCES memory_view_versions(id), source_version INTEGER NOT NULL,
                    PRIMARY KEY (view_id, valid_cutoff, recorded_cutoff, pipeline_key)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memory_branch_reviews (
                    view_id TEXT NOT NULL REFERENCES memory_views(id),
                    assertion_id TEXT NOT NULL REFERENCES memory_assertions(assertion_id),
                    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
                    reviewer TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    proposal TEXT NOT NULL,
                    PRIMARY KEY (view_id, assertion_id)
                )
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS memory_branch_review_insert
                AFTER INSERT ON memory_branch_reviews WHEN new.decision = 'accepted'
                BEGIN
                    UPDATE memory_state SET version = version + 1 WHERE singleton = 1;
                END
            """)

            from .view_names import ensure_schema as ensure_name_schema
            ensure_name_schema(connection)
            from .view_name_work import ensure_schema as ensure_name_work_schema
            ensure_name_work_schema(connection)
            from .run_provenance import schema as provenance_schema
            provenance_schema(connection)

    def prepare_view_name(self, view, **kwargs):
        from .view_names import prepare
        return prepare(self, view, **kwargs)

    def review_view_name(self, candidate, **kwargs):
        from .view_names import review
        return review(self, candidate, **kwargs)

    def append_view_name(self, approved, **kwargs):
        from .view_names import append
        return append(self, approved, **kwargs)

    def resolve_view_name(self, view_id, **kwargs):
        from .view_names import resolve
        return resolve(self, view_id, **kwargs)

    def capture_presentation_snapshot(self):
        from .view_names import capture
        return capture(self)

    def define_named_view(self, spec, **kwargs):
        from .view_names import define_named
        return define_named(self, spec, **kwargs)

    def define_view(self, spec: ViewSpec) -> None:
        definition = _json(asdict(spec))
        with self.store.transaction(write=True) as connection:
            check_guard(self, connection)
            existing = connection.execute(
                "SELECT definition FROM memory_views WHERE id = ?", (spec.id,),
            ).fetchone()
            if existing:
                if existing[0] != definition:
                    raise ValueError(f"view {spec.id!r} already has a different definition")
                return
            for dependency in spec.depends_on:
                row = connection.execute(
                    "SELECT definition FROM memory_views WHERE id = ?", (dependency,),
                ).fetchone()
                if not row:
                    raise ValueError(f"unknown dependency {dependency!r}")
                upstream = ViewSpec(**json.loads(row[0]))
                if spec.scopes and (not upstream.scopes or not set(upstream.scopes) <= set(spec.scopes)):
                    raise ValueError("dependency scope must fit inside the view scope")
            connection.execute(
                "INSERT INTO memory_views (id, definition) VALUES (?, ?)", (spec.id, definition),
            )

    def list_views(self) -> list[ViewSpec]:
        with self.store.transaction() as connection:
            rows = connection.execute("SELECT definition FROM memory_views ORDER BY id").fetchall()
        return [ViewSpec(**json.loads(row[0])) for row in rows]

    def _spec(self, view_id: str) -> ViewSpec:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT definition FROM memory_views WHERE id = ?", (view_id,),
            ).fetchone()
        if not row:
            raise ValueError(f"unknown view {view_id!r}")
        return ViewSpec(**json.loads(row[0]))

    def review_branch(
        self, queue: CaptureQueue, view_id: str, assertion_id: str, *,
        decision: str, reviewer: str, reason: str,
    ) -> BranchReview:
        """Record an explicit decision about one grounded branch hypothesis."""
        if queue.store is not self.store:
            raise ValueError("capture queue and engine must share a store")
        if decision not in ("accepted", "rejected"):
            raise ValueError("branch review decision must be accepted or rejected")
        if not all(isinstance(value, str) and value.strip() for value in (reviewer, reason)):
            raise ValueError("branch review requires a reviewer and reason")
        with self.store.transaction(write=True) as connection:
            existing = connection.execute("""
                SELECT * FROM memory_branch_reviews WHERE view_id = ? AND assertion_id = ?
            """, (view_id, assertion_id)).fetchone()
            if existing:
                review = _load_branch_review(existing)
                if (review.decision, review.reviewer, review.reason) != (decision, reviewer, reason):
                    raise ValueError("this branch hypothesis already has a different review")
                return review
            spec = self._spec(view_id)
            assertion = self.store.get_assertion(assertion_id)
            if assertion is None or not spec.scopes or assertion.scope in spec.scopes:
                raise ValueError("branch review requires an assertion outside the branch scope")
            job = queue.get(assertion_id)
            proposal = next((
                item for item in job.proposals
                if item.get("kind") == "branch_relevance" and item.get("status") == "pending"
                and item.get("from_id") == assertion_id and item.get("view_id") == view_id
            ), None) if job and job.status == "completed" else None
            if proposal is None:
                raise ValueError("no pending branch hypothesis for this assertion and view")
            recorded_at = _now()
            connection.execute("""
                INSERT INTO memory_branch_reviews VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (view_id, assertion_id, decision, reviewer, reason, recorded_at, _json(proposal)))
        return BranchReview(view_id, assertion_id, decision, reviewer, reason, recorded_at, proposal)

    def list_branch_reviews(
        self, view_id: str | None = None, *, recorded_as_of: str | None = None,
    ) -> list[BranchReview]:
        cutoff = _timestamp(recorded_as_of) if recorded_as_of is not None else None
        with self.store.transaction() as connection:
            rows = connection.execute("""
                SELECT * FROM memory_branch_reviews
                WHERE (? IS NULL OR view_id = ?) AND (? IS NULL OR recorded_at <= ?)
                ORDER BY recorded_at, view_id, assertion_id
            """, (view_id, view_id, cutoff, cutoff)).fetchall()
        return [_load_branch_review(row) for row in rows]

    def include_reviewed_evidence(
        self, view_id: str, evidence: list[dict], assertions: dict[str, dict], *,
        recorded_as_of: str | None = None, _snapshot=None,
    ) -> list[dict]:
        """Append approved records; select_evidence adds their semantic restrictions."""
        included = {record["id"] for record in evidence}
        extra = []
        reviews = (_snapshot.reviews if _snapshot is not None else
                   self.list_branch_reviews(view_id, recorded_as_of=recorded_as_of))
        for review in reviews:
            if review.view_id != view_id:
                continue
            record = assertions.get(review.assertion_id)
            if review.decision == "accepted" and record is not None and review.assertion_id not in included:
                extra.append({
                    **record, "disposition": declared_kind(record["text"], record["metadata"]) or "assertion",
                    "branch_review": {"view_id": view_id, "reviewer": review.reviewer,
                                      "recorded_at": review.recorded_at},
                })
        return sorted([*evidence, *extra], key=lambda item: (item["recorded_at"], item["id"])) if extra else evidence

    def inspect_view(self, view_id: str) -> dict:
        spec = self._spec(view_id)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT current_id, checked_version, error, current_check_id FROM memory_views WHERE id = ?", (view_id,),
            ).fetchone()
            current_id, checked_version, error, check_id = row
            source_version = self._coverage_version(spec)
            check = check_receipt(self,check_id) if check_id is not None else None
            unchanged = (self.capture_snapshot().corpus(self,spec) ==
                         check['freshness']['covered_corpus_revision']) if check and check['freshness'] else checked_version == source_version
        status = "empty" if current_id is None else "fresh" if unchanged and not error else "stale"
        return {"id": view_id, "status": status, "current_id": current_id, "source_version": source_version, "error": error, "current_check_id": check_id}

    def inspect_historical_view(
        self, view_id: str, *, valid_as_of: str | None = None, recorded_as_of: str | None = None,
    ) -> dict:
        if valid_as_of is None and recorded_as_of is None:
            raise ValueError("historical view requires a cutoff")
        with self.store.transaction() as connection:
            row = connection.execute("""
                SELECT version_id, source_version FROM memory_historical_checks
                WHERE view_id = ? AND valid_cutoff = ? AND recorded_cutoff = ? AND pipeline_key = ?
            """, (view_id, valid_as_of or "", recorded_as_of or "", self.pipeline_key)).fetchone()
            check = connection.execute(
                "SELECT freshness FROM memory_view_checks WHERE view_id=? AND version_id=? "
                "AND coalesce(json_extract(overview_review,'$.certificate.kind'),'') != 'answer_applicability' AND valid_cutoff=? AND recorded_cutoff=? AND pipeline_key=? "
                "ORDER BY source_version DESC,json_extract(query_snapshot,'$.captured_at') DESC,id DESC LIMIT 1",
                (view_id,row[0],valid_as_of or '',recorded_as_of or '',self.pipeline_key)).fetchone() if row else None
            freshness = json.loads(check[0]) if check and check[0] else None
            unchanged = (self.capture_snapshot(valid_as_of=valid_as_of,recorded_as_of=recorded_as_of).corpus(self,self._spec(view_id))
                         == freshness['covered_corpus_revision']) if freshness else bool(row and row[1] == self.store.version)
        status = "empty" if row is None else "fresh" if unchanged else "stale"
        return {"status": status, "current_id": row[0] if row else None}

    def _coverage_version(self, spec: ViewSpec, active_at: str | None = None) -> int:
        active_at = active_at or _now()
        if not spec.scopes:
            return self.store.scope_version(active_at=active_at)
        roots = set(spec.root_ids)
        dependencies = list(spec.depends_on)
        visited = set()
        while dependencies:
            view_id = dependencies.pop()
            if view_id not in visited:
                visited.add(view_id)
                dependency = self._spec(view_id)
                roots.update(dependency.root_ids)
                dependencies.extend(dependency.depends_on)
        with self.store.transaction() as connection:
            reviews = connection.execute("""
                SELECT r.assertion_id FROM memory_branch_reviews r
                JOIN memory_assertions a ON a.assertion_id = r.assertion_id
                WHERE r.view_id = ? AND r.decision = 'accepted' AND r.recorded_at <= ?
                  AND a.recorded_at <= ? AND (a.valid_at IS NULL OR a.valid_at <= ?)
            """, (spec.id, active_at, active_at, active_at)).fetchall()
            from .source_revision import selection
            inactive = selection(connection, active_at).inactive_ids
            reviews = [row for row in reviews if row[0] not in inactive]
        roots.update(row[0] for row in reviews)
        return len(reviews) + self.store.scope_version(
            spec.scopes, follow_kinds=_EVIDENCE_RELATIONS, root_ids=roots, active_at=active_at,
        )

    def find_views(
        self, query: str, scopes: tuple[str, ...] = (), *,
        view_ids: Collection[str] | None = None, limit: int = 10,
    ) -> list[dict]:
        if view_ids is None:
            specs = self.list_views()
        else:
            with self.store.transaction() as connection:
                rows = connection.execute('SELECT definition FROM memory_views WHERE id IN '
                    '(SELECT value FROM json_each(?)) ORDER BY id', (_json(list(view_ids)),)).fetchall()
            specs = [ViewSpec(**json.loads(row[0])) for row in rows]
        views = {spec.id: spec for spec in specs if not scopes or spec.scopes == scopes}
        documents={key:spec.query for key,spec in views.items()}
        if documents:
            from .navigation_overview import accepted_navigation_review
            with self.store.transaction() as connection:
                rows=connection.execute('SELECT d.id,v.snapshot FROM memory_views d JOIN memory_view_versions v '
                    'ON v.id=d.current_id WHERE d.id IN (SELECT value FROM json_each(?))',(_json(list(documents)),)).fetchall()
            for row in rows:
                material=json.loads(row['snapshot'])
                if accepted_navigation_review(material.get('overview_review')):
                    documents[row['id']]+='\n'+material['summary']
        matches = self.candidates.rank(query, documents, limit=limit)
        from .view_names import presentation
        pinned = self.capture_presentation_snapshot()
        return [{**asdict(match), "title": views[match.id].title,
                 **presentation(self,match.id,snapshot=pinned)[0]} for match in matches]

    def prepare_question(
        self, question: str, scopes: tuple[str, ...] = (), *,
        valid_as_of: str | None = None, recorded_as_of: str | None = None, _snapshot=None,
    ) -> dict:
        from .question_routes import prepare_question
        return prepare_question(self, question, scopes, valid_as_of=valid_as_of, recorded_as_of=recorded_as_of, _snapshot=_snapshot)

    def question_history(
        self, question: str, scopes: tuple[str, ...] = (), *,
        valid_as_of: str | None = None, recorded_as_of: str | None = None,
    ) -> list[dict]:
        from .question_routes import get_history
        return get_history(self, question, scopes, valid_as_of=valid_as_of, recorded_as_of=recorded_as_of)

    def view_history(self, view_id: str) -> list[ViewVersion]:
        self._spec(view_id)
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT id, snapshot FROM memory_view_versions WHERE view_id = ? ORDER BY id", (view_id,),
            ).fetchall()
        return [_load_version(row) for row in rows]

    def view_checks(self, view_id: str, *, snapshot_id: int | None = None) -> list[dict]:
        """Immutable validation certificates, separately from material snapshots."""
        self._spec(view_id)
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_view_checks WHERE view_id = ? AND (? IS NULL OR id <= ?) ORDER BY id",
                (view_id, snapshot_id, snapshot_id),
            ).fetchall()
        return [_load_check(row) for row in rows]

    def latest_check(self, version: ViewVersion) -> dict | None:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_view_checks WHERE view_id = ? AND version_id = ? "
                "AND coalesce(json_extract(overview_review,'$.certificate.kind'),'') != 'answer_applicability' AND valid_cutoff = ? AND recorded_cutoff = ? AND pipeline_key = ? ORDER BY id DESC LIMIT 1",
                (version.view_id, version.id, version.valid_as_of or "", version.recorded_as_of or "", self.pipeline_key),
            ).fetchone()
        return _load_check(row) if row else None

    def get_version(self, version_id: int) -> ViewVersion:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT id, snapshot FROM memory_view_versions WHERE id = ?", (version_id,),
            ).fetchone()
        if not row:
            raise ValueError(f"unknown view version {version_id}")
        return _load_version(row)

    def _refresh_specs(self, excluded):
        if not excluded:
            return self.list_views()
        excluded = frozenset(excluded)
        with self.store.transaction() as connection:
            revision = connection.execute('SELECT count(*),coalesce(max(rowid),0) FROM memory_views').fetchone()
            identity = (excluded, tuple(revision))
            cached = getattr(self, '_refresh_spec_cache', None)
            if cached and cached[0] == identity:
                return cached[1]
            rows = connection.execute("WITH RECURSIVE blocked(id) AS (SELECT value FROM json_each(?) UNION "
                "SELECT v.id FROM memory_views v, json_each(v.definition,'$.depends_on') d JOIN blocked b ON d.value=b.id) "
                "SELECT definition FROM memory_views WHERE id NOT IN (SELECT id FROM blocked) ORDER BY id",
                (_json(sorted(excluded)),)).fetchall()
        specs = [ViewSpec(**json.loads(row[0])) for row in rows]
        self._refresh_spec_cache = (identity, specs)
        return specs

    def _impact_selection(self, assertion_id: str, *, exclude_view_ids=()) -> tuple[list[str], dict[str, list[str]]]:
        """Select obligations plus bounded hypotheses; read freshness stays conservative."""
        assertion = self.store.get_assertion(assertion_id)
        if assertion is None:
            raise ValueError(f"unknown assertion {assertion_id!r}")
        specs = self._refresh_specs(exclude_view_ids)
        if not specs:
            return [], {}
        edges = [{**asdict(edge), "id": edge.edge_id} for edge in self.store.list_edges()]
        mandatory = _mandatory_adjacency(edges, reverse=True)
        changed, _ = _reachable({assertion_id}, mandatory)
        # Navigation is one direct hop, as in select_evidence. Every such neighbour
        # retains its complete mandatory component; optional chains do not fan out.
        neighbours = {key for edge in edges if edge["kind"] in _EVIDENCE_RELATIONS - {"depends_on"}
                      and changed.intersection((edge["from_id"], edge["to_id"]))
                      for key in (edge["from_id"], edge["to_id"])}
        linked, _ = _reachable(changed | neighbours, mandatory)
        encoded = _json(sorted(linked))
        with self.store.transaction() as connection:
            known = {row[0] for row in connection.execute("""
                SELECT view_id FROM memory_branch_reviews
                WHERE decision = 'accepted' AND assertion_id IN (SELECT value FROM json_each(?))
                UNION
                SELECT d.id FROM memory_views d JOIN memory_view_versions v ON v.id = d.current_id
                WHERE EXISTS (
                    SELECT 1 FROM json_each(v.snapshot, '$.evidence') e
                    WHERE json_extract(e.value, '$.id') IN (SELECT value FROM json_each(?))
                )
            """, (encoded, encoded))}
        known.intersection_update(view.id for view in specs)
        known.update(view.id for view in specs if linked.intersection(view.root_ids))
        eligible = {view.id for view in specs if not view.scopes or assertion.scope in view.scopes}
        # Scope is eligibility, not a topic label. New cross-scope relevance still
        # needs an existing explicit root, graph relation or accepted review.
        hypotheses = self.find_views(assertion.text, view_ids=eligible - known, limit=8)
        selected = known | {match["id"] for match in hypotheses}
        dependents: dict[str, list[str]] = {}
        for spec in specs:
            for dependency in (*spec.depends_on, *related_ids(self, spec)):
                dependents.setdefault(dependency, []).append(spec.id)
        return sorted(selected), dependents

    def impact(self, assertion_id: str) -> list[str]:
        """Report potential impact, including dependents gated during refresh."""
        selected, dependents = self._impact_selection(assertion_id)
        impacted = set(selected)
        pending = list(selected)
        while pending:
            for view_id in dependents.get(pending.pop(), []):
                if view_id not in impacted:
                    impacted.add(view_id)
                    pending.append(view_id)
        return sorted(impacted)

    def suggest_links(self, assertion_id: str, *, limit: int = 8) -> list[dict]:
        assertion = self.store.get_assertion(assertion_id)
        if assertion is None:
            raise ValueError(f"unknown assertion {assertion_id!r}")
        records = {
            row.assertion_id: row for row in self.store.list_assertions()
            if row.assertion_id != assertion_id
        }
        matches = self.candidates.rank(assertion.text, {key: row.text for key, row in records.items()}, limit=limit)
        return [{**asdict(match), "assertion": asdict(records[match.id])} for match in matches]

    def refresh_from(self, assertion_id: str, *, exclude_view_ids=()) -> list[ViewVersion]:
        from .run_provenance import execution
        with execution(self,owner_id='refresh:'+assertion_id,assertion_id=assertion_id):
            return self._refresh_from(assertion_id,exclude_view_ids=exclude_view_ids)

    def _refresh_from(self, assertion_id: str, *, exclude_view_ids=()) -> list[ViewVersion]:
        pending, dependents = self._impact_selection(assertion_id, exclude_view_ids=exclude_view_ids)
        with self.store.transaction() as connection:
            previous = {row["id"]: row["current_id"] for row in connection.execute(
                "SELECT id, current_id FROM memory_views")}
        refreshed = {}
        while pending:
            view_id = pending.pop(0)
            if view_id in refreshed:
                continue
            try:
                result = self.build_view(view_id)
            except SemanticUnavailable as error:
                result = getattr(error,'failure_version',None)
                audit = result.overview_review if result is not None else {}
                if (getattr(error,'operational_contract',False) or not getattr(error,'failure_check',None)
                        or not audit or audit.get('failure_classification')!='semantic'
                        or audit.get('status')!='unresolved' or audit.get('failure_proof') is not True):
                    raise
                # The persisted negative result completes this attempt, never
                # promotes a current answer, and does not abort sibling work.
            refreshed[view_id] = result
            if previous[view_id] != result.id:
                pending.extend(dependents.get(view_id, []))
        return list(refreshed.values())

    def capture_snapshot(self, *, valid_as_of=None, recorded_as_of=None):
        return capture_snapshot(self, valid_as_of, recorded_as_of, captured_at=_now())

    def build_view(
        self, view_id: str, *, valid_as_of: str | None = None, recorded_as_of: str | None = None,
        _memo: dict | None = None, _snapshot=None, _receipts: dict | None = None, _recovery_ids=(),
    ) -> ViewVersion:
        spec = self._spec(view_id)
        if _snapshot is None and valid_as_of is None and recorded_as_of is None:
            from .question_routes import owned_cutoffs
            owned=owned_cutoffs(self,spec)
            valid_as_of,recorded_as_of=owned.get('valid_as_of'),owned.get('recorded_as_of')
        snapshot = _snapshot or self.capture_snapshot(valid_as_of=valid_as_of, recorded_as_of=recorded_as_of)
        memo = {} if _memo is None else _memo
        receipts = {} if _receipts is None else _receipts
        from .build_proof import acquisition_state, persist_failure
        state = acquisition_state(self, spec, snapshot)
        state['recovery_ids']=tuple(key for key in _recovery_ids if key in snapshot.assertions and
            (not spec.scopes or snapshot.assertions[key]['scope'] in spec.scopes))[:32]
        try:
            if view_id not in memo:
                from .assembly_traversal import dependency_order
                for child in dependency_order(self, spec, memo):
                    self.build_view(child.id, valid_as_of=valid_as_of, recorded_as_of=recorded_as_of,
                                    _memo=memo, _snapshot=snapshot, _receipts=receipts)
                memo[view_id] = self._build(spec, valid_as_of, recorded_as_of, memo, snapshot, receipts, state)
            return memo[view_id]
        except (ModelError, ValueError, sqlite3.Error, OSError) as error:
            if isinstance(error, (sqlite3.Error, OSError)):
                wrapped = ModelError(f'view persistence failed: {error}')
                wrapped.__cause__ = error
                wrapped.acquisition = getattr(error,'acquisition',{})
                error = wrapped
            from .run_provenance import active, publication
            if active(self) is None:
                persist_failure(self, spec, snapshot, error, state)
            else:
                # The failure writer has its own publication path. Its receipt
                # and this causal output still share one owned write boundary.
                with self.store.transaction(write=True) as connection:
                    prior_max = connection.execute('SELECT coalesce(max(id),0) FROM memory_view_versions').fetchone()[0]
                    persist_failure(self, spec, snapshot, error, state)
                    failed = getattr(error,'failure_version',None)
                    failed_check = getattr(error,'failure_check',None)
                    if failed is not None and failed_check is not None:
                        publication(self,connection,failed,failed_check['id'],produced=failed.id>prior_max)
            if valid_as_of is None and recorded_as_of is None:
                try:
                    with self.store.transaction(write=True) as connection:
                        check_guard(self, connection)
                        connection.execute("UPDATE memory_views SET error = ? WHERE id = ?", (str(error), view_id))
                except (sqlite3.Error, OSError) as persistence_error:
                    state['persistence_error'] = str(persistence_error)
            raise error

    def _build(self, spec, valid_as_of, recorded_as_of, memo, snapshot, receipts, state):
        from .build_pipeline import build
        return build(self, spec, valid_as_of, recorded_as_of, memo, snapshot, receipts, state)

    def select_evidence(
        self, spec: ViewSpec, seeds: set[str], assertions: dict[str, dict], edges: list[dict], *,
        recorded_as_of: str | None = None, _snapshot=None, include_navigation: bool = True,
    ) -> tuple[list[dict], list[dict]]:
        """Keep semantic restrictions whole; rank a bounded set of navigation neighbours.

        Navigation is only considered from the original search anchors. A selected
        neighbour brings its own restrictions, but never starts another navigation
        walk. Approved branch records receive the same mandatory closure.
        """
        anchors = seeds & assertions.keys()
        evidence, links = _expand_evidence(anchors, assertions, edges)
        reviewed = self.include_reviewed_evidence(
            spec.id, evidence, assertions, recorded_as_of=recorded_as_of, _snapshot=_snapshot,
        )
        review_marks = {row["id"]: row["branch_review"] for row in reviewed if "branch_review" in row}
        included = {row["id"] for row in reviewed}
        evidence, links = _expand_evidence(included, assertions, edges)
        included = {row["id"] for row in evidence}
        navigation = [edge for edge in edges
                      if (edge["kind"] in _EVIDENCE_RELATIONS - _MANDATORY_RELATIONS
                          or (edge["kind"] == "depends_on" and edge["to_id"] in anchors))
                      and anchors.intersection((edge["from_id"], edge["to_id"]))]
        links += [edge for edge in navigation if {edge["from_id"], edge["to_id"]} <= included]
        neighbours = {key for edge in navigation for key in (edge["from_id"], edge["to_id"])
                      if key not in included and key in assertions}
        ranked = self.candidates.rank(
            spec.query, {key: assertions[key]["text"] for key in sorted(neighbours)}, limit=spec.max_seeds,
        ) if include_navigation else []
        for match in ranked:
            proposed, proposed_links = _expand_evidence(included | {match.id}, assertions, edges)
            proposed_ids = {row["id"] for row in proposed}
            proposed_links += [edge for edge in navigation
                               if {edge["from_id"], edge["to_id"]} <= proposed_ids]
            try:
                _request(spec, proposed, proposed_links, [])
            except ModelError:
                # Optional context never crowds out required restrictions, even
                # when that neighbour has a large semantic component of its own.
                continue
            evidence, links, included = proposed, proposed_links, proposed_ids
        links = sorted({edge["id"]: edge for edge in links}.values(), key=lambda edge: edge["id"])
        for row in evidence:
            if row["id"] in review_marks:
                row["branch_review"] = review_marks[row["id"]]
        return evidence, links

    def _publish(
        self, spec: ViewSpec, version: ViewVersion, fingerprint: str,
        source_version: int, checked_version: int | None, *, coverage_version: int,
        review: dict | None, snapshot, receipts, retrieval_lineage=(), presentation=None,
    ) -> ViewVersion:
        with self.store.transaction(write=True) as connection:
            check_guard(self, connection)
            freshness = snapshot_freshness(self, spec, snapshot)
            freshness['retrieval_dependencies'] = list(retrieval_lineage)
            material = _material_fingerprint(version)
            row = connection.execute(
                "SELECT id, snapshot FROM memory_view_versions WHERE view_id = ? "
                "AND material_fingerprint = ? ORDER BY id LIMIT 1", (version.view_id, material),
            ).fetchone()
            saved = _load_version(row) if row else None
            produced = False
            if saved is None:
                # A concurrent same-input winner retains its own validated
                # wording. Our review cannot certify a different draft.
                winner = published_winner(connection,spec.id,fingerprint)
                if winner:
                    saved,review,prior_check_id = winner
                    freshness['reused_validation_check_id'] = prior_check_id
            if saved is None:
                row = connection.execute(
                    "INSERT INTO memory_view_versions (view_id, fingerprint, snapshot, material_fingerprint) VALUES (?, ?, ?, ?)",
                    (version.view_id, fingerprint, _json(asdict(version)), material),
                )
                saved = replace(version, id=row.lastrowid)
                produced = True
            check_row = connection.execute(
                "INSERT INTO memory_view_checks (view_id, version_id, source_version, coverage_version, "
                "valid_cutoff, recorded_cutoff, pipeline_key, input_fingerprint, checked_at, overview_review, query_snapshot, freshness) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (saved.view_id, saved.id, source_version, coverage_version, saved.valid_as_of or "",
                 saved.recorded_as_of or "", self.pipeline_key, fingerprint, freshness["checked_at"],
                 _json(review) if review is not None else None, _json(snapshot.descriptor()), _json(freshness)),
            )
            check_id = check_row.lastrowid
            from .source_omission import persist as persist_omissions
            if review is not None:persist_omissions(self,connection,spec,snapshot,review,check_id)
            if checked_version is not None:
                connection.execute(
                    "UPDATE memory_views SET current_id=?,checked_version=?,error=NULL,current_check_id=? "
                    "WHERE id=? AND (current_check_id IS NULL OR "
                    "EXISTS (SELECT 1 FROM memory_view_checks c WHERE c.id=memory_views.current_check_id "
                    "AND (c.source_version < ? OR (c.source_version = ? AND "
                    "json_extract(c.query_snapshot,'$.captured_at') <= ?))))",
                    (saved.id,checked_version,check_id,saved.view_id,source_version,source_version,snapshot.captured_at))
            else:
                connection.execute("""
                    INSERT INTO memory_historical_checks VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (view_id, valid_cutoff, recorded_cutoff, pipeline_key) DO UPDATE SET
                        version_id = excluded.version_id, source_version = excluded.source_version
                    WHERE NOT EXISTS (
                        SELECT 1 FROM memory_view_checks c WHERE coalesce(json_extract(c.overview_review,'$.certificate.kind'),'') != 'answer_applicability' AND c.view_id=excluded.view_id
                        AND c.valid_cutoff=excluded.valid_cutoff AND c.recorded_cutoff=excluded.recorded_cutoff
                        AND c.pipeline_key=excluded.pipeline_key
                        AND (c.source_version > excluded.source_version OR
                             (c.source_version = excluded.source_version AND
                              json_extract(c.query_snapshot,'$.captured_at') > ?)))
                """, (saved.view_id, saved.valid_as_of or "", saved.recorded_as_of or "",
                       self.pipeline_key, saved.id, source_version, snapshot.captured_at))
            from .view_names import publish_pair,content_digest
            publish_pair(self,spec,saved,presentation if content_digest(saved)==material else None,source_check_id=check_id)
            receipts[spec.id] = check_receipt(self,check_id)
            from .run_provenance import publication
            publication(self,connection,saved,check_id,produced=produced)
        return self.get_version(saved.id)

    def _summarize(
        self, spec: ViewSpec, evidence: list[dict], links: list[dict], missing: list[str],
        *, valid_as_of: str | None = None, recorded_as_of: str | None = None,
    ) -> str:
        if not evidence:
            return "В этой выборке нет оснований для ответа."
        if self.model is None:
            labels = {
                "conflict": "Спор", "exception": "Исключение", "proposal": "Предложение", "observation": "Наблюдение",
                "implementation": "Сообщение о реализации", "inference": "Вывод",
            }
            summary = "\n".join(
                f"[{item['id']}] {labels.get(item['disposition'], item['actor'])}: {item['text']}"
                for item in evidence if item["disposition"] != "superseded"
            ) or "В выборке только заменённые утверждения."
            summary = _include_required_evidence(summary, evidence, links, include_records=False)
            return _include_observation_date(spec.query, summary, evidence)
        cutoffs = dict(valid_as_of=valid_as_of, recorded_as_of=recorded_as_of)
        batches = _request_batches(spec, evidence, links, missing, **cutoffs)
        summaries = []
        for records, batch_links in batches:
            request = _request(spec, records, batch_links, missing, partial=len(batches) > 1, **cutoffs)
            if not request.records:
                continue
            result = self.model(request)
            if not isinstance(result, str) or not result.strip():
                raise ModelError("model returned no usable overview")
            if len(batches) > 1:
                # The final view version pins both the complete evidence and each
                # bounded overview's content identity; no lossy reducer is needed.
                batch_input = [self.pipeline_key, spec.query, records, batch_links, missing, cutoffs]
                batch_id = hashlib.sha256(_json(batch_input).encode()).hexdigest()[:16]
                refs = ", ".join(f"[{row['id']}]" for row in records)
                summaries.append(f"Обзор пакета {batch_id}; основания: {refs}\n{result.strip()}")
            else:
                summaries.append(result.strip())
        summary = "\n\n".join(summaries) or "В выборке только заменённые утверждения."
        summary = _include_required_evidence(summary, evidence, links)
        return _include_observation_date(spec.query, summary, evidence)

    def model_requests(self, view: ViewVersion) -> list[ModelRequest]:
        """Export initial writer prompts; repair/review calls are pinned by audit hashes."""
        spec = self._spec(view.view_id)
        cutoffs = dict(valid_as_of=view.valid_as_of, recorded_as_of=view.recorded_as_of)
        if self.verify_overviews:
            from .overview_export import initial_writer_requests
            audit = view.overview_review or {}
            return initial_writer_requests(spec, view.evidence, view.edges, view.missing_roots, cutoffs,
                upstream_contributions=audit.get('upstream_contributions', ()),
                query_context=audit.get('query_context'), revision=audit.get('corpus_revision'))
        batches = _request_batches(spec, view.evidence, view.edges, view.missing_roots, **cutoffs)
        return [_request(spec, records, links, view.missing_roots, partial=len(batches) > 1, **cutoffs)
                for records, links in batches]

    def model_request(self, view: ViewVersion) -> ModelRequest:
        requests = self.model_requests(view)
        if len(requests) != 1:
            raise ModelError("view needs multiple bounded requests; use model_requests")
        return requests[0]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _material_fingerprint(version: ViewVersion) -> str:
    # Include complete evidence and edges, not merely prose/citation IDs. Source
    # provenance, attribution, dates, disposition and conditions are contractual.
    values = [version.view_id, version.summary, version.evidence, version.edges,
              version.valid_as_of, version.recorded_as_of, version.upstream,
              version.missing_roots, version.pipeline_key,
              {key: (version.overview_review or {}).get(key) for key in ("status", "protocol")}]
    return hashlib.sha256(_json(values).encode()).hexdigest()


def _load_check(row) -> dict:
    value = dict(row)
    value["valid_as_of"] = value.pop("valid_cutoff") or None
    value["recorded_as_of"] = value.pop("recorded_cutoff") or None
    for name in ("overview_review", "query_snapshot", "freshness"):
        value[name] = json.loads(value[name]) if value.get(name) is not None else None
    return value


def _find_version(connection, view_id: str, fingerprint: str) -> ViewVersion | None:
    row = connection.execute(
        "SELECT id, snapshot FROM memory_view_versions WHERE view_id = ? AND fingerprint = ?",
        (view_id, fingerprint),
    ).fetchone()
    return _load_version(row) if row else None


def _load_version(row) -> ViewVersion:
    snapshot = json.loads(row[1])
    snapshot["id"] = row[0]
    return ViewVersion(**snapshot)


def _load_branch_review(row) -> BranchReview:
    fields = dict(row)
    fields["proposal"] = json.loads(fields["proposal"])
    return BranchReview(**fields)
