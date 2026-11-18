"""Frozen acquisition/build orchestration, shared by direct and semantic views."""
from __future__ import annotations

import hashlib
import json

from .question_routes import select_sources, related_sources
from .build_proof import remember, accepted_review


def build(engine, spec, valid_as_of, recorded_as_of, memo, snapshot, receipts, state):
    from .engine import _json
    from .semantic_views import contributions
    from .background_overviews import thematic_navigation_context
    thematic=thematic_navigation_context(engine,spec,snapshot)
    if thematic is not None:
        upstream=_dependencies(engine,spec,valid_as_of,recorded_as_of,memo,snapshot,receipts,state)
        from .navigation_overview import build as build_map
        return build_map(engine,spec,snapshot,thematic,upstream,receipts,state)
    upstream = _dependencies(engine, spec, valid_as_of, recorded_as_of, memo, snapshot, receipts, state)
    from .background_overviews import navigation_context
    navigation = navigation_context(engine, spec, snapshot)
    if navigation is not None:
        from .navigation_index import build as build_navigation
        return build_navigation(engine, spec, snapshot, navigation, upstream, receipts, state)
    semantic = contributions(engine, upstream, receipts, snapshot)
    upstream_ids = {key: value.id for key, value in upstream.items()}
    corpus = snapshot.corpus(engine, spec)
    coverage = snapshot.coverage(engine, spec)
    checked = coverage if valid_as_of is None and recorded_as_of is None else None
    cached = _cached(engine, spec, valid_as_of, recorded_as_of, corpus, upstream_ids)
    if cached and not set(state.get('recovery_ids',())) <= {row['id'] for row in cached[0].evidence}:
        cached=None
    if cached:
        version, fingerprint, review, lineage = cached
    else:
        evidence, links, missing, lineage = _select(engine, spec, upstream, snapshot, state)
        from .semantic_reuse import revalidate
        reused, reused_review, evidence, links = revalidate(engine, spec, snapshot, evidence, links, upstream_ids, missing, state)
        if reused is not None:
            inventory=snapshot.source_gaps(spec.scopes)
            prior_inventory=(reused_review.get('source_gap_review') or {}).get('inventory',{})
            # Canonical selection equality cannot adjudicate a new zero-claim gap.
            if ((inventory['total'] or prior_inventory.get('total')) and
                    prior_inventory.get('frontier_sha256')!=inventory['frontier_sha256']):
                reused=None
                reused_review={'selection_revalidation':{**state.get('selection_revalidation',{}),
                    'summary_reused':False,'source_gap_frontier_changed':True}}
        if reused is not None:
            inventory=snapshot.relation_gaps(spec.scopes)
            prior_inventory=(reused_review.get('relation_gap_review') or {}).get('inventory',{})
            # Equal canonical selection cannot certify an unreviewed proposal
            # revision or reuse applicability after the source frontier changed.
            if ((inventory['total'] or prior_inventory.get('total')) and any(
                    prior_inventory.get(key)!=inventory[key]
                    for key in ('frontier_sha256','source_frontier_sha256'))):
                reused=None
                reused_review={'selection_revalidation':{**state.get('selection_revalidation',{}),
                    'summary_reused':False,'relation_gap_frontier_changed':True}}
        inputs = [engine.pipeline_key, valid_as_of, recorded_as_of, evidence, links, upstream_ids, missing, lineage]
        if engine.verify_overviews and engine.model is not None:
            from .overview_review import corpus_revision
            inputs.extend([corpus_revision(spec, snapshot.assertions, snapshot.edges, evidence), corpus])
        fingerprint = hashlib.sha256(_json(inputs).encode()).hexdigest()
        version, review = (reused, reused_review) if reused is not None else _previous(engine, spec, fingerprint)
        if version is None:
            version, review = _generate(engine, spec, evidence, links, missing, upstream_ids,
                semantic, snapshot, valid_as_of, recorded_as_of, coverage, state)
            if review is not None and reused_review:
                review.update(reused_review)
    if (review or {}).get('query_context',{}).get('knowledge_at_cutoff') is not None:
        from .recorded_history import from_audit
        if from_audit(engine,spec,review,snapshot.descriptor()) is None:
            # An unchanged empty inventory does not rebind a snapshot-specific
            # certificate. All reuse paths must obtain ordinary independent
            # review at this boundary, retaining the immutable prior attestation.
            version,review=_generate(engine,spec,version.evidence,version.edges,version.missing_roots,
                upstream_ids,semantic,snapshot,valid_as_of,recorded_as_of,coverage,state)
    history=(review or {}).get('query_context',{}).get('knowledge_at_cutoff')
    if history is not None:
        from .recorded_history import RECORDED_HISTORY_PROTOCOL
        # A same-input winner may retain different reviewed wording. Its proof
        # must certify this exact snapshot too, rather than an earlier empty one.
        fingerprint=hashlib.sha256(_json([RECORDED_HISTORY_PROTOCOL,engine.pipeline_key,
            spec.id,valid_as_of,recorded_as_of,version.evidence,version.edges,upstream_ids,
            version.missing_roots,lineage,history['proof_sha256']]).encode()).hexdigest()
    if review is not None and 'selection_index' in state:
        review = {key: value for key, value in review.items() if key != 'selection_index'}
        if state['selection_index']:
            review['selection_index'] = state['selection_index']
    state['phase'] = 'publication'
    remember(state, version.evidence, version.edges)
    return engine._publish(spec, version, fingerprint, snapshot.source_version, checked,
        coverage_version=coverage, review=review, snapshot=snapshot, receipts=receipts, retrieval_lineage=lineage,presentation=state.get('presentation_candidate'))


def _dependencies(engine, spec, valid_as_of, recorded_as_of, memo, snapshot, receipts, state):
    state['phase'] = 'semantic_dependencies'
    upstream = {}
    for view_id in spec.depends_on:
        version = engine.build_view(view_id, valid_as_of=valid_as_of, recorded_as_of=recorded_as_of,
                                    _memo=memo, _snapshot=snapshot, _receipts=receipts)
        upstream[view_id] = version
        remember(state, version.evidence, version.edges, upstream={key: row.id for key, row in upstream.items()})
    return upstream


def _cached(engine, spec, valid_as_of, recorded_as_of, corpus, upstream_ids):
    """Full eligible-corpus equality precedes embedding/ranking calls."""
    from .engine import _json, _load_version
    with engine.store.transaction() as connection:
        row = connection.execute("SELECT v.id,v.snapshot,c.* FROM memory_view_checks c "
            "JOIN memory_view_versions v ON v.id=c.version_id WHERE c.view_id=? AND coalesce(json_extract(c.overview_review,'$.certificate.kind'),'') != 'answer_applicability' "
            "AND c.valid_cutoff=? AND c.recorded_cutoff=? AND c.pipeline_key=? "
            "AND json_extract(c.freshness,'$.covered_corpus_revision')=? "
            "AND json_extract(v.snapshot,'$.upstream')=? "
            "AND coalesce(json_extract(c.overview_review,'$.status'),'') != 'unresolved' ORDER BY c.id DESC LIMIT 1",
            (spec.id, valid_as_of or '', recorded_as_of or '', engine.pipeline_key, corpus, _json(upstream_ids))).fetchone()
    if row:
        review = json.loads(row['overview_review']) if row['overview_review'] else None
        if accepted_review(engine, review):
            indexed = json.loads(row['freshness']).get('retrieval_dependencies', []) if row['freshness'] else []
            return _load_version(row), row['input_fingerprint'], review, indexed
    return None


def _select(engine, spec, upstream, snapshot, state):
    state['phase'] = 'source_indexes'
    indexed, index_missing, lineage = related_sources(engine, spec, snapshot, acquisition=state)
    inherited = [item for version in upstream.values() for item in version.evidence if 'branch_review' not in item]
    state['phase'] = 'candidate_selection'
    recovery=[snapshot.assertions[key] for key in state.get('recovery_ids',()) if key in snapshot.assertions]
    evidence, links = select_sources(engine, spec, snapshot, [*inherited, *indexed,*recovery], acquisition=state)
    missing = sorted((set(spec.root_ids) - snapshot.assertions.keys()) | set(index_missing) | {
        key for version in upstream.values() for key in version.missing_roots})
    remember(state, evidence, links, missing_roots=missing, selection_complete=True)
    return evidence, links, missing, lineage


def _previous(engine, spec, fingerprint):
    from .engine import _load_version, _find_version
    with engine.store.transaction() as connection:
        check = connection.execute('SELECT v.id,v.snapshot,c.overview_review FROM memory_view_checks c '
            "JOIN memory_view_versions v ON v.id=c.version_id WHERE c.view_id=? AND coalesce(json_extract(c.overview_review,'$.certificate.kind'),'') != 'answer_applicability' AND c.input_fingerprint=? "
            'ORDER BY c.id DESC LIMIT 1', (spec.id, fingerprint)).fetchone()
        version = _load_version(check) if check else _find_version(connection, spec.id, fingerprint)
        review = json.loads(check[2]) if check and check[2] else version.overview_review if version else None
    return (version, review) if version and accepted_review(engine, review) else (None, None)


def _generate(engine, spec, evidence, links, missing, upstream_ids, semantic, snapshot,
              valid_as_of, recorded_as_of, coverage, state):
    from .engine import ViewVersion, _now
    state['phase'] = 'generation'
    review = None
    if engine.verify_overviews and engine.model is not None:
        from .overview_review import review_overview
        presentation={}
        summary, evidence, links, review = review_overview(engine, spec, evidence, links, missing,
            snapshot.assertions, snapshot.edges, valid_as_of=valid_as_of, recorded_as_of=recorded_as_of,snapshot=snapshot,presentation_out=presentation,
            **({'upstream_contributions': semantic} if semantic else {}))
        state['presentation_candidate']=presentation
        review['active_coverage_version'] = coverage
        if state.get('selection_index'):
            review['selection_index'] = state['selection_index']
    else:
        summary = engine._summarize(spec, evidence, links, missing,
                                    valid_as_of=valid_as_of, recorded_as_of=recorded_as_of)
    return ViewVersion(0, spec.id, summary, evidence, links, snapshot.source_version, _now(),
                       valid_as_of, recorded_as_of, upstream_ids, missing, engine.pipeline_key, review), review
