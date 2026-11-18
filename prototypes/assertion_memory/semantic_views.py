"""Versioned thematic definitions and accepted semantic dependency contributions."""
from __future__ import annotations

from .model import ModelError


def contributions(engine, upstream, receipts, snapshot):
    """Consume only summaries verified at this exact eligible frontier."""
    from .overview_review import PROTOCOL
    from .evidence import _MANDATORY_RELATIONS
    result = []
    for view_id, version in upstream.items():
        review = version.overview_review or {}
        if ((review.get('certificate') or {}).get('kind')=='answer_applicability' or
                ((receipts.get(view_id,{}).get('overview_review') or {}).get('certificate') or {}).get('kind')=='answer_applicability'):
            raise ModelError('applicability certificate cannot supply semantic domain evidence')
        from .question_routes import routing_review
        from .background_overviews import thematic_navigation_context
        if (routing_review(review) or routing_review(receipts.get(view_id, {}).get('overview_review')) or
                thematic_navigation_context(engine, engine._spec(view_id)) is not None):
            raise ModelError('navigation map cannot supply semantic domain evidence')
        # Legacy manual dependencies keep their raw-evidence behavior when no
        # independent overview verifier is configured.
        if not engine.verify_overviews or engine.model is None:
            continue
        check = receipts.get(view_id)
        if (review.get('status') != 'accepted' or review.get('protocol') != PROTOCOL
                or version.pipeline_key != engine.pipeline_key or not check
                or check['version_id'] != version.id or check['query_snapshot'] != snapshot.descriptor()):
            raise ModelError('semantic dependency lacks an accepted matching-frontier summary')
        spec = engine._spec(view_id)
        result.append({'role': 'verified_view', 'view_id': view_id, 'version_id': version.id,
            'check_id': check['id'], 'question': spec.query, 'scopes': list(spec.scopes),
            'valid_as_of': version.valid_as_of, 'recorded_as_of': version.recorded_as_of,
            'cutoffs': {'valid_as_of': version.valid_as_of, 'recorded_as_of': version.recorded_as_of},
            'summary': version.summary, 'source_ids': sorted(row['id'] for row in version.evidence),
            'required_edge_ids': sorted(row['id'] for row in version.edges if row['kind'] in _MANDATORY_RELATIONS),
            'provenance': {'upstream': version.upstream, 'pipeline_key': version.pipeline_key,
                           'query_snapshot': check['query_snapshot']}})
    return result


def navigation_contributions(engine, upstream, receipts, snapshot, context):
    """Pin accepted direct children; these descriptors attest no domain facts."""
    import hashlib
    import json
    from .build_proof import accepted_review
    from .query_snapshot import receipt
    from .overview_review import _digest, _hash_text
    from .navigation_overview import accepted_navigation_review
    required = context['required_view_ids']
    if set(upstream) != set(required) or len(required) != len(set(required)):
        raise ModelError('navigation child membership is incomplete')
    result = []
    for view_id in required:
        version, check = upstream[view_id], receipts.get(view_id)
        map_publication = any((audit or {}).get('assembly_coverage') == 'thematic_navigation'
            for audit in (version.overview_review, (check or {}).get('overview_review')))
        def publication_review(audit):
            if (audit or {}).get('assembly_coverage') == 'thematic_navigation':
                return accepted_navigation_review(audit)
            return accepted_review(engine, audit)
        if map_publication:
            digest_text = _hash_text(version.summary)
            for audit in (version.overview_review or {}, (check or {}).get('overview_review') or {}):
                frame = audit.get('query_context') or {}
                if (not accepted_navigation_review(audit) or audit.get('final_text_sha256') != digest_text
                        or frame.get('question') != engine._spec(view_id).query
                        or frame.get('scopes') != list(engine._spec(view_id).scopes)
                        or frame.get('cutoffs') != {'valid_as_of': version.valid_as_of, 'recorded_as_of': version.recorded_as_of}
                        or audit.get('query_context_sha256') != _digest(frame)):
                    raise ModelError('navigation map review differs from its saved text or question')
        if (not check or check.get('view_id') != view_id or check.get('version_id') != version.id
                or version.view_id != view_id or version.pipeline_key != engine.pipeline_key
                or check.get('pipeline_key') != engine.pipeline_key
                or check.get('query_snapshot') != snapshot.descriptor()
                or version.valid_as_of != snapshot.requested_valid_as_of
                or version.recorded_as_of != snapshot.requested_recorded_as_of
                or version.missing_roots or not publication_review(version.overview_review)
                or not publication_review(check.get('overview_review'))):
            raise ModelError('navigation child lacks an accepted matching-frontier receipt')
        if engine.get_version(version.id) != version or receipt(engine, check['id']) != check:
            raise ModelError('navigation child proof differs from its immutable publication')
        for record in version.evidence:
            original = snapshot.assertions.get(record['id'])
            if original is None or {k: record.get(k) for k in original} != original:
                raise ModelError('navigation child source revision differs from the frozen frontier')
        edges = {row['id']: row for row in snapshot.edges}
        if any(edges.get(row['id']) != row for row in version.edges):
            raise ModelError('navigation child relation differs from the frozen frontier')
        spec = engine._spec(view_id)
        if spec.scopes != engine._spec(context['view_id']).scopes:
            raise ModelError('navigation child scope differs from its parent')
        # JSON hashes bind exact text, source bodies/spans, clocks and checks.
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result.append({'view_id': view_id, 'version_id': version.id, 'check_id': check['id'],
            'question': spec.query, 'title': spec.title, 'scopes': list(spec.scopes),
            'cutoffs': {'valid_as_of': version.valid_as_of, 'recorded_as_of': version.recorded_as_of},
            'summary_sha256': digest(version.summary), 'evidence_sha256': digest(version.evidence),
            'edges_sha256': digest(version.edges), 'check_sha256': digest(check),
            'assembly_coverage': (check.get('overview_review') or {}).get('assembly_coverage')})
    return result
