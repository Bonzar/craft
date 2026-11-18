"""Local command-line entry point: python3 -m prototypes.assertion_memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .artifact import render_artifact
from .backup import snapshot_database
from .capture import CaptureInput
from .engine import MemoryEngine, ViewSpec
from .model import CandidateSearch, CodexModel, LocalOpenAIEmbedder, LocalOpenAIModel, SubprocessModel, format_prompt
from .queue import CaptureQueue
from .runtime_config import build_candidate_search
from .storage import MemoryStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local assertion memory prototype")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / ".local/memory.sqlite3")
    parser.add_argument("--generator", choices=("extractive", "codex", "local", "command"), default="extractive")
    parser.add_argument("--model-name")
    parser.add_argument("--model-endpoint", default="http://127.0.0.1:11434/v1/chat/completions")
    parser.add_argument("--model-command", help="JSON array of arguments for an explicitly selected model command")
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-endpoint", default="http://127.0.0.1:11434/v1/embeddings")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="append an attributed assertion; text '-' reads stdin")
    add.add_argument("id")
    add.add_argument("text")
    add.add_argument("--actor", required=True)
    add.add_argument("--source", required=True)
    add.add_argument("--episode", required=True)
    add.add_argument("--scope", default="default")
    add.add_argument("--kind")
    add.add_argument("--valid-at")
    add.add_argument("--recorded-at")
    add.add_argument("--refresh", action="store_true")

    link = commands.add_parser("link", help="append an explicit relationship between assertions")
    link.add_argument("id")
    link.add_argument("from_id")
    link.add_argument("kind")
    link.add_argument("to_id")
    link.add_argument("--condition")
    link.add_argument("--from-fragment")
    link.add_argument("--to-fragment")
    link.add_argument("--recorded-at")
    link.add_argument("--refresh", action="store_true")

    load = commands.add_parser("import", help="atomically import a local JSON fixture or export")
    load.add_argument("path", type=Path)
    commands.add_parser("export", help="export source assertions, edges and view definitions as JSON")

    search = commands.add_parser("search", help="find candidate assertions without generating an answer")
    search.add_argument("query")
    search.add_argument("--scope", action="append", default=[])
    search.add_argument("--limit", type=int, default=12)

    define = commands.add_parser("view", help="save a repeatable question as a branch")
    define.add_argument("id")
    define.add_argument("question")
    define.add_argument("--title")
    define.add_argument("--scope", action="append", default=[])
    define.add_argument("--root", action="append", default=[])
    define.add_argument("--depends-on", action="append", default=[])
    define.add_argument("--max-seeds", type=int, default=8)

    ask = commands.add_parser("ask", help="read or create a question view, with evidence")
    target = ask.add_mutually_exclusive_group(required=True)
    target.add_argument("--view")
    target.add_argument("--question")
    ask.add_argument("--scope", action="append", default=[])
    ask.add_argument("--valid-as-of")
    ask.add_argument("--recorded-as-of")
    ask.add_argument("--prompt", action="store_true", help="export evidence as a prompt for the current agent")

    artifact = commands.add_parser("artifact", help="render a reviewable implementation document to stdout")
    artifact_source = artifact.add_mutually_exclusive_group(required=True)
    artifact_source.add_argument("--view")
    artifact_source.add_argument("--snapshot", type=int)

    find = commands.add_parser("find-views", help="find reusable branches by wording or embeddings")
    find.add_argument("query")
    find.add_argument("--scope", action="append", default=[])
    review = commands.add_parser("review-branch", help="record a decision on one pending branch association")
    review.add_argument("view_id")
    review.add_argument("assertion_id")
    review.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--reason", required=True)
    reviews = commands.add_parser("branch-reviews", help="inspect recorded branch review decisions")
    reviews.add_argument("--view")
    for name in ("status", "history", "impact", "refresh", "suggest-links"):
        commands.add_parser(name).add_argument("id")
    commands.add_parser("snapshot", help="read a saved historical view version exactly").add_argument("id", type=int)
    commands.add_parser("backup", help="save a consistent copy of the entire database").add_argument("destination", type=Path)
    commands.add_parser("restore", help="restore a backup into the unused --db path").add_argument("source", type=Path)
    return parser


def _configuration(args):
    embedder = LocalOpenAIEmbedder(args.embedding_endpoint, args.embedding_model) if args.embedding_model else None
    candidates = CandidateSearch(embedder, identity=f"local:{args.embedding_endpoint}:{args.embedding_model}" if embedder else None)
    generator = None
    if getattr(args, "prompt", False):
        return None, candidates, "extractive"
    if args.generator == "codex":
        generator = CodexModel(model=args.model_name)
    elif args.generator == "local":
        if not args.model_name:
            raise ValueError("--generator local requires --model-name")
        generator = LocalOpenAIModel(args.model_endpoint, args.model_name)
    elif args.generator == "command":
        if not args.model_command:
            raise ValueError("--generator command requires --model-command")
        command = json.loads(args.model_command)
        if not isinstance(command, list) or not all(isinstance(value, str) for value in command):
            raise ValueError("--model-command must be a JSON array of strings")
        generator = SubprocessModel(command)
    identity = "extractive" if generator is None else json.dumps([
        args.generator, args.model_name, args.model_endpoint, args.model_command,
    ])
    return generator, candidates, identity


def _read_view(engine: MemoryEngine, args) -> str:
    if args.view:
        return args.view
    question = " ".join(args.question.split())
    scopes = tuple(sorted(set(args.scope)))
    existing = [view for view in engine.list_views() if view.query == question and view.scopes == scopes]
    if existing:
        return existing[0].id
    key = hashlib.sha256(json.dumps([question, scopes]).encode()).hexdigest()[:20]
    view_id = f"question-{key}"
    engine.define_named_view(ViewSpec(view_id,question,question,scopes=scopes),
        authorized_scopes=scopes,operation='automatic-cli-v1',automatic=True)
    return view_id


def _import_views(engine: MemoryEngine, definitions: list[dict]) -> None:
    pending = [ViewSpec(**item) for item in definitions]
    defined = {item.id for item in engine.list_views()}
    while pending:
        ready = [spec for spec in pending if set(spec.depends_on) <= defined]
        if not ready:
            raise ValueError("imported views have unknown or cyclic dependencies")
        for spec in ready:
            engine.define_view(spec)
            defined.add(spec.id)
            pending.remove(spec)


def _anchor(store: MemoryStore, assertion_id: str, fragment: str) -> dict:
    assertion = store.get_assertion(assertion_id)
    if assertion is None or not fragment:
        raise ValueError("fragment requires an existing assertion and nonempty text")
    start = assertion.text.find(fragment)
    if start < 0 or assertion.text.find(fragment, start + 1) >= 0:
        raise ValueError(f"fragment must match exactly once in assertion {assertion_id!r}")
    return {"start": start, "end": start + len(fragment), "text": fragment}


def _query_diagnostics(store: MemoryStore, engine: MemoryEngine, view, check: dict) -> dict:
    from .service import MemoryService, _check_descriptor

    service = MemoryService(store, engine)
    spec = next(spec for spec in engine.list_views() if spec.id == view.view_id)
    health, processing_gaps, freshness_gaps = [], False, False
    for scope in spec.scopes or (None,):
        state = service.status(scope=scope, limit=1)
        health.append({"scope": scope, **{key: state[key] for key in (
            "source_version", "job_counts", "pending_count", "error_count", "unresolved_count")}})
        applies,refresh=service._query_gap_assessment(scope,view.recorded_as_of,state,check)
        processing_gaps=True if applies is True or processing_gaps is True else None if applies is None or processing_gaps is None else False
        freshness_gaps |= refresh
    return {
        "query_snapshot": check["query_snapshot"], "freshness_check": _check_descriptor(check),
        "refresh_required": bool(freshness_gaps or view.missing_roots or check["freshness"]["refresh_required"]),
        "completeness": "incomplete" if processing_gaps is not False or view.missing_roots else "not_guaranteed",
        "source_status": {"scopes": list(spec.scopes), "missing_roots": view.missing_roots,
                          "queue_counts_perspective": "latest_scope_health", "latest_scope_health": health,
                          "processing_gaps_apply_to_query": processing_gaps},
    }


def _dispatch(args, store: MemoryStore, engine: MemoryEngine, candidates: CandidateSearch):
    now = datetime.now(timezone.utc).isoformat()
    if args.command == "add":
        text = sys.stdin.read() if args.text == "-" else args.text
        record = CaptureQueue(store).capture(CaptureInput(
            assertion_id=args.id, text=text, actor=args.actor, source=args.source,
            episode=args.episode, scope=args.scope, valid_at=args.valid_at,
            recorded_at=args.recorded_at, metadata={"kind": args.kind} if args.kind else None,
        ))
        return {"assertion": asdict(record), "refreshed": [item.id for item in engine.refresh_from(args.id)] if args.refresh else []}
    if args.command == "link":
        with store.transaction(write=True):
            existing = store.get_edge(args.id)
            recorded_at = args.recorded_at or (existing.recorded_at if existing else now)
            metadata = {"condition": args.condition} if args.condition else {}
            if args.from_fragment is not None:
                metadata["from_anchor"] = _anchor(store, args.from_id, args.from_fragment)
            if args.to_fragment is not None:
                metadata["to_anchor"] = _anchor(store, args.to_id, args.to_fragment)
            edge = store.append_edge(
                edge_id=args.id, from_id=args.from_id, to_id=args.to_id, kind=args.kind,
                recorded_at=recorded_at, metadata=metadata,
            )
        return {"edge": asdict(edge), "refreshed": [item.id for item in engine.refresh_from(args.from_id)] if args.refresh else []}
    if args.command == "import":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        with store.transaction(write=True):
            for record in payload.get("assertions", []):
                store.append_assertion(**record)
            for edge in payload.get("edges", []):
                store.append_edge(**edge)
            _import_views(engine, payload.get("views", []))
        return {"source_version": store.version}
    if args.command == "export":
        with store.transaction():
            presentation=engine.capture_presentation_snapshot()
            specs=engine.list_views()
            return {"assertions": [asdict(item) for item in store.list_assertions()],
                    "edges": [asdict(item) for item in store.list_edges()],
                    "views": [asdict(item) for item in specs],
                    "presentation_snapshot":presentation,
                    "view_presentation":{item.id:engine.resolve_view_name(item.id,**presentation) for item in specs}}
    if args.command == "search":
        scope = args.scope or None
        records = store.list_assertions(scopes=scope)
        ranked = candidates.rank(args.query, {row.assertion_id: row.text for row in records}, limit=args.limit)
        return {"text": [asdict(item) for item in store.search(args.query, scopes=scope, limit=args.limit)],
                "candidates": [asdict(item) for item in ranked]}
    if args.command == "view":
        spec = ViewSpec(args.id, args.title or args.question, args.question, args.scope, args.root, args.depends_on, args.max_seeds)
        if args.title:
            engine.define_view(spec)
        else:
            spec=engine.define_named_view(spec,authorized_scopes=spec.scopes,operation='automatic-cli-v1',automatic=True)
        return asdict(spec)
    if args.command == "ask":
        receipts = {}
        view = engine.build_view(_read_view(engine, args), valid_as_of=args.valid_as_of,
                                 recorded_as_of=args.recorded_as_of, _receipts=receipts)
        if args.prompt:
            return format_prompt(engine.model_request(view))
        return {**asdict(view), **_query_diagnostics(store, engine, view, receipts[view.view_id])}
    if args.command == "find-views":
        return engine.find_views(args.query, tuple(sorted(set(args.scope))))
    if args.command == "artifact":
        receipts = {}
        version = (engine.get_version(args.snapshot) if args.snapshot is not None
                   else engine.build_view(args.view, _receipts=receipts))
        naming=engine.resolve_view_name(version.view_id,presentation_cutoff=(
            min(version.created_at,version.recorded_as_of) if version.recorded_as_of else version.created_at)
            if args.snapshot is not None else None)
        title=naming['display_title'] or engine._spec(version.view_id).query
        document = render_artifact(title, version)
        if args.snapshot is None:
            diagnostics = _query_diagnostics(store, engine, version, receipts[version.view_id])
            check = diagnostics["freshness_check"]
            notice = (f"Проверено по срезу {diagnostics['query_snapshot']['captured_at']} "
                      f"(версия источников {check['source_version']}); актуальность проверена {check['checked_at']}.")
            if diagnostics["refresh_required"]:
                notice += " Новые сведения ещё не проверены; требуется обновление."
            if diagnostics["completeness"]=="incomplete":
                notice += " Полнота ответа ограничена необработанными источниками или нерешёнными вопросами."
            heading, body = document.split("\n\n", 1)
            document = f"{heading}\n\n{notice}\n\n{body}"
        return document
    if args.command == "review-branch":
        return asdict(engine.review_branch(CaptureQueue(store), args.view_id, args.assertion_id,
                                           decision=args.decision, reviewer=args.reviewer, reason=args.reason))
    if args.command == "branch-reviews":
        return [asdict(item) for item in engine.list_branch_reviews(args.view)]
    if args.command == "status":
        return engine.inspect_view(args.id)
    if args.command == "history":
        return [asdict(item) for item in engine.view_history(args.id)]
    if args.command == "impact":
        return engine.impact(args.id)
    if args.command == "suggest-links":
        return engine.suggest_links(args.id)
    if args.command == "refresh":
        return [asdict(item) for item in engine.refresh_from(args.id)]
    return asdict(engine.get_version(args.id))


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            snapshot_database(args.db, args.destination)
            result = {"backup": str(args.destination)}
        elif args.command == "restore":
            snapshot_database(args.source, args.db)
            result = {"restored": str(args.db)}
        else:
            model, candidates, model_key = _configuration(args)
            args.db.parent.mkdir(parents=True, exist_ok=True)
            with MemoryStore(args.db) as store:
                if not args.embedding_model:
                    candidates = build_candidate_search(store)
                engine = MemoryEngine(store, model, candidates, naming_model=model, model_key=model_key,
                                      verify_overviews=model is not None or (
                                          getattr(args, "prompt", False) and args.generator != "extractive"))
                result = _dispatch(args, store, engine, candidates)
    except (ValueError, RuntimeError, OSError, sqlite3.Error, TypeError, AttributeError, KeyError) as error:
        parser.exit(1, f"error: {error}\n")
    print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
