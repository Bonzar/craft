"""Revalidate accepted semantic inputs after complete-corpus invalidation."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict

from .build_proof import accepted_review, remember
from .evidence import _expand_evidence


def _identity(engine, spec, evidence, edges, upstream, missing, valid_as_of, recorded_as_of):
    body = [engine.pipeline_key, asdict(spec), valid_as_of, recorded_as_of,
        sorted(evidence, key=lambda row: row['id']), sorted(edges, key=lambda row: row['id']), upstream, sorted(missing)]
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _prior(engine, spec, valid_as_of, recorded_as_of):
    from .engine import _load_version, _load_check
    from .background_overviews import thematic_navigation_context
    if thematic_navigation_context(engine, spec) is not None:
        return None
    with engine.store.transaction() as connection:
        row = connection.execute('SELECT v.id,v.snapshot,c.* FROM memory_view_checks c '
            "JOIN memory_view_versions v ON v.id=c.version_id WHERE c.view_id=? AND coalesce(json_extract(c.overview_review,'$.certificate.kind'),'') != 'answer_applicability' AND c.valid_cutoff=? "
            "AND c.recorded_cutoff=? AND c.pipeline_key=? AND json_extract(c.overview_review,'$.status')='accepted' "
            'ORDER BY c.id DESC LIMIT 1', (spec.id, valid_as_of or '', recorded_as_of or '', engine.pipeline_key)).fetchone()
    from .question_routes import routing_review
    if (row and not routing_review(json.loads(row['overview_review']))
            and not routing_review(json.loads(row['snapshot']).get('overview_review')) and accepted_review(engine, json.loads(row['overview_review']))):
        # c.id shares the SQL name id with v.id; extract the check separately.
        with engine.store.transaction() as connection:
            check = connection.execute("SELECT * FROM memory_view_checks WHERE view_id=? AND coalesce(json_extract(overview_review,'$.certificate.kind'),'') != 'answer_applicability' AND version_id=? "
                'AND valid_cutoff=? AND recorded_cutoff=? AND pipeline_key=? ORDER BY id DESC LIMIT 1',
                (spec.id, row[0], valid_as_of or '', recorded_as_of or '', engine.pipeline_key)).fetchone()
        return _load_version(row), _load_check(check)
    return None


def _recipes(audit, obligations):
    recipes, queries, reads = [], set(), set()
    for receipt in audit.get('retrieval', ()):
        if receipt['status'] == 'deferred':
            continue
        gap = receipt['gap_id']
        if gap not in obligations:
            return None
        recipes.append({**receipt, 'limit': obligations[gap]['limit'], 'exact': True})
        queries.add((gap, receipt['query']))
        reads.update((gap, key) for key in receipt['requested_ids'])
    for gap, obligation in obligations.items():
        if obligation['operation'] == 'read':
            ids = [key for key in obligation['source_ids'] if (gap, key) not in reads]
            extra = [{'query': '', 'requested_ids': ids}] if ids else []
        else:
            extra = [{'query': query, 'requested_ids': []} for query in
                dict.fromkeys([obligation['query'], *obligation.get('aliases', ())]) if (gap, query) not in queries]
        for item in extra:
            recipes.append({'gap_id': gap, 'operation': obligation['operation'], **item,
                'limit': obligation['limit'], 'selected_ids': obligation['served_ids'], 'exact': False})
    return recipes


def _perform(engine, recipe, visible, seen):
    if recipe['operation'] == 'read':
        return [key for key in recipe['requested_ids'] if key in visible]
    excluded = seen if recipe['exact'] else set()
    candidates = {key: row['text'] for key, row in visible.items() if key not in excluded}
    return [match.id for match in engine.candidates.rank(recipe['query'], candidates,
        limit=recipe['limit']) if match.id in candidates]


def _replay(engine, spec, snapshot, evidence, edges, audit, state):
    from .overview_review import _authorized
    visible = _authorized(spec, snapshot.assertions, snapshot.edges, evidence)
    obligations = {row['gap_id']: row for row in audit.get('obligations', ())}
    unchanged = all(row.get('status') not in ('pending', 'deferred') for row in obligations.values())
    recipes = _recipes(audit, obligations)
    if recipes is None:
        return evidence, edges, False, []
    selected, seen, receipts = {row['id'] for row in evidence}, {}, []
    marks = {row['id']: row['branch_review'] for row in evidence if 'branch_review' in row}
    for recipe in recipes:
        gap = recipe['gap_id']
        served = seen.setdefault(gap, set())
        ids = _perform(engine, recipe, visible, served)
        served.update(ids)
        expected = set(recipe['selected_ids'])
        unchanged = unchanged and (set(ids) == expected if recipe['exact'] else set(ids) <= expected)
        receipts.append({key: recipe[key] for key in ('gap_id', 'operation', 'query', 'requested_ids')} | {
            'selected_ids': sorted(ids), 'status': 'performed' if ids else 'unavailable',
            'coverage': 'bounded_not_exhaustive'})
        selected.update(ids)
        evidence, required = _expand_evidence(selected, snapshot.assertions, snapshot.edges)
        for row in evidence:
            if row['id'] in marks:
                row['branch_review'] = marks[row['id']]
        edges = sorted({row['id']: row for row in [*edges, *required]}.values(), key=lambda row: row['id'])
        remember(state, evidence, edges, replayed_acquisition=copy.deepcopy(receipts))
    return evidence, edges, unchanged, receipts


def revalidate(engine, spec, snapshot, evidence, edges, upstream, missing, state):
    """Refresh selection and known material recipes; never replay an old no-hit as fact."""
    if not engine.verify_overviews or engine.model is None:
        return None, None, evidence, edges
    prior = _prior(engine, spec, snapshot.requested_valid_as_of, snapshot.requested_recorded_as_of)
    if prior is None:
        return None, None, evidence, edges
    version, check = prior
    audit = check['overview_review']
    state['phase'] = 'material_acquisition_revalidation'
    evidence, edges, same_outcomes, replayed = _replay(engine, spec, snapshot, evidence, edges, audit, state)
    current = _identity(engine, spec, evidence, edges, upstream, missing,
                        snapshot.requested_valid_as_of, snapshot.requested_recorded_as_of)
    approved = _identity(engine, spec, version.evidence, version.edges, version.upstream, version.missing_roots,
                         version.valid_as_of, version.recorded_as_of)
    trace = {'prior_validation_check_id': check['id'], 'semantic_input_sha256': current,
        'query_snapshot': snapshot.descriptor(), 'material_acquisition_outcomes_unchanged': same_outcomes,
        'replayed_acquisition': replayed, 'summary_reused': same_outcomes and current == approved}
    state['selection_revalidation'] = trace
    if not trace['summary_reused']:
        return None, {'selection_revalidation': trace}, evidence, edges
    review = copy.deepcopy(audit)
    review['selection_revalidation'] = {**trace, 'reused_validation_check_id': check['id']}
    return version, review, evidence, edges
