"""Keep acquired source proof when planning, matching or generation fails."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, replace


def accepted_review(engine, review):
    if review and ((review.get('certificate') or {}).get('kind')=='answer_applicability' or review.get('status') == 'unresolved'):
        return False
    if not engine.verify_overviews or engine.model is None:
        return True
    from .overview_review import PROTOCOL
    return bool(review and review.get('status') == 'accepted' and review.get('protocol') == PROTOCOL)


def accepted_publication(engine,review):
    """Publication validation is separate from permission to reuse an answer."""
    from .navigation_overview import accepted_navigation_review
    return accepted_navigation_review(review) or accepted_review(engine,review)


def remember(state, evidence=(), edges=(), **updates):
    """Union immutable acquisitions; each phase may append but cannot erase proof."""
    for field, rows in (('evidence', evidence), ('edges', edges)):
        merged = {row['id']: row for row in [*state.get(field, ()), *rows]}
        state[field] = [merged[key] for key in sorted(merged)]
    state.update(updates)
    return state


def acquisition_state(engine, spec, snapshot, *, phase='candidate_selection'):
    from .evidence import _expand_evidence
    roots = set(spec.root_ids) & snapshot.assertions.keys()
    evidence, edges = _expand_evidence(roots, snapshot.assertions, snapshot.edges)
    return remember({'phase': phase, 'query_snapshot': snapshot.descriptor(), 'view_id': spec.id,
        'missing_roots': sorted(set(spec.root_ids) - snapshot.assertions.keys()),
        'selection_complete': False, 'review': {}, 'obligations': []}, evidence, edges)


def persist_failure(engine, spec, snapshot, error, state):
    """Persist an unaccepted attempt, without current/history answer promotion."""
    incoming = getattr(error, 'acquisition', {})
    remember(state, incoming.get('evidence', ()), incoming.get('edges', ()))
    state['missing_roots'] = sorted(set(state.get('missing_roots', ())) | set(incoming.get('missing_roots', ())))
    state['upstream'] = {**incoming.get('upstream', {}), **state.get('upstream', {})}
    state.update({key: value for key, value in incoming.items()
                  if key not in ('evidence', 'edges', 'view_id', 'missing_roots', 'upstream')})
    state.setdefault('origin_view_id', incoming.get('view_id', spec.id))
    state['view_id'] = spec.id
    state['query_snapshot'] = snapshot.descriptor()
    error.acquisition = state
    error.failure_view_id = spec.id
    try:
        error.failure_version, error.failure_check = _write_failure(engine, spec, snapshot, error, state)
    except (sqlite3.Error, OSError) as persistence_error:
        state['persistence_error'] = str(persistence_error)
        # No durable cursor can be promised. The caller can expose bounded inline proof.
        error.failure_version = None
        error.failure_check = None


def _write_failure(engine, spec, snapshot, error, state):
    from .engine import ViewVersion, _json, _now, _material_fingerprint, _load_version
    from .query_snapshot import freshness, receipt
    from .model import SemanticUnavailable
    audit = {**state.get('review', {}), 'status': 'unresolved', 'failed_phase': state['phase'],
        'failure_classification':'operational' if getattr(error,'operational_contract',False) or not isinstance(error,SemanticUnavailable) else 'semantic',
        'generation_error': str(error), 'selection_complete': state.get('selection_complete', False),
        'unresolved_obligations': state.get('obligations', []), 'failure_proof': True,
        'selection_revalidation': state.get('selection_revalidation'),
        'replayed_acquisition': state.get('replayed_acquisition', [])}
    evidence = [row for row in state['evidence'] if row['id'] in snapshot.assertions]
    ids = {row['id'] for row in evidence}
    edges = [row for row in state['edges'] if {row['from_id'], row['to_id']} <= ids]
    version = ViewVersion(0, spec.id, '', evidence, edges, snapshot.source_version, _now(),
        snapshot.requested_valid_as_of, snapshot.requested_recorded_as_of, state.get('upstream', {}),
        state.get('missing_roots', []), engine.pipeline_key, audit)
    fingerprint = hashlib.sha256(_json(['failure-proof-v1', asdict(version), snapshot.descriptor()]).encode()).hexdigest()
    with engine.store.transaction(write=True) as connection:
        from .publication_fences import check_guard
        check_guard(engine, connection)
        material = _material_fingerprint(version)
        row = connection.execute('SELECT id,snapshot FROM memory_view_versions WHERE view_id=? AND fingerprint=?',
                                 (spec.id, fingerprint)).fetchone()
        if row:
            saved = _load_version(row)
        else:
            inserted = connection.execute('INSERT INTO memory_view_versions '
                '(view_id,fingerprint,snapshot,material_fingerprint) VALUES (?,?,?,?)',
                (spec.id, fingerprint, _json(asdict(version)), material))
            saved = replace(version, id=inserted.lastrowid)
        observed = freshness(engine, spec, snapshot)
        observed.update({'generation_status': 'unresolved', 'answer_accepted': False})
        check = connection.execute('INSERT INTO memory_view_checks '
            '(view_id,version_id,source_version,coverage_version,valid_cutoff,recorded_cutoff,pipeline_key,'
            'input_fingerprint,checked_at,overview_review,query_snapshot,freshness) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (spec.id, saved.id, snapshot.source_version, snapshot.coverage(engine, spec),
             version.valid_as_of or '', version.recorded_as_of or '', engine.pipeline_key, fingerprint,
             observed['checked_at'], _json(audit), _json(snapshot.descriptor()), _json(observed)))
        from .source_omission import persist as persist_omissions
        persist_omissions(engine,connection,spec,snapshot,audit,check.lastrowid)
        # Admission metadata may extend the check audit after snapshot storage.
        # Return exactly the immutable version, not the shared mutable audit.
        saved=_load_version(connection.execute('SELECT id,snapshot FROM memory_view_versions WHERE id=?',(saved.id,)).fetchone())
        return saved, receipt(engine, check.lastrowid)
