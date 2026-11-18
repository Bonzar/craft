"""Mechanical navigation publication, never a domain LLM summary."""
from __future__ import annotations

import hashlib
import json

from .model import ModelError

PROTOCOL = 'automatic-navigation-index-v1'


def render(context, children):
    required = context['required_view_ids']
    if ([row['view_id'] for row in children] != required or len(set(required)) != len(required)):
        raise ModelError('navigation rendered membership differs from the complete required set')
    lines = ['Navigation index of attributed thematic questions at this snapshot.',
             'Labels are quoted questions; this directory makes no domain claims.']
    for child in children:
        # JSON quoting escapes newlines/control characters, Markdown delimiters
        # are plain text inside a code fence chosen longer than any input run.
        label = json.dumps(child['question'], ensure_ascii=False)
        lines.append(label)
        lines.append('view_id=' + json.dumps(child['view_id']) + ', version_id=' + str(child['version_id']) + ', check_id=' + str(child['check_id']))
        for section in ('summary', 'evidence', 'edges', 'metadata', 'checks'):
            lines.append('memory_history(view_id=' + json.dumps(child['view_id']) +
                ', version_id=' + str(child['version_id']) +
                ', section=' + json.dumps(section) + (', snapshot_id=' + str(child['check_id']) if section == 'checks' else '') + ')')
        lines.append('cutoffs=' + json.dumps(child['cutoffs'], sort_keys=True))
    text = '\n'.join(lines)
    # Render all model-authored labels as literal text, including malicious
    # Markdown/HTML/links. Fence length is derived, so labels cannot close it.
    import re
    fence = '`' * max(3, 1 + max((len(m.group()) for m in re.finditer(r'`+', text)), default=0))
    return fence + '\n' + text + '\n' + fence



def _require_source_frontier(snapshot, scopes):
    pending = any(row.get('status') in ('pending', 'failed', 'unresolved') and not row.get('context_only')
        for row in snapshot.source_processing_evidence if not scopes or row['scope'] in scopes)
    if pending or snapshot.source_gaps(scopes)['total']:
        raise ModelError('navigation source processing frontier has unresolved gaps')
    return _relation_boundary(snapshot, scopes)


def _relation_boundary(snapshot, scopes):
    """Allow terminal uncertainty only as pinned, explicitly unassessed metadata."""
    if not hasattr(snapshot, 'relation_gaps'):
        return None  # Explicit canonical-only legacy capability, no relation API.
    from .answer_relation_gaps import inventory, digest
    import re
    error = 'navigation relation processing frontier lacks complete operational proof'
    value = snapshot.relation_gaps(scopes)
    if not isinstance(value, dict):
        raise ModelError(error)
    counters = ('total', 'operational_count', 'unassessed_count')
    hashes = ('frontier_sha256', 'inventory_sha256', 'source_frontier_sha256')
    if (any(type(value.get(key)) is not int or value[key] < 0 for key in counters)
            or type(value.get('complete')) is not bool
            or any(not isinstance(value.get(key), str) or not re.fullmatch('[0-9a-f]{64}', value[key]) for key in hashes)
            or value['operational_count'] != 0
            or value['unassessed_count'] > value['total']
            or value['complete'] != (value['unassessed_count'] == 0)):
        raise ModelError(error)
    # Verify the full frozen proposal frontier, not just a capped packet or
    # caller-provided count. No proposal is approved, removed or deemed irrelevant.
    evidence = getattr(snapshot, 'relation_processing_evidence', None)
    frontier = getattr(snapshot, 'relation_processing_frontier', None)
    if not isinstance(evidence, tuple) or frontier != digest(evidence):
        raise ModelError(error)
    expected = inventory(evidence, scopes, snapshot.assertions)
    expected['source_frontier_sha256'] = snapshot.source_gaps(scopes)['frontier_sha256']
    fields = (*hashes, *counters, 'complete')
    if any(value[key] != expected[key] for key in fields):
        raise ModelError(error)
    return {**{key: value[key] for key in fields}, 'semantic_assessment': 'not_assessed',
        'terminal_candidates_approved': False}

def build(engine, spec, snapshot, context, upstream, receipts, state):
    from .engine import ViewVersion, _now
    from .semantic_views import navigation_contributions
    from .overview_review import PROTOCOL as REVIEW_PROTOCOL
    from .build_proof import remember
    if not engine.verify_overviews or engine.model is None:
        raise ModelError('navigation requires accepted independently reviewed thematic children')
    relation_boundary = _require_source_frontier(snapshot, spec.scopes)
    state['phase'] = 'navigation_membership'
    children = navigation_contributions(engine, upstream, receipts, snapshot, context)
    summary = render(context, children)
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    audit = {'status': 'accepted', 'protocol': REVIEW_PROTOCOL, 'assembly_coverage': 'navigation_index',
        'navigation_protocol': PROTOCOL, 'verification': 'mechanical_membership', 'domain_coverage': 'not_assessed',
        **context, 'relation_boundary': relation_boundary, 'children': children, 'child_manifest_sha256': digest(children), 'summary_sha256': digest(summary)}
    evidence = {row['id']: row for version in upstream.values() for row in version.evidence}
    edges = {row['id']: row for version in upstream.values() for row in version.edges}
    if not set(context['required_source_ids']) <= set(evidence):
        raise ModelError('navigation child proof lost required source membership')
    upstream_ids = {key: value.id for key, value in upstream.items()}
    version = ViewVersion(0, spec.id, summary, [evidence[key] for key in sorted(evidence)],
        [edges[key] for key in sorted(edges)], snapshot.source_version, _now(), snapshot.requested_valid_as_of,
        snapshot.requested_recorded_as_of, upstream_ids, [], engine.pipeline_key, audit)
    fingerprint = digest([PROTOCOL, context, relation_boundary, children, summary])
    remember(state, version.evidence, version.edges, selection_complete=True)
    coverage = snapshot.coverage(engine, spec)
    checked = coverage if snapshot.requested_valid_as_of is None and snapshot.requested_recorded_as_of is None else None
    state['phase'] = 'publication'
    return engine._publish(spec, version, fingerprint, snapshot.source_version, checked,
        coverage_version=coverage, review=audit, snapshot=snapshot, receipts=receipts)


def validate_publication(engine, snapshot, context, version, check):
    """A resumed directory must still bind exactly the same immutable children."""
    from .query_snapshot import receipt
    from .semantic_views import navigation_contributions
    relation_boundary = _require_source_frontier(snapshot, engine._spec(context['view_id']).scopes)
    from .overview_review import PROTOCOL as REVIEW_PROTOCOL
    purpose = {'status': 'accepted', 'protocol': REVIEW_PROTOCOL, 'assembly_coverage': 'navigation_index',
        'navigation_protocol': PROTOCOL, 'verification': 'mechanical_membership', 'domain_coverage': 'not_assessed',
        'relation_boundary': relation_boundary, **context}
    audit = check.get('overview_review') or {}
    for candidate in (audit, version.overview_review or {}):
        if (any(candidate.get(key) != value for key, value in purpose.items())
                or 'relation_gap_review' in candidate or 'processing_gaps_apply_to_query' in candidate):
            return False
    try:
        upstream = {key: engine.get_version(value) for key, value in version.upstream.items()}
        receipts = {child['view_id']: receipt(engine, child['check_id']) for child in audit['children']}
        children = navigation_contributions(engine, upstream, receipts, snapshot, context)
        return children == audit['children'] and render(context, children) == version.summary
    except (KeyError, ValueError, ModelError):
        return False
