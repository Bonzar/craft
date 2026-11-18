"""Iterative derivation order; semantic assertion relations may remain cyclic."""
from __future__ import annotations


def dependency_order(engine, spec, completed=()):
    """Yield missing descendants once, after their own direct dependencies.

    The explicit iterator stack grows with stored depth rather than Python call
    depth. A derivation back edge cannot produce a version, unlike an ordinary
    semantic relation cycle, so reject it before attempting that assembly.
    """
    finished = set(completed)
    active = {spec.id}
    path = [spec.id]
    frames = [(spec, iter(spec.depends_on))]
    while frames:
        node, dependencies = frames[-1]
        try:
            key = next(dependencies)
        except StopIteration:
            finished.add(node.id)
            active.remove(node.id)
            path.pop()
            frames.pop()
            if node.id != spec.id:
                yield node
            continue
        if key in finished:
            continue
        if key in active:
            cycle = path[path.index(key):] + [key]
            raise ValueError('cyclic assembly dependency: ' + ' -> '.join(cycle))
        child = engine._spec(key)
        active.add(key)
        path.append(key)
        frames.append((child, iter(child.depends_on)))


def related_sources(engine, spec, snapshot, *, acquisition=None):
    """Collect source indexes with the same deterministic cycle boundary as before.

    An in-progress index contributes no sources on its back edge. Unlike hard
    derivation, these source-selection links do not require a child publication.
    """
    from .question_routes import related_ids
    records, lineage, selected = {}, [], {spec.id: []}
    for key in related_ids(engine, spec):
        if key in selected:
            continue
        selected[key] = []
        frames = [_index_frame(engine, key)]
        while frames:
            child, dependencies, inherited = frames[-1]
            dependency = next(dependencies, None)
            if dependency is not None:
                if dependency in selected:
                    inherited.extend(selected[dependency])
                else:
                    selected[dependency] = []
                    frames.append(_index_frame(engine, dependency))
                continue
            evidence = _select_index(engine, spec, child, snapshot, inherited,
                                     records, lineage, acquisition)
            selected[child.id] = evidence
            frames.pop()
            if frames:
                frames[-1][2].extend(evidence)
    # Optional index gaps remain audit information, never hard answer roots.
    return list(records.values()), [], lineage


def _index_frame(engine, key):
    from .question_routes import related_ids
    child = engine._spec(key)
    return child, iter((*child.depends_on, *related_ids(engine, child))), []


def _select_index(engine, spec, child, snapshot, inherited, records, lineage, acquisition):
    import hashlib
    import json
    from .question_routes import select_sources, index_plan, dependency_definitions
    with engine.store.transaction() as connection:
        row = connection.execute("SELECT snapshot FROM memory_view_versions WHERE view_id=? "
            "AND json_extract(snapshot,'$.valid_as_of') IS ? AND json_extract(snapshot,'$.recorded_as_of') IS ? "
            "ORDER BY id DESC LIMIT 1",
            (child.id, snapshot.requested_valid_as_of, snapshot.requested_recorded_as_of)).fetchone()
    cached = json.loads(row[0])['evidence'] if row else []
    eligible = [snapshot.assertions[item['id']] for item in cached
                if item['id'] in snapshot.assertions and 'branch_review' not in item]
    evidence, _ = select_sources(engine, child, snapshot, [*inherited, *eligible], acquisition=acquisition)
    candidates = {item['id']: item for item in evidence}
    queries = [spec.query, *index_plan(engine, spec.id).get('subquestions', ())]
    relevant = {match.id for query in queries for match in engine.candidates.rank(
        query, {key: value['text'] for key, value in candidates.items()}, limit=spec.max_seeds)}
    records.update((key, candidates[key]) for key in relevant)
    signature = hashlib.sha256(json.dumps(dependency_definitions(engine, child), sort_keys=True).encode()).hexdigest()
    lineage.append({'view_id': child.id, 'selection_signature': signature,
                    'source_ids': sorted(item['id'] for item in evidence),
                    'missing_roots': sorted(set(child.root_ids) - snapshot.assertions.keys())})
    return evidence
