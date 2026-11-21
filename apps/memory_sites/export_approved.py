"""Export a narrow, explicitly approved canonical memory snapshot, without uploading it.

Run from the repository root; inspect the resulting JSON before importing it.
Approved thought bodies and overview summaries are copied verbatim and may still
contain personal or sensitive information. An allowlist is not redaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path

from apps.graph_viewer.server.providers.canonical_thoughts import (
    SCHEMA as CANONICAL_SCHEMA,
    capture,
    compatibility_document,
    readonly,
)
from apps.graph_viewer.server.providers.json_snapshot import validate_document
from prototypes.assertion_memory.backup import snapshot_database
from prototypes.assertion_memory.thought_graph import version_node_id

SCHEMA = "graph-viewer-source/v1"
# Match the bounded Sites import contract in store.mjs / worker.mjs.
MAX_BYTES = 768 * 1024
COUNT_LIMITS = {"nodes": 3000, "edges": 6000, "overviews": 1200, "topic_links": 2400}
NODE_FIELDS = (
    "id", "text", "kind", "scope", "recorded_at", "valid_at", "valid_precision",
    "source_recorded_at", "source_available_at", "interpreted_at", "published_status",
)
EDGE_FIELDS = ("id", "from_id", "to_id", "kind", "recorded_at", "effective_at")
VERSION_FIELDS = (
    "version_id", "published_at", "member_ids", "summary", "valid_cutoff", "recorded_cutoff",
)


class ExportError(ValueError):
    """Safe, non-data-bearing messages from the export boundary."""


def _select(record, fields):
    return {name: record[name] for name in fields if name in record}


def _encode(document):
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _check_scopes(scopes):
    if isinstance(scopes, str):
        raise ExportError("Supply scopes as an explicit list of exact values.")
    items = tuple(scopes)
    if not items or any(not isinstance(item, str) or not item.strip() or item != item.strip()
                        or item in {"*", "all", "ALL"} for item in items):
        raise ExportError("At least one exact, nonempty scope is required; wildcards are unsupported.")
    return frozenset(items)


def _dependency_safe_ids(nodes, scopes, event_scopes):
    """Exclude unavailable/unapproved bases and their dependent thoughts."""
    ids = {node["id"] for node in nodes if node.get("scope") in scopes}
    failing = set()
    dependents = defaultdict(set)
    for node in nodes:
        node_id = node["id"]
        if node_id not in ids:
            continue
        origin_event = node.get("origin", {}).get("event_id")
        if origin_event and event_scopes.get(origin_event) not in scopes:
            failing.add(node_id)
        for basis in node["bases"]:
            if "event_id" in basis:
                if event_scopes.get(basis["event_id"]) not in scopes:
                    failing.add(node_id)
            elif "thought_id" in basis:
                dependency = version_node_id(basis["thought_id"], basis["version"])
                if dependency not in ids:
                    failing.add(node_id)
                else:
                    dependents[dependency].add(node_id)
            else:
                failing.add(node_id)
    pending = deque(failing)
    while pending:
        for dependent in dependents[pending.popleft()]:
            if dependent not in failing:
                failing.add(dependent)
                pending.append(dependent)
    return ids - failing


def approved_snapshot(canonical: dict, scopes, event_scopes: dict) -> dict:
    """Preserve compatible graph identities and dates; strip raw capture data."""
    selected_scopes = _check_scopes(scopes)
    if canonical.get("schema") != CANONICAL_SCHEMA or canonical.get("semantic_model") != "thought":
        raise ExportError("Expected a canonical thought graph.")
    available = {node.get("scope") for node in canonical["nodes"]}
    if selected_scopes - available:
        raise ExportError("An approved scope is absent from the canonical read-only snapshot.")
    safe = _dependency_safe_ids(canonical["nodes"], selected_scopes, event_scopes)
    if not safe:
        raise ExportError("No thoughts have an approved scope with exportable documentary bases.")
    safe_scopes = {n["scope"] for n in canonical["nodes"] if n["id"] in safe}
    if selected_scopes - safe_scopes:
        raise ExportError("At least one approved scope contains no safely exportable thoughts.")

    # Generate groups from the filtered canonical graph; incomplete groups cannot
    # silently acquire a new membership. Metadata is used only internally for
    # canonical classification; titles are derived from the approved text.
    local_nodes = [n for n in canonical["nodes"] if n["id"] in safe]
    local_edges = [
        e for e in canonical["edges"]
        if e["from_id"] in safe and e["to_id"] in safe and e.get("recorded_at") is not None
    ]
    projected = compatibility_document(dict(canonical, nodes=local_nodes, edges=local_edges))
    by_id = {node["id"]: node for node in projected["nodes"]}
    overviews = [
        {
            "id": group["id"],
            "title": by_id[group["id"]]["text"].splitlines()[0][:80].strip() or "Обзор",
            "scopes": sorted({by_id[member]["scope"] for v in group["versions"] for member in v["member_ids"]}),
            "current_version_id": group["current_version_id"],
            "versions": [_select(v, VERSION_FIELDS) for v in group["versions"]],
        }
        for group in projected["overviews"]
    ]
    document = {
        "schema": SCHEMA,
        "provider_id": canonical["provider_id"],
        "nodes": [_select(n, NODE_FIELDS) for n in projected["nodes"]],
        "edges": [_select(e, EDGE_FIELDS) for e in local_edges],
        "episodes": [],
        "overviews": overviews,
        "topic_links": [
            dict(_select(link, ("id", "parent_id", "child_id", "relation", "recorded_at")),
                 provenance={"kind": "approved_snapshot"})
            for link in projected["topic_links"]
        ],
    }
    document["source_revision"] = "sha256:" + hashlib.sha256(_encode(document)).hexdigest()
    return validate_document(document)


def _event_scopes(snapshot_path, canonical):
    ids = {basis["event_id"] for node in canonical["nodes"]
           for basis in node["bases"] if "event_id" in basis}
    ids.update(node["origin"]["event_id"] for node in canonical["nodes"]
               if node.get("origin", {}).get("event_id"))
    if not ids:
        return {}
    with closing(readonly(snapshot_path)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_raw_events'").fetchone():
            return {}
        result = {}
        ordered = sorted(ids)
        for first in range(0, len(ordered), 500):
            batch = ordered[first:first + 500]
            placeholders = ",".join("?" for _ in batch)
            result.update(connection.execute(
                f"SELECT id, scope FROM memory_raw_events WHERE id IN ({placeholders})", batch,
            ).fetchall())
        return result


def export_approved(source: Path, output: Path, scopes, *, provider_id="assertion-memory"):
    # Refuse absent/ambiguous permission or an existing path before reading the source.
    allowlist = _check_scopes(scopes)
    output = Path(output).absolute()
    if os.path.lexists(output):
        raise FileExistsError("The export destination already exists.")
    with tempfile.TemporaryDirectory(prefix="craft-memory-export-") as temporary:
        snapshot = Path(temporary) / "snapshot.sqlite3"
        snapshot_database(Path(source), snapshot)
        canonical = capture(snapshot, provider_id)
        scoped = approved_snapshot(canonical, allowlist, _event_scopes(snapshot, canonical))
    if any(len(scoped[name]) > limit for name, limit in COUNT_LIMITS.items()):
        raise ExportError("The approved snapshot exceeds a Sites record limit; approve fewer scopes.")
    encoded = _encode(scoped) + b"\n"
    if len(encoded) > MAX_BYTES:
        raise ExportError("The approved snapshot exceeds the Sites import size limit; approve fewer scopes.")
    # Owner-only, no clobber even for concurrent exporters, no half-written output.
    descriptor, name = tempfile.mkstemp(prefix=".memory-export-", dir=output.parent)
    temporary_export = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_export, output)
    finally:
        temporary_export.unlink(missing_ok=True)
    return {key: len(scoped[key]) for key in ("nodes", "edges", "overviews", "topic_links")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Existing canonical memory SQLite database.")
    parser.add_argument("--scope", action="append", required=True, help="Exact approved scope; repeat for each scope.")
    parser.add_argument("--output", required=True, type=Path, help="New file in a private directory; never commit it.")
    args = parser.parse_args(argv)
    try:
        result = export_approved(args.source, args.output, args.scope)
    except ExportError as exc:
        print("Export failed: " + str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("Export failed ({}). No existing export was changed.".format(type(exc).__name__), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
