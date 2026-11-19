"""Record relationships before scheduling refresh or assembling a new overview."""
from .model import ModelError
from .thought_answering import can_refresh, refresh_thought
from .thought_engine_contract import (
    GENERALIZE, GENERALIZE_REFRESH, GENERALIZE_RELATIONS, GENERALIZE_REUSE,
    MAX_OVERVIEW_TITLE_CHARACTERS, semantic_text,
)
from .thought_store import ThoughtConflict


def _ancestors(engine, changed):
    """A conclusion is not independent evidence for a thought it already uses."""
    pending = [basis for thought in changed for basis in thought.bases]
    visited = set()
    while pending:
        basis = pending.pop()
        if 'thought_id' not in basis:
            continue
        key = (basis['thought_id'], basis['version'])
        if key in visited:
            continue
        visited.add(key)
        thought = engine.thoughts.get(*key)
        if thought is not None:
            pending.extend(thought.bases)
    return {thought_id for thought_id, _ in visited}


def _classify(engine, changed):
    changed_ids = {row.id for row in changed}
    related = engine.retrieve('\n'.join(row.text for row in changed), [row.id for row in changed])
    existing = [row for row in related if row.id not in changed_ids]
    candidates = [*changed, *existing]
    ancestors = _ancestors(engine, changed)
    refresh = [row for row in existing if row.id not in ancestors and can_refresh(engine, row)] if any(
        row.origin.get('event_id') or row.origin.get('kind') == 'inference' for row in changed) else []
    reusable = [row for row in candidates if row.metadata.get('verification') != 'legacy_unverified'
                and not engine._is_parallel_alternative(row)]
    refresh_numbers = {row.id: index for index, row in enumerate(refresh)}
    reuse_numbers = {row.id: index for index, row in enumerate(reusable)}
    records = []
    for index, row in enumerate(candidates):
        record = {'role': 'changed' if row.id in changed_ids else 'existing', **engine._content(row)}
        if existing:
            record['link_option'] = index
        if row.id in reuse_numbers:
            record['reuse_option'] = reuse_numbers[row.id]
        if row.id in refresh_numbers:
            record['refresh_option'] = refresh_numbers[row.id]
        records.append(record)
    instructions = GENERALIZE
    if existing:
        instructions += GENERALIZE_RELATIONS
    if reusable:
        instructions += GENERALIZE_REUSE
    if refresh:
        instructions += GENERALIZE_REFRESH
    result = engine._call('Find useful shared thought question', records,
        [engine._basis(row) for row in candidates], instructions, refresh_option_count=len(refresh),
        reuse_option_count=len(reusable), link_option_count=len(candidates) if existing else 0)
    relations = [(candidates[row['source']], candidates[row['target']], row['text'])
                 for row in result.get('relations', [])]
    if any(source.id not in changed_ids or target.id in changed_ids for source, target, _ in relations):
        raise ModelError('semantic links must go from changed to existing offered thoughts')
    linked = {target.id for _, target, _ in relations}
    affected = [refresh[index] for index in dict.fromkeys(result.get('affected_candidates', []))]
    if any(row.id not in linked for row in affected):
        raise ModelError('refresh requires a confirmed semantic link')
    reuse = result.get('reuse_candidate')
    return result, candidates, relations, affected, reusable[reuse] if reuse is not None else None


def _save_relations(engine, changed, relations, affected, on_affected):
    """No model runs while the graph and its change jobs are committed."""
    with engine.store.transaction(write=True):
        for row in [*changed, *(target for _, target, _ in relations), *affected]:
            if engine.thoughts.get(row.id) != row:
                raise ThoughtConflict('A thought changed during maintenance classification')
        written = []
        for source, target, meaning in relations:
            written.append(engine.thoughts.connect(source.id, target.id, meaning,
                source_version=source.version, target_version=target.version))
        if on_affected is not None:
            on_affected(affected)
    if written:
        engine._emit('semantic_relations', outputs=written)


def _overview_material(engine, question, changed, candidates, relations):
    sources = {row.id: row for row in changed}
    for _, target, _ in relations:
        sources[target.id] = target
    exact_bases = {(basis['thought_id'], basis['version']) for thought in changed
                   for basis in thought.bases if 'thought_id' in basis}
    for candidate in candidates:
        if (candidate.id, candidate.version) in exact_bases:
            sources[candidate.id] = candidate
    if len(sources) < 2:
        return [], []
    records, bindings = engine._current_material(question, list(sources.values()), known_ids=list(sources))
    if len({binding.get('thought_id') or binding.get('event_id') for binding in bindings}) < 2:
        return [], []
    comparison = [{'text': row.text, 'questions': engine.question_texts(row)}
                  for row in candidates if row.id not in sources]
    return [*records, {'role': 'overview_comparison', 'existing': comparison}], [*bindings, {}]


def _build_overview(engine, response, changed, candidates, relations):
    question = semantic_text(response['question'], 'question')
    title = semantic_text(response['text'])
    if len(title) > MAX_OVERVIEW_TITLE_CHARACTERS or len(title.splitlines()) != 1:
        engine._emit('automatic_overview_skipped', reason='Missing concise title for proposed scope', question=question)
        return []
    nearby = engine.retrieve(question, [row.id for row in changed])
    for row in nearby:
        if (engine._usable_content(row) and not engine._is_parallel_alternative(row)
                and question in engine.question_texts(row)):
            return [row]
    comparison = list({row.id: row for row in [*candidates, *nearby]}.values())
    records, bindings = _overview_material(engine, question, changed, comparison, relations)
    if not records:
        engine._emit('automatic_overview_skipped', reason='No connected supporting thoughts', question=question)
        return []
    result = engine._construct(records, bindings, question=question, origin={'kind': 'inference'}, title=title)
    for thought in result:
        frontier = engine._frontier(comparison, thought.id)
        engine._save_check(thought, question, frontier)
    return result


def consolidate(engine, changed, *, on_affected=None):
    changed = list(changed)
    if not changed:
        return []
    response, candidates, relations, affected, reusable = _classify(engine, changed)
    _save_relations(engine, changed, relations, affected, on_affected)
    refreshed = []
    if on_affected is None:
        for parent in affected:
            sources = [source for source, target, _ in relations if target.id == parent.id]
            refreshed.append(refresh_thought(engine, parent, additional=sources))
    if response.get('question') is not None:
        return _build_overview(engine, response, changed, candidates, relations)
    if reusable is not None and reusable.id not in {row.id for row in affected}:
        if not engine._fresh(reusable):
            raise ThoughtConflict('A reusable thought changed during maintenance classification')
        engine._emit('automatic_overview_reused', thought=engine._basis(reusable))
        return [reusable]
    return refreshed
