"""Shared directional prerequisite closure and evidence dispositions.

These helpers are also re-exported by engine for existing projection consumers.
"""
from __future__ import annotations

from collections.abc import Collection

from .capture import declared_kind
from .link_proof import reported_decision_proof

_EVIDENCE_RELATIONS = frozenset({
    "exception_to", "supersedes", "contradicts", "supports", "realizes", "depends_on",
})
_MANDATORY_RELATIONS = frozenset({"exception_to", "supersedes", "contradicts", "depends_on"})


def _expand_evidence(seeds: set[str], assertions: dict[str, dict], edges: list[dict]):
    included, selected = _reachable(seeds, _mandatory_adjacency(edges))
    links = sorted(selected.values(), key=lambda item: item["id"])
    conflicts, exceptions, observed_exceptions, observed_conflicts = set(), set(), set(), set()
    replacements: dict[str, set[str]] = {}
    for link in links:
        source = assertions[link["from_id"]]
        target = assertions[link["to_id"]]
        kind = link["kind"]
        source_kind = declared_kind(source["text"], source["metadata"])
        target_kind = declared_kind(target["text"], target["metadata"])
        if source_kind == "proposal" or target_kind == "proposal":
            continue
        if kind in {"exception_to", "supersedes", "contradicts"} and "inference" in (source_kind, target_kind):
            continue
        if kind == "contradicts":
            conflicts.update((source["id"], target["id"]))
            if link["metadata"].get("basis") == "observation":
                observed_conflicts.add(source["id"])
        if kind == "exception_to":
            exceptions.add(source["id"])
            if source_kind == "observation" or link["metadata"].get("basis") == "observation":
                observed_exceptions.add(source["id"])
        if kind == "supersedes" and not link["metadata"].get("condition") and source["scope"] == target["scope"]:
            if target_kind is None or source_kind == target_kind or _verified_policy(source, target, link['metadata']):
                replacements.setdefault(source["id"], set()).add(target["id"])
            else:
                conflicts.update((source["id"], target["id"]))

    superseded = set()
    for source, targets in replacements.items():
        for target in targets:
            if _can_reach(target, source, replacements):
                conflicts.update((source, target))
            else:
                superseded.add(target)

    evidence = []
    for key in sorted(included, key=lambda item: (assertions[item]["recorded_at"], item)):
        record = assertions[key]
        if key in superseded:
            disposition = "superseded"
        elif key in conflicts:
            disposition = "conflict"
        elif key in exceptions:
            disposition = "exception"
        else:
            disposition = declared_kind(record["text"], record["metadata"]) or "assertion"
        evidence.append({**record, "disposition": disposition})
        if key in observed_exceptions:
            evidence[-1]["exception_basis"] = "observation"
        if key in observed_conflicts:
            evidence[-1]["conflict_basis"] = "reported_observation"
    return evidence, links


def _verified_policy(source, target, metadata):
    proof = metadata.get('verification')
    return (metadata.get('basis') == 'reported_decision' and isinstance(proof, dict)
            and reported_decision_proof(source, target, proof, resolved=False) is not None)


def _can_reach(start: str, target: str, graph: dict[str, set[str]]) -> bool:
    pending = [start]
    seen = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            pending.extend(graph.get(node, ()))
    return False


def _mandatory_adjacency(edges: list[dict], *, reverse: bool = False) -> dict[str, list[dict]]:
    adjacency = _adjacency(edges, kinds=_MANDATORY_RELATIONS - {"depends_on"})
    # Reading a prerequisite does not require all of its dependents. Impact
    # propagation follows the opposite direction so every affected view is found.
    endpoint = "to_id" if reverse else "from_id"
    for edge in edges:
        if edge["kind"] == "depends_on":
            adjacency.setdefault(edge[endpoint], []).append(edge)
    return adjacency


def _adjacency(edges: list[dict], *, kinds: Collection[str] = _EVIDENCE_RELATIONS) -> dict[str, list[dict]]:
    adjacency: dict[str, list[dict]] = {}
    for edge in edges:
        if edge["kind"] in kinds:
            adjacency.setdefault(edge["from_id"], []).append(edge)
            adjacency.setdefault(edge["to_id"], []).append(edge)
    return adjacency


def _reachable(seeds: set[str], adjacency: dict[str, list[dict]]) -> tuple[set[str], dict[str, dict]]:
    included = set(seeds)
    pending = list(seeds)
    selected = {}
    while pending:
        for edge in adjacency.get(pending.pop(), []):
            selected[edge["id"]] = edge
            for endpoint in (edge["from_id"], edge["to_id"]):
                if endpoint not in included:
                    included.add(endpoint)
                    pending.append(endpoint)

    return included, selected
