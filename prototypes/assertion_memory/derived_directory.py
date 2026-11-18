"""Read-only scope controller metadata and attributed thematic catalogs."""
import json

from .navigation_catalog import catalog_manifest, catalog_child, catalog_page


def scopes(service, scope):
    with service.store.transaction() as connection:
        rows = connection.execute("SELECT scope FROM memory_derived_work WHERE (? IS NULL OR scope=?) "
            "UNION SELECT e.scope FROM memory_raw_events e JOIN memory_capture_jobs j ON j.event_id=e.id "
            "WHERE (? IS NULL OR e.scope=?) AND j.status!='completed' AND ?=0 ORDER BY scope",
            (scope, scope, scope, scope, service.engine.model is not None)).fetchall()
    return [row[0] for row in rows]


def descriptor(service, scope, *, include_catalog=True):
    # Include root state and catalog from the same read boundary. No worker runs here.
    with service.store.transaction():
        row = _descriptor(service, scope)
        if include_catalog:
            from .navigation_catalog import descriptor as catalog_descriptor
            row['catalog'] = catalog_descriptor(service, scope)
        else:
            row['catalog'] = {'details_inline': False, 'continuation': {
                'tool': 'memory_status', 'arguments': {
                    'scope': scope, 'section': 'navigation_catalog', 'offset': 0, 'limit': 100}}}
        return row


def _descriptor(service, scope):
    rows = service.derived.status(scope)
    if not rows:
        with service.store.transaction() as connection:
            count = connection.execute("SELECT count(*) FROM memory_raw_events e JOIN memory_capture_jobs j "
                "ON j.event_id=e.id WHERE e.scope=? AND j.status!='completed'", (scope,)).fetchone()[0]
        return {'scope': scope, 'state': 'waiting_model', 'root_request_id': None, 'root_view_id': None,
                'current_version_id': None, 'current_check_id': None, 'desired_frontier': None,
                'completed_frontier': None, 'error': None, 'canonical_ready': False,
                'source_pending_count': count, 'continuations': []}
    row = rows[0]
    with service.store.transaction() as connection:
        metadata = connection.execute("SELECT json_extract(result,'$.root_request_id'), "
            "json_extract(checkpoint,'$.overview.failure') "
            "FROM memory_derived_work WHERE scope=?", (scope,)).fetchone()
    request = metadata[0]
    failure = json.loads(metadata[1]) if metadata[1] else None
    manifest = row.get('manifest_id')
    row['root_request_id'] = request or (manifest.rsplit('@', 1)[0] if manifest else None)
    if failure:
        row['failure'] = {key: failure.get(key) for key in ('view_id', 'version_id', 'check_id')}
        row['failure']['continuations'] = history_cursors(
            failure.get('view_id'), failure.get('version_id'), failure.get('check_id'))
    row['canonical_ready'] = True
    row['continuations'] = history_cursors(
        row.get('root_view_id'), row.get('current_version_id'), row.get('current_check_id'))
    view_id, version_id = row.get('root_view_id'), row.get('current_version_id')
    if view_id and version_id:
        _, maximum = service._route_revisions(view_id, None)
        row['continuations'].append({'tool': 'memory_history', 'arguments': {
            'view_id': view_id, 'section': 'routes', 'snapshot_id': maximum, 'offset': 0, 'limit': 6000}})
    if row.get('registry_id'):
        _,maximum=service._registry_revisions(row['registry_id'],None)
        row['continuations'].append({'tool':'memory_history','arguments':{
            'view_id':row['registry_id'],'section':'routes','snapshot_id':maximum,'offset':0,'limit':6000}})
    return row


def history_cursors(view_id, version_id, check_id):
    if not view_id or not version_id:
        return []
    cursors = []
    for section in ('summary', 'evidence', 'edges', 'metadata', 'checks'):
        args = dict(view_id=view_id, version_id=version_id, section=section, offset=0, limit=6000)
        if section == 'checks':
            args['snapshot_id'] = check_id
        cursors.append({'tool': 'memory_history', 'arguments': args})
    return cursors


def directory(service, scope, offset, limit, fits, *, include_catalog=True):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('offset must be nonnegative and limit between 1 and 1000')
    if scope is not None and (not isinstance(scope, str) or not scope.strip()):
        raise ValueError('scope must be nonempty or None for global diagnostics')
    names = scopes(service, scope)
    entries = []
    for name in names[offset:offset + limit]:
        row = descriptor(service, name, include_catalog=include_catalog)
        detail_cursor = {'tool': 'memory_status', 'arguments': {
            'section': 'derived_overview', 'scope': name, 'offset': 0, 'limit': 6000}}
        omitted = []
        for field in ('desired_frontier', 'completed_frontier', 'error'):
            encoded = json.dumps(row[field], ensure_ascii=False)
            if len(encoded) > 1500:
                row[field] = {'details_inline': False, 'characters': len(encoded)}
                omitted.append(field)
        if omitted:
            row.update(details_inline=False, omitted_fields=omitted, details_continuation=detail_cursor)
        candidate = {'entries': [*entries, row], 'total': len(names), 'offset': offset,
                     'limit': limit, 'continuation': detail_cursor}
        if entries and not fits({'derived_overviews': candidate}):
            break
        entries.append(row)
    result = {'entries': entries, 'total': len(names), 'offset': offset, 'limit': limit}
    repaginate(result, scope)
    return result


def repaginate(directory, scope):
    next_offset = directory['offset'] + len(directory['entries'])
    directory['continuation'] = ({'tool': 'memory_status', 'arguments': {
        'section': 'derived_overviews', 'scope': scope, 'offset': next_offset, 'limit': directory['limit']}}
        if next_offset < directory['total'] else None)
