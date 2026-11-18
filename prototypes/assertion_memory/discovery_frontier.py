"""Complete canonical membership with incremental thematic discovery projections."""
from __future__ import annotations

import hashlib
import json

from .evidence import _expand_evidence
from .model import ModelError


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def corpus_state(spec, snapshot):
    ids = {key for key, row in snapshot.assertions.items() if not spec.scopes or row['scope'] in spec.scopes}
    ids.update(set(spec.root_ids) & snapshot.assertions.keys())
    records, _ = _expand_evidence(ids, snapshot.assertions, snapshot.edges)
    included = {row['id'] for row in records}
    edges = [edge for edge in snapshot.edges if {edge['from_id'], edge['to_id']} <= included]
    incident = {key: [] for key in included}
    for edge in edges:
        for endpoint in {edge['from_id'], edge['to_id']}:
            incident[endpoint].append(edge)
    fingerprints = {row['id']: _digest([row, sorted(incident[row['id']], key=lambda edge: edge['id'])])
                    for row in records}
    return records, edges, fingerprints, _digest(fingerprints)


def retained_membership(previous, request, pipeline_key, fingerprints):
    """Reuse classifications only for identical source meaning at matching intent."""
    compatible = (previous and previous.get('request') == request
                  and previous.get('pipeline_key') == pipeline_key
                  and isinstance(previous.get('source_fingerprints'), dict))
    prior = previous['source_fingerprints'] if compatible else {}
    if compatible:
        assigned = {key for theme in previous.get('themes', ()) for key in theme['source_ids']}
        assigned.update(previous.get('unassigned_source_ids', ()))
        if assigned != set(prior) or len(prior) != previous.get('source_count'):
            raise ModelError('previous thematic coverage manifest is incomplete')
    unchanged = {key for key, value in fingerprints.items() if prior.get(key) == value}
    changed = set(fingerprints) - unchanged
    themes = [{key: theme[key] for key in ('question',)} | {
        'source_ids': sorted(set(theme['source_ids']) & unchanged)}
        for theme in previous.get('themes', ())] if compatible else []
    unassigned = set(previous.get('unassigned_source_ids', ())) & unchanged if compatible else set()
    return themes, unassigned, changed, sorted(set(prior) - fingerprints.keys()), len(unchanged)


def current_membership(engine, spec, snapshot):
    """A complete matching manifest is an operational thematic source index."""
    from .storage import _timestamp
    with engine.store.transaction() as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone()
        if not exists:
            return False
        rows = connection.execute("SELECT DISTINCT r.rowid,r.plan,r.view_id,r.request_id,r.revision FROM memory_question_route_versions r, "
            "json_tree(r.plan,'$.discovery') t WHERE t.key='view_id' AND t.value=? ORDER BY r.rowid DESC",
            (spec.id,)).fetchall()
    revision = None
    for row in rows:
        plan = json.loads(row['plan'])
        discovery = plan.get('discovery', {})
        request = discovery.get('request', {})
        if (discovery.get('pipeline_key') != engine.pipeline_key or request.get('scopes') != list(spec.scopes)
                or request.get('valid_as_of') != (_timestamp(snapshot.requested_valid_as_of) if snapshot.requested_valid_as_of else None)
                or request.get('recorded_as_of') != (_timestamp(snapshot.requested_recorded_as_of) if snapshot.requested_recorded_as_of else None)):
            continue
        themes = discovery.get('themes', ())
        assigned = {key for theme in themes for key in theme['source_ids']} | set(discovery.get('unassigned_source_ids', ()))
        hashes = discovery.get('source_fingerprints', {})
        if (assigned != set(hashes) or len(hashes) != discovery.get('source_count')
                or discovery.get('unassigned_source_ids')
                or any(item.get('unresolved_source_ids') for item in discovery.get('membership_acquisitions', ()))):
            continue
        exact = any(theme.get('view_id') == spec.id and set(theme['source_ids']) == set(spec.root_ids) for theme in themes)
        groups = [group for level in discovery.get('hierarchy', ()) for group in level.get('groups', ())]
        exact = exact or any(group.get('view_id') == spec.id and set(group['depends_on']) == set(spec.depends_on) for group in groups)
        if not exact:
            continue
        revision = revision or corpus_state(spec, snapshot)[3]
        if discovery.get('corpus_revision') == revision:
            return {'method': 'complete_thematic_membership', 'route_view_id': row['view_id'],
                'request_id': row['request_id'], 'route_revision': row['revision'],
                'covered_corpus_revision': revision, 'eligible_source_count': discovery['source_count'],
                'pipeline_key': engine.pipeline_key, 'definition_root_ids_sha256': _digest(list(spec.root_ids)),
                'definition_dependency_ids': list(spec.depends_on), 'member_source_count': len(spec.root_ids),
                'mandatory_closure': 'complete_directional', 'optional_navigation': 'within_selected_membership_only'}
    return False
