"""Schedule useful upper maps from exact prior memberships before residual discovery.

Prior routing is a proposal, never current answer/check authority. Native builds
must obtain current matching child checks before an upper map can be published.
"""
from __future__ import annotations

import copy
import hashlib
import json

from .model import ModelError


def _origin_row(engine, origin):
    with engine.store.transaction() as connection:
        row = connection.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions '
            'WHERE request_id=? AND revision=? AND view_id=?',
            (origin['request_id'], origin['revision'], origin['view_id'])).fetchone()
    if row is None or hashlib.sha256(row['plan'].encode()).hexdigest() != origin['route_sha256']:
        raise ModelError('ready map proposal lost its immutable origin registry')
    return row


def classification_previous(engine, prior):
    """A partial ready registry must not masquerade as finished classification."""
    if prior is None:
        return None
    manifest = prior['plan']['discovery']
    if manifest.get('partial_ready_groups'):
        row = _origin_row(engine, manifest['origin_prior_registry'])
        return json.loads(row['plan'])['discovery']
    return manifest


def _proposals(engine, scope, fingerprints):
    from .background_overviews import POLICY, registered_navigation_membership
    from .navigation_overview import accepted_navigation_review
    with engine.store.transaction() as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone():
            return [], None
        rows = connection.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions '
            "WHERE json_extract(plan,'$.automatic.policy')=? AND json_extract(plan,'$.automatic.scope')=? "
            'ORDER BY rowid DESC', (POLICY, scope)).fetchall()
    for row in rows:
        plan = json.loads(row['plan']); manifest = plan['discovery']
        if manifest.get('partial_ready_groups'):
            continue
        try:
            registry = registered_navigation_membership(engine, row)
        except ValueError:
            continue
        if registry['historical_registry']:
            continue
        themes = []
        for theme in manifest['themes']:
            members = theme['source_ids']
            if not members or any(key not in fingerprints or
                    fingerprints[key] != manifest['source_fingerprints'].get(key) for key in members):
                continue
            with engine.store.transaction() as connection:
                current = connection.execute('SELECT current_id FROM memory_views WHERE id=?', (theme['view_id'],)).fetchone()
            version = engine.get_version(current[0]) if current and current[0] is not None else None
            if version is not None and accepted_navigation_review(version.overview_review):
                themes.append(copy.deepcopy(theme))
        if len(themes) >= 2:
            origin = {'request_id': row['request_id'], 'revision': row['revision'], 'view_id': row['view_id'],
                      'route_sha256': hashlib.sha256(row['plan'].encode()).hexdigest()}
            return themes, origin
    return [], None


def _publish_partial(engine, spec, request, snapshot, work, fingerprints, revision):
    from .background_overviews import POLICY, PLAN_PROTOCOL, publish_navigation_registry
    assigned = {key for theme in work['themes'] for key in theme['source_ids']}
    manifest = {'request': request, 'pipeline_key': engine.pipeline_key, 'snapshot': snapshot.descriptor(),
        'corpus_revision': revision, 'source_fingerprints': fingerprints, 'source_count': len(fingerprints),
        'themes': work['themes'], 'hierarchy': work['hierarchy'],
        'unassigned_source_ids': sorted(set(fingerprints) - assigned), 'membership_acquisitions': [],
        'explicit_dependency_ids': [], 'dependency_ids': work['dependencies'],
        'partial_ready_groups': True, 'origin_prior_registry': work['origin_prior_registry']}
    plan = {'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [], 'answer_metadata_requests': [],
        'depends_on': work['dependencies'], 'discover_topics': True,
        'reason': 'Prior exact memberships proposed for early reviewed upper maps; residual classification remains pending.',
        'method': 'model', 'protocol': PLAN_PROTOCOL, 'request': request, 'discovery': manifest,
        'automatic': {'policy': POLICY, 'scope': spec.scopes[0], 'root_request_id': spec.id}}
    return publish_navigation_registry(engine, spec, plan)


def _admit_failed_writer(work, policy):
    """One component retry keeps the old plan and never restarts classification."""
    if work.get('stage') != 'failed' or work.get('writer_policy') == policy:
        return False
    groups = [group for level in work.get('hierarchy', []) for group in level.get('groups', [])]
    if not groups or not work.get('dependencies'):
        return False
    # Persist the admission before any provider call; another failure under this
    # policy is terminal for this component until a later explicit policy change.
    work.setdefault('failure_history', []).append({'writer_policy': work.get('writer_policy'),
        'failure': copy.deepcopy(work.get('failure')), 'retry_policy': policy})
    work.update(stage='build', writer_policy=policy)
    work.pop('failure', None)
    return True


def prepare(engine, spec, request, snapshot, state, checkpoint, save):
    """Publish selected group dependencies first, preserving discovery progress."""
    from .background_overviews import _postorder, _restore, _validate_leaf_work_manifest
    from .discovery_frontier import corpus_state
    from .topic_hierarchy import group_definitions
    from .navigation_overview import accepted_navigation_review, WRITER_POLICY
    work = state.setdefault('ready_groups', {})
    if work.get('stage') == 'complete':
        return
    if work.get('stage') == 'failed':
        if not _admit_failed_writer(work, WRITER_POLICY):
            return
        save(checkpoint)
    _, _, fingerprints, revision = corpus_state(spec, snapshot)
    if not work:
        themes, origin = _proposals(engine, spec.scopes[0], fingerprints)
        work.update(stage='plan' if themes else 'complete', themes=themes,
                    origin_prior_registry=origin, accepted={}, writer_policy=WRITER_POLICY)
        save(checkpoint)
    if work['stage'] == 'complete':
        return
    if work['stage'] == 'plan':
        dependencies, hierarchy = group_definitions(engine, spec, request, [row['view_id'] for row in work['themes']])
        work.update(dependencies=dependencies, hierarchy=hierarchy, stage='build')
        save(checkpoint)
    groups = [row['view_id'] for level in work['hierarchy'] for row in level.get('groups', [])]
    if groups:
        route = _publish_partial(engine, spec, request, snapshot, work, fingerprints, revision)
        with engine.store.transaction() as connection:
            row = connection.execute('SELECT revision FROM memory_question_route_versions WHERE request_id=? '
                'AND view_id=? ORDER BY revision DESC LIMIT 1', (spec.id, route['view_id'])).fetchone()
        work.update(registry_id=route['view_id'], route_revision=row['revision'])
        _validate_leaf_work_manifest(engine, snapshot, route, work)
        memo, receipts = _restore(engine, snapshot, work)
        for key in _postorder(engine, groups):
            if key in memo:
                continue
            state['building_view_id'] = key; save(checkpoint)
            version = engine.build_view(key, valid_as_of=snapshot.requested_valid_as_of,
                recorded_as_of=snapshot.requested_recorded_as_of, _snapshot=snapshot, _memo=memo, _receipts=receipts)
            if not accepted_navigation_review(receipts[key]['overview_review']):
                raise ModelError('ready upper map lacks a current accepted navigation check')
            work['accepted'][key] = {'version_id': version.id, 'check_id': receipts[key]['id']}
            save(checkpoint)
        state.pop('building_view_id', None)
    work['stage'] = 'complete'
    save(checkpoint)
