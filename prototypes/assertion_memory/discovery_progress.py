"""Resume bounded thematic discovery without copying canonical source bodies."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .capture import declared_kind
from .build_proof import acquisition_state, remember
from .model import ModelError
from .topic_discovery import DISCOVERY_PROTOCOL

PROGRESS_PROTOCOL = 'residual-progress-v2'
MAX_RESIDUAL_PASSES = 2


def _save(progress, callback):
    if callback is not None:
        callback(progress)


def _prepare(engine, spec, request, snapshot, previous, progress, save):
    from .discovery_frontier import corpus_state, retained_membership
    identity = hashlib.sha256(json.dumps([PROGRESS_PROTOCOL, engine.pipeline_key, request, snapshot.descriptor()], sort_keys=True).encode()).hexdigest()
    if (progress.get('protocol') == PROGRESS_PROTOCOL
            and progress.get('identity') == identity and progress.get('prepared')):
        prepared = progress['prepared']
        records = [{**snapshot.assertions[key], 'disposition': declared_kind(snapshot.assertions[key]['text'],
            snapshot.assertions[key]['metadata']) or 'assertion', **prepared['record_states'].get(key, {})}
            for key in prepared['record_ids']]
        ids = set(prepared['edge_ids'])
        return records, [row for row in snapshot.edges if row['id'] in ids]
    records, edges, hashes, revision = corpus_state(spec, snapshot)
    themes, unknown, changed, removed, retained = retained_membership(previous, request, engine.pipeline_key, hashes)
    states = {row['id']: {key: row[key] for key in ('disposition', 'exception_basis', 'conflict_basis') if key in row}
              for row in records if row['disposition'] != (declared_kind(row['text'], row['metadata']) or 'assertion')
              or 'exception_basis' in row or 'conflict_basis' in row}
    progress.clear()
    progress.update(identity=identity, protocol=PROGRESS_PROTOCOL, stage='classify', themes=themes, unassigned_source_ids=sorted(unknown), batches=[],
        residual_discoveries=[], remaining_ids=[row['id'] for row in records if row['id'] in changed], batch_cursor=0,
        prepared={'record_ids': [row['id'] for row in records], 'edge_ids': [row['id'] for row in edges],
            'record_states': states, 'source_fingerprints': hashes, 'corpus_revision': revision,
            'changed_ids': sorted(changed), 'removed_ids': removed, 'retained_count': retained})
    _save(progress, save)
    return records, edges


def _classify_work(engine, request, by_id, progress, work, save):
    from .topic_discovery import _request, _validated, _merge
    from .dialogue import response_object
    if 'themes' not in work:
        batch = [by_id[key] for key in work['source_ids']]
        proposed, omitted = _validated(response_object(engine.model, _request(request, batch)), batch)
        work.update(themes=proposed, unassigned_source_ids=omitted)
        # Routing uncertainty is separate from unresolved semantic membership
        # acquisitions. Remove only IDs actually selected in this response.
        selected = {key for theme in proposed for key in theme['source_ids']}
        progress['unassigned_source_ids'] = sorted(
            (set(progress['unassigned_source_ids']) | set(omitted)) - selected)
        _save(progress, save)
    while work['merge_cursor'] < len(work['themes']):
        _merge(engine, request, progress['themes'], work['themes'][work['merge_cursor']])
        work['merge_cursor'] += 1
        _save(progress, save)


def _residual(engine, request, by_id, progress, work, save):
    from .topic_discovery import MAX_THEMES_PER_CALL
    if len(work['themes']) != MAX_THEMES_PER_CALL or not work['unassigned_source_ids']:
        return
    if 'residual' not in work:
        work['residual'] = {'source_ids': list(work['source_ids']),
            'remaining_ids': list(work['unassigned_source_ids']), 'passes': [], 'status': 'pending',
            'max_passes': MAX_RESIDUAL_PASSES}
        _save(progress, save)
    residual = work['residual']
    while residual['status'] == 'pending':
        passes = residual['passes']
        if not passes or passes[-1].get('complete'):
            # Reserve the pass durably BEFORE a provider call. A replay uses
            # this reservation and the worker's exact-call response cache.
            passes.append({'source_ids': list(residual['remaining_ids']), 'merge_cursor': 0})
            _save(progress, save)
        attempt = passes[-1]
        _classify_work(engine, request, by_id, progress, attempt, save)
        before, after = set(attempt['source_ids']), set(attempt['unassigned_source_ids'])
        residual['remaining_ids'] = sorted(after)
        attempt['complete'] = True
        if not after:
            residual['status'] = 'resolved'
        elif not after < before:
            residual['status'] = 'no_progress'
        elif len(passes) >= MAX_RESIDUAL_PASSES:
            residual['status'] = 'exhausted'
        _save(progress, save)


def _classify(engine, request, records, progress, save):
    from .topic_discovery import _batches
    by_id = {row['id']: row for row in records}
    while progress['remaining_ids']:
        if 'batch' not in progress:
            batch = next(_batches(request, [by_id[key] for key in progress['remaining_ids']]))
            progress['batch'] = {'source_ids': [row['id'] for row in batch], 'merge_cursor': 0}
            _save(progress, save)
        work = progress['batch']
        _classify_work(engine, request, by_id, progress, work, save)
        _residual(engine, request, by_id, progress, work, save)
        progress['batches'].append({key: work[key] for key in ('source_ids', 'themes', 'unassigned_source_ids')})
        if 'residual' in work:
            progress['residual_discoveries'].append(work['residual'])
        progress['remaining_ids'] = progress['remaining_ids'][len(work['source_ids']):]
        progress['batch_cursor'] += 1
        del progress['batch']
        _save(progress, save)


def _definitions(engine, spec, request, progress, save):
    from .topic_discovery import _definitions as define_themes
    from .topic_hierarchy import group_definitions
    if progress.get('stage') == 'ready':
        return
    progress['themes'] = [row for row in progress['themes'] if row['source_ids']]
    assigned = {key for row in progress['themes'] for key in row['source_ids']} | set(progress['unassigned_source_ids'])
    if assigned != set(progress['prepared']['source_fingerprints']):
        raise ModelError('resumed thematic discovery lost canonical source membership')
    leaves = define_themes(engine, spec, progress['themes'])
    progress.update(stage='hierarchy', leaf_ids=leaves)
    _save(progress, save)
    definitions, hierarchy = group_definitions(engine, spec, request, leaves)
    progress.update(stage='ready', definitions=definitions, hierarchy=hierarchy)
    _save(progress, save)


def discover_checkpointed(engine, spec, request, snapshot, previous, progress, save):
    records, edges = _prepare(engine, spec, request, snapshot, previous, progress, save)
    try:
        _classify(engine, request, records, progress, save)
        from .theme_acquisition import acquire_memberships
        acquire_memberships(engine, spec, request, records, progress, lambda value: _save(value, save))
        _definitions(engine, spec, request, progress, save)
    except (ModelError, sqlite3.Error, OSError) as error:
        state = acquisition_state(engine, spec, snapshot, phase='thematic_' + progress['stage'])
        error.acquisition = remember(state, records, edges)
        raise
    prepared = progress['prepared']
    manifest = {'protocol': DISCOVERY_PROTOCOL, 'progress_protocol': PROGRESS_PROTOCOL,
        'residual_discoveries': progress['residual_discoveries'], 'corpus_revision': prepared['corpus_revision'], 'source_fingerprints': prepared['source_fingerprints'],
        'request': request, 'pipeline_key': engine.pipeline_key, 'scan_source_ids': prepared['changed_ids'],
        'removed_source_ids': prepared['removed_ids'], 'retained_source_count': prepared['retained_count'],
        'previous_corpus_revision': previous.get('corpus_revision') if previous else None,
        'source_count': len(records), 'snapshot': snapshot.descriptor(), 'batches': progress['batches'],
        'unassigned_source_ids': progress['unassigned_source_ids'], 'themes': progress['themes'],
        'hierarchy': progress['hierarchy'], 'membership_acquisitions': progress['membership_acquisitions']}
    return progress['definitions'], manifest
