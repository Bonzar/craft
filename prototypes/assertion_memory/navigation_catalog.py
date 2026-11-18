"""Read-only, scope-local routing metadata. Never an accepted semantic resource."""
from __future__ import annotations

import hashlib
import json
import re

# Compatibility for immutable recorded acceptance only. This allowlist never
# authorizes current domain reuse or relaxes build_proof.accepted_review.
_RECORDED_REVIEW_PROTOCOLS = frozenset({'grounded-overview-v12', 'grounded-overview-v13', 'grounded-overview-v14', 'grounded-overview-v15', 'grounded-overview-v16', 'grounded-overview-v17', 'grounded-overview-v18'})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _source_metadata(connection, scope):
    """Project only controller fields from raw/extraction records, never their bodies."""
    rows = connection.execute("""SELECT e.rowid AS watermark,e.id,e.scope,e.recorded_at,
        0 AS context_only,
        coalesce(json_extract(e.metadata,'$.role'),CASE WHEN e.actor IN ('assistant','tool') THEN e.actor ELSE 'user' END) AS origin_role,
        r.id AS run_id,r.completed_at,
        json_object('source_revision',json_object(
            'status',json_extract(r.result,'$.source_revision.status'),
            'event_id',json_extract(r.result,'$.source_revision.event_id'),
            'assertion_ids',json_extract(r.result,'$.source_revision.assertion_ids')),
            'context_truncated',CASE WHEN json_type(r.result,'$.context_truncated')='false' THEN json('false') ELSE NULL END,
            'context_event_ids',json_extract(r.result,'$.context_event_ids'),
            'reviews',json((SELECT json_group_array(json_object(
                'kind',json_extract(value,'$.kind'),'status',json_extract(value,'$.status'),
                'index',CASE WHEN json_type(value,'$.index')='integer' THEN json_extract(value,'$.index') ELSE NULL END)) FROM json_each(r.result,'$.reviews'))),
            'context_recovery',json((SELECT json_group_array(json_object(
                'operation',json_extract(value,'$.operation'),'protocol',json_extract(value,'$.protocol'),
                'attempt',CASE WHEN json_type(value,'$.attempt')='integer' THEN json_extract(value,'$.attempt') ELSE NULL END,
                'omissions',CASE WHEN json_type(value,'$.omissions')='array' THEN json('[]') ELSE NULL END,
                'recovery',json_extract(value,'$.recovery'))) FROM json_each(r.result,'$.context_recovery')))
        ) AS processing
        FROM memory_raw_events e LEFT JOIN memory_extraction_runs r ON r.id=(
            SELECT r2.id FROM memory_extraction_runs r2 WHERE r2.event_id=e.id
            ORDER BY r2.completed_at DESC,r2.id DESC LIMIT 1)
        WHERE e.scope=? ORDER BY e.id""", (scope,)).fetchall()
    from .answer_source_gaps import semantic_terminal
    from .queue import _current_reviews
    output = []
    for row in rows:
        value = dict(row)
        processing = json.loads(value.pop('processing'))
        status = processing['source_revision']['status'] or ('legacy' if row['run_id'] else 'pending')
        reviews = _current_reviews(processing['reviews'])
        gap = status in ('unresolved', 'failed') or any(review.get('status') in ('pending', 'unresolved')
            and review.get('kind') != 'branch_relevance' for review in reviews)
        value.update(status=status, gap=gap, terminal_semantic=semantic_terminal(processing))
        output.append(value)
    return output


def _recorded_review(row):
    if row['check_protocol'] in _RECORDED_REVIEW_PROTOCOLS:
        return (row['version_assembly_coverage'] != 'thematic_navigation' and
                row['check_navigation_review'] is None)
    from .navigation_overview import PROTOCOL, accepted_navigation_review
    if row['check_protocol'] != PROTOCOL:
        return False
    try:
        check, version = (json.loads(row[field])
            for field in ('check_navigation_review', 'version_navigation_review'))
        return (accepted_navigation_review(check) and accepted_navigation_review(version)
                and check.get('final_text_sha256') == version.get('final_text_sha256')
                and check.get('query_context_sha256') == version.get('query_context_sha256'))
    except (TypeError, ValueError):
        return False


def catalog_child(service, entry, registry):
    """Resolve normal publication references without loading summary/evidence bodies.

    Fixed-answer applicability receipts review another question; they are neither
    child publication attempts nor part of this routing catalog identity.
    """
    key, scope = entry['view_id'], registry['scope']
    connection = service.store._connection
    rows = connection.execute("""SELECT c.id AS check_id,c.version_id,c.view_id,c.pipeline_key,
        c.valid_cutoff,c.recorded_cutoff,c.source_version,c.query_snapshot,
        json_extract(c.overview_review,'$.status') AS check_status,
        json_extract(c.overview_review,'$.protocol') AS check_protocol,
        CASE WHEN json_extract(c.overview_review,'$.assembly_coverage')='thematic_navigation'
            THEN c.overview_review END AS check_navigation_review,
        CASE WHEN json_extract(v.snapshot,'$.overview_review.assembly_coverage')='thematic_navigation'
            THEN json_extract(v.snapshot,'$.overview_review') END AS version_navigation_review,
        v.view_id AS version_view_id,json_extract(v.snapshot,'$.view_id') AS snapshot_view_id,
        json_extract(v.snapshot,'$.pipeline_key') AS version_pipeline,
        json_extract(v.snapshot,'$.valid_as_of') AS version_valid,
        json_extract(v.snapshot,'$.recorded_as_of') AS version_recorded,
        json_extract(v.snapshot,'$.overview_review.status') AS version_status,
        json_extract(v.snapshot,'$.overview_review.protocol') AS version_protocol,
        json_extract(v.snapshot,'$.overview_review.certificate.kind') AS version_certificate_kind,
        json_extract(v.snapshot,'$.overview_review.assembly_coverage') AS version_assembly_coverage,
        json_array_length(v.snapshot,'$.missing_roots') AS missing_roots,
        (SELECT count(*) FROM json_each(v.snapshot,'$.evidence')
            WHERE coalesce(json_extract(value,'$.scope'),'')!=?) AS outside_scope
        FROM memory_view_checks c LEFT JOIN memory_view_versions v ON v.id=c.version_id
        WHERE c.view_id=?
        AND coalesce(json_extract(c.overview_review,'$.certificate.kind'),'')!='answer_applicability'
        AND coalesce(json_extract(c.overview_review,'$.assembly_coverage'),'')!='fixed_answer_question_applicability'
        ORDER BY c.id DESC""", (scope, key)).fetchall()
    maximum = connection.execute('SELECT coalesce(max(id),0) FROM memory_view_versions WHERE view_id=?', (key,)).fetchone()[0]
    latest = rows[0] if rows else None
    historical = None
    valid_checks = []
    for row in rows:
        try:
            snapshot = json.loads(row['query_snapshot'])
            valid = (row['view_id'] == row['version_view_id'] == row['snapshot_view_id'] == key
                and row['pipeline_key'] == row['version_pipeline']
                and isinstance(row['pipeline_key'], str) and re.fullmatch('[0-9a-f]{64}', row['pipeline_key'])
                and row['valid_cutoff'] == (row['version_valid'] or '') == (snapshot['requested_valid_as_of'] or '')
                and row['recorded_cutoff'] == (row['version_recorded'] or '') == (snapshot['requested_recorded_as_of'] or '')
                and row['source_version'] == snapshot['source_version']
                and bool(snapshot['effective_valid_as_of']) and bool(snapshot['effective_recorded_as_of'])
                and bool(snapshot['captured_at']) and row['missing_roots'] == 0 and row['outside_scope'] == 0
                and row['check_status'] == row['version_status'] == 'accepted'
                and row['check_protocol'] == row['version_protocol']
                and _recorded_review(row)
                and row['version_certificate_kind'] != 'answer_applicability'
                and row['version_assembly_coverage'] != 'fixed_answer_question_applicability')
        except (TypeError, KeyError, ValueError):
            valid = False
        if valid:
            valid_checks.append(row['check_id'])
            if historical is None:
                historical = {'version_id': row['version_id'], 'check_id': row['check_id'],
                    'pipeline_key': row['pipeline_key'], 'protocol': row['check_protocol'],
                    'acceptance_basis': 'recorded_protocol_acceptance',
                    'purpose': 'routing_only' if row['version_assembly_coverage']=='thematic_navigation' else 'recorded_summary',
                    'assembly_coverage': row['version_assembly_coverage'], 'cutoffs': {
                        'valid_as_of': row['version_valid'], 'recorded_as_of': row['version_recorded']},
                    'query_snapshot': snapshot, 'accepted_at_that_snapshot': True, 'current_validation': 'unknown',
                    'summary_continuation': {'tool': 'memory_history', 'arguments': {
                        'view_id': key, 'version_id': row['version_id'], 'section': 'summary', 'offset': 0, 'limit': 6000}},
                    'check_continuation': {'tool': 'memory_history', 'arguments': {
                        'view_id': key, 'version_id': row['version_id'], 'section': 'checks',
                        'snapshot_id': row['check_id'], 'offset': 0, 'limit': 6000}}}
    status = ('accepted_at_snapshot' if latest and latest['check_id'] in valid_checks else
              'failed' if latest and latest['check_status'] == 'unresolved' else
              'unverified' if latest or maximum else 'not_published')
    descriptor = {**entry, 'label_encoding': 'literal_json_string',
        'definition_route_revision': registry['route_revision'], 'definition_route_sha256': registry['route_sha256'],
        'definition_root_view_id': registry['root_view_id'], 'definition_registry_id':registry.get('registry_id'), 'definition_route_watermark': registry['route_watermark'],
        'definition_registry_historical': registry['historical_registry'],
        'definition_registry_purpose': registry['purpose'],
        'root_request_id': registry['root_request_id'], 'latest_attempt_status': status,
        'version_id': None, 'check_id': None, 'accepted_snapshot': historical, 'domain_answer_current': False}
    literal = json.dumps(entry['question'], ensure_ascii=False)
    # The JSON question field is already exact and quoted. Avoid duplicating
    # pathological escaped labels beyond the bounded page budget.
    if len(literal) <= 4000:
        descriptor['question_literal'] = literal
    if latest:
        descriptor['latest_attempt'] = {'version_id': latest['version_id'], 'check_id': latest['check_id'],
                                        'status': status}
    return descriptor, [maximum, [dict(row) for row in rows]]


def catalog_manifest(service, scope):
    """One consistent metadata transaction. Every identity is recomputed server-side."""
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError('navigation catalog requires an explicit nonempty scope')
    from .background_overviews import registered_navigation_membership
    from .evidence import _EVIDENCE_RELATIONS
    with service.store.transaction() as connection:
        known = [tuple(row) for row in connection.execute(
            'SELECT rowid,assertion_id FROM memory_assertions WHERE scope=? ORDER BY rowid', (scope,))]
        sources = _source_metadata(connection, scope)
        jobs = [dict(row) for row in connection.execute('SELECT j.id,j.event_id,j.status,j.phase,j.attempts,'
            'j.updated_at,j.available_at,j.error,j.waiting_model,j.proposals,j.lease_token,j.lease_until '
            'FROM memory_capture_jobs j LEFT JOIN memory_raw_events e ON j.event_id=e.id '
            'LEFT JOIN memory_assertions a ON j.assertion_id=a.assertion_id '
            'WHERE coalesce(e.scope,a.scope)=? ORDER BY j.id', (scope,))]
        edges = [tuple(row) for row in connection.execute('SELECT e.rowid,e.edge_id FROM memory_edges e '
            'JOIN memory_assertions a ON a.assertion_id=e.from_id JOIN memory_assertions b ON b.assertion_id=e.to_id '
            'WHERE a.scope=? OR b.scope=? ORDER BY e.rowid', (scope, scope))]
        state = [dict(row) for row in connection.execute('SELECT scope,status,desired_frontier,completed_frontier,'
            'result,error,updated_at,lease_token,lease_until,available_at,next_wake_at,error_count,'
            "json_extract(checkpoint,'$._scheduler.snapshot') AS worker_snapshot,"
            "json_extract(checkpoint,'$._scheduler.frontier') AS worker_frontier,"
            "json_extract(checkpoint,'$.overview.root_request_id') AS root_request_id,"
            "json_extract(checkpoint,'$.overview.root_view_id') AS root_view_id,"
            "json_extract(checkpoint,'$.overview.generation') AS generation,"
            "json_extract(checkpoint,'$.overview.route_revision') AS route_revision,"
            "json_extract(checkpoint,'$.overview.stage') AS stage,"
            "json_extract(checkpoint,'$.overview.building_view_id') AS building_view_id,"
            "json_extract(checkpoint,'$.overview.accepted') AS accepted,"
            "json_extract(checkpoint,'$.overview.failure') AS failure, "
            "json_remove(json_extract(checkpoint,'$.overview.navigation_source_coverage'),"
            "'$.uncovered_source_ids') AS navigation_source_coverage "
            'FROM memory_derived_work WHERE scope=?', (scope,))]
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone()
        routes = connection.execute('SELECT rowid AS route_watermark,request_id,revision,view_id,plan FROM memory_question_route_versions '
            "WHERE json_extract(plan,'$.automatic.scope')=? ORDER BY rowid DESC", (scope,)).fetchall() if exists else []
        registries, registry_error = [], None
        for index, row in enumerate(routes):
            try:
                registries.append(registered_navigation_membership(service.engine, row))
            except ValueError:
                if index == 0:
                    registry_error = 'immutable_registry_invalid'
                    break
        if not routes:
            registry_error = 'immutable_registry_unavailable'
        if registry_error:
            registries = []
        naming_snapshot=service.engine.capture_presentation_snapshot()
        entries, selected_ids, proof = [], set(), []
        for index, registry in enumerate(registries):
            for entry in registry['entries']:
                child, identity = catalog_child(service, entry, registry)
                proof.append([registry['route_sha256'], entry['view_id'], identity])
                if child['view_id'] in selected_ids or (index and child['accepted_snapshot'] is None):
                    continue
                from .view_names import presentation
                selected=child.get('accepted_snapshot') or {}
                name,_=presentation(service.engine,child['view_id'],version_id=selected.get('version_id'),snapshot=naming_snapshot,allow_legacy=bool(selected))
                if name['naming_status'] in ('accepted','fallback'):
                    child.update(name)
                child['membership'] = ('historical_registry' if index or registry['historical_registry'] else 'selected_registry')
                entries.append(child)
                selected_ids.add(child['view_id'])
        relation_gaps = relation_operational = 0
        for job in jobs:
            for proposal in json.loads(job['proposals']):
                if not isinstance(proposal, dict) or proposal.get('kind') not in _EVIDENCE_RELATIONS or proposal.get('status') not in ('pending', 'unresolved'):
                    continue
                terminal = (job['status'] == 'completed' and not job['error'] and not job['waiting_model']
                    and all(isinstance(proposal.get(key), str) and proposal[key] for key in ('from_id', 'to_id')))
                relation_gaps += int(terminal)
                relation_operational += int(not terminal)
        selected = registries[0] if registries else {}
        header = {'scope': scope, 'purpose': 'routing_only', 'partial': True, 'domain_coverage': 'not_assessed',
            'completeness': 'incomplete', 'current_validation': 'not_established',
            'catalog_available': bool(registries), 'registry_available': bool(registries),
            'registry_error': registry_error, 'membership_complete_for_registry': bool(registries),
            'registry_purpose': selected.get('purpose'), 'registry_historical': selected.get('historical_registry', False),
            'all_raw_meaning_covered': False, 'known_canonical_count': len(known),
            'canonical_count_definition': 'stored_scope_assertions', 'raw_source_count': len(sources),
            'human_source_count': sum(row['origin_role']=='user' for row in sources),
            'assistant_source_count': sum(row['origin_role']=='assistant' for row in sources),
            'tool_source_count': sum(row['origin_role']=='tool' for row in sources),
            'source_gap_count': sum(row['gap'] for row in sources),
            'source_terminal_gap_count': sum(row['gap'] and row['terminal_semantic'] for row in sources),
            'uninterpreted_raw_count': sum(row['run_id'] is None and not row['context_only'] for row in sources),
            'source_pending_count': sum(job['status'] != 'completed' for job in jobs if job['event_id'] and job['phase'] != 'refresh'),
            'source_running_count': sum(job['status'] == 'running' for job in jobs if job['event_id'] and job['phase'] != 'refresh'),
            'source_failed_count': sum(job['status'] == 'failed' for job in jobs if job['event_id'] and job['phase'] != 'refresh'),
            'refresh_failed_count': sum(job['status'] == 'failed' for job in jobs if job['phase'] == 'refresh'),
            'refresh_pending_count': sum(job['status'] != 'completed' for job in jobs if job['phase'] == 'refresh'),
            'relation_gap_count': relation_gaps, 'relation_operational_count': relation_operational,
            'relation_semantic_assessment': 'not_assessed',
            'source_frontier_sha256': _digest(sources), 'relation_frontier_sha256': _digest(jobs),
            'registered_theme_count': selected.get('registered_theme_count', 0),
            'registered_entry_count': len(selected.get('entries', [])),
            'registered_canonical_count': selected.get('registered_canonical_count', 0),
            'unassigned_canonical_count': selected.get('unassigned_canonical_count', 0),
            'registry_unassessed_canonical_count': selected.get('registry_unassessed_canonical_count', 0),
            'historical_entry_count': sum(row['membership'] == 'historical_registry' for row in entries),
            'available_historical_snapshot_count': sum(row['accepted_snapshot'] is not None for row in entries),
            'unavailable_entry_count': sum(row['accepted_snapshot'] is None for row in entries),
            'notice': 'Quoted questions and historical snapshots are routing metadata. Current domain conclusions require an ordinary memory_query and its independent review.'}
        for work in state:
            coverage=json.loads(work['navigation_source_coverage']) if work['navigation_source_coverage'] else {}
            if (coverage.get('registry_id')!=selected.get('registry_id')
                    or coverage.get('route_revision')!=selected.get('route_revision')
                    or coverage.get('navigation_coverage')!='partial'):
                continue
            header.update({key:coverage[key] for key in ('navigation_coverage','navigation_uncovered_count',
                'required_map_count','published_map_count','covered_source_count','uncovered_source_ids_sha256')})
            header['navigation_state']=work['status']
            header['navigation_coverage_continuation']={'tool':'memory_history','arguments':{
                'view_id':selected['registry_id'],'section':'routes',
                'snapshot_id':selected['route_watermark'],'offset':0,'limit':6000}}
        if naming_snapshot['name_watermark']:
            header['name_watermark']=naming_snapshot['name_watermark']
        identity = [scope, service.engine.pipeline_key, known, sources, jobs, edges, state,
                    [dict(row) for row in routes], registries, proof, header]
        header['catalog_id'] = _digest(identity)
        return header, entries


def _cursor(scope, catalog_id, offset, limit):
    return {'tool': 'memory_status', 'arguments': {'scope': scope, 'section': 'navigation_catalog',
        'catalog_id': catalog_id, 'offset': offset, 'limit': limit}}


def catalog_page(service, scope, catalog_id, offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('offset must be nonnegative and limit between 1 and 1000')
    if catalog_id is not None and (not isinstance(catalog_id, str) or len(catalog_id) != 64):
        raise ValueError('catalog_id must be a server catalog identity')
    if offset and catalog_id is None:
        raise ValueError('continuation requires a pinned catalog_id')
    header, entries = catalog_manifest(service, scope)
    if catalog_id is not None and catalog_id != header['catalog_id']:
        return {**header, 'state': 'stale', 'restart_required': True, 'entries': [],
            'total': len(entries), 'offset': offset, 'limit': limit, 'response_complete': False,
            'continuation': _cursor(scope, header['catalog_id'], 0, limit)}
    if offset > len(entries):
        raise ValueError('offset is outside the navigation catalog')
    from .service import _fits
    result = {**header, 'state': 'available', 'restart_required': False, 'entries': [],
              'total': len(entries), 'offset': offset, 'limit': limit}
    for entry in entries[offset:offset + limit]:
        candidate = {**result, 'entries': [*result['entries'], entry],
                     'continuation': _cursor(scope, header['catalog_id'], offset + len(result['entries']) + 1, limit)}
        if not _fits({'navigation_catalog': candidate, 'response_complete': False}):
            if result['entries']:
                break
            # A pathological escaped label can exceed the SDK's duplicated
            # text/structured envelope even alone. Preserve exact authority via
            # the existing immutable, character-paged route metadata surface.
            entry = dict(entry)
            question = entry.pop('question')
            entry.pop('question_literal', None)
            entry.update(question_inline=False, question_characters=len(question),
                question_sha256=hashlib.sha256(question.encode()).hexdigest(),
                question_continuation={'tool': 'memory_history', 'arguments': {
                    'view_id': entry.get('definition_registry_id') or entry['definition_root_view_id'], 'section': 'routes',
                    'snapshot_id': entry['definition_route_watermark'], 'offset': 0, 'limit': 6000}},
                question_locator={'route_revision': entry['definition_route_revision'], 'view_id': entry['view_id']})
            candidate['entries'] = [entry]
            if not _fits({'navigation_catalog': candidate, 'response_complete': False}):
                raise ValueError('catalog entry metadata exceeds the response budget')
        result['entries'].append(entry)
    next_offset = offset + len(result['entries'])
    result['response_complete'] = next_offset == len(entries)
    result['continuation'] = None if result['response_complete'] else _cursor(scope, header['catalog_id'], next_offset, limit)
    return result


def descriptor(service, scope):
    header, entries = catalog_manifest(service, scope)
    return {**header, 'total': len(entries), 'continuation': _cursor(scope, header['catalog_id'], 0, 100)}
