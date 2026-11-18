"""System-owned reviewed thematic overview for one authorized scope generation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from .build_proof import accepted_review, accepted_publication
from .model import ModelError, SemanticUnavailable
from .question_routes import PLAN_PROTOCOL, _ensure_schema, _route, _publish
from .storage import _timestamp
from .publication_fences import check_guard

POLICY = 'automatic-shared-scope-v1'
AUTOMATIC_LEAF_SCHEDULING_PROTOCOL = 'rootless-reviewed-forest-v2'
REGISTRY_PROTOCOL = 'automatic-navigation-registry-v2'
COMPLETION_PROTOCOL = 'reviewed-search-map-completion-v2'


@dataclass(frozen=True)
class NavigationRequest:
    # Operational discovery descriptor, deliberately not a content ViewSpec.
    id: str
    query: str
    scopes: tuple
    root_ids: tuple = ()
    depends_on: tuple = ()
    max_seeds: int = 8
PURPOSE = 'Navigate the attributed thematic questions and their pinned accepted versions in this scope.'
# Historical automatic overview manifests are readable routing metadata only.
_RECORDED_AUTOMATIC_PURPOSES = (PURPOSE,
    'Summarize the known topics, reported decisions, material conditions, open questions and interfaces in this scope.')


def _identity(engine, scope, snapshot):
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError('background overview scope must be nonempty')
    body = [POLICY, scope, engine.pipeline_key]
    key = 'scope-overview-' + hashlib.sha256(json.dumps(body).encode()).hexdigest()[:32]
    spec = NavigationRequest(key, PURPOSE, (scope,))
    request = {'question': PURPOSE, 'scopes': [scope],
        'valid_as_of': _timestamp(snapshot.requested_valid_as_of) if snapshot.requested_valid_as_of else None,
        'recorded_as_of': _timestamp(snapshot.requested_recorded_as_of) if snapshot.requested_recorded_as_of else None}
    generation = hashlib.sha256(json.dumps([body, snapshot.descriptor()], sort_keys=True).encode()).hexdigest()
    return spec, request, generation


def _save(checkpoint, save):
    save(checkpoint)


def _controller(plan):
    result= {'request_id':plan['automatic']['root_request_id'],'purpose':plan['request']['question'],
        'scope':plan['automatic']['scope'],'required_view_ids':plan['depends_on'],
        'unassigned_source_ids':plan['discovery'].get('unassigned_source_ids',[]),
        'registry_protocol':REGISTRY_PROTOCOL}
    if plan['automatic'].get('rebuild_operation'):result['rebuild_operation']=plan['automatic']['rebuild_operation']
    return result


def _registry_id(plan):
    d=plan['discovery'];request=plan['request'];a=plan['automatic']
    identity=[a['root_request_id'],request['question'],tuple(request['scopes']),tuple(plan['depends_on']),
        plan['related_view_ids'],plan['protocol'],plan['answer_metadata_requests'],plan['subquestions'],
        tuple(d.get('unassigned_source_ids',[])),d['corpus_revision'],d['pipeline_key']]
    if a.get('rebuild_operation'):identity.append(a['rebuild_operation'])
    return 'navigation-registry-'+hashlib.sha256(json.dumps([REGISTRY_PROTOCOL,identity],ensure_ascii=False).encode()).hexdigest()[:40]


def publish_navigation_registry(engine, request, plan):
    """Controller-only publisher; ordinary _publish never gains a bypass flag."""
    from .storage import _timestamp
    from .question_routes import _replaceable
    import copy
    plan=copy.deepcopy(plan)
    plan['automatic']['registry_protocol']=REGISTRY_PROTOCOL
    plan['automatic']['controller']=_controller(plan)
    registry_id=_registry_id(plan)
    if not _owned_navigation_route({'request_id':request.id},plan):raise ModelError('invalid controller registry owner')
    with engine.store.transaction(write=True) as c:
        check_guard(engine,c)
        _ensure_schema(c);saved=_route(c,request.id)
        if saved is not None:
            old=saved['plan'];old_protocol=old.get('automatic',{}).get('registry_protocol')
            before=old.get('discovery',{}).get('snapshot',{});after=plan['discovery']['snapshot']
            frontier=lambda v:(v.get('source_version',-1),v.get('captured_at',''))
            if old_protocol not in (None,REGISTRY_PROTOCOL):raise ModelError('unknown controller registry protocol')
            if old_protocol==REGISTRY_PROTOCOL and (old==plan or frontier(after)<frontier(before)):
                return saved
        revision=c.execute('SELECT coalesce(max(revision),0)+1 FROM memory_question_route_versions WHERE request_id=?',(request.id,)).fetchone()[0]
        encoded=json.dumps(plan,ensure_ascii=False)
        c.execute('INSERT INTO memory_question_route_versions VALUES(?,?,?,?,?)',(request.id,revision,registry_id,encoded,_timestamp(__import__('datetime').datetime.now(__import__('datetime').timezone.utc))))
        c.execute('INSERT INTO memory_question_routes VALUES(?,?,?) ON CONFLICT(request_id) DO UPDATE SET view_id=excluded.view_id,plan=excluded.plan',(request.id,registry_id,encoded))
    return {'view_id':registry_id,'plan':plan}

def _route_for_generation(engine, spec, request, snapshot, state, checkpoint, save):
    from .topic_discovery import discover
    from .discovery_frontier import corpus_state
    from .ready_map_groups import prepare as prepare_ready_groups, classification_previous
    import copy
    try:
        prepare_ready_groups(engine,spec,request,snapshot,state,checkpoint,save)
    except ModelError as error:
        if isinstance(error.__cause__,sqlite3.Error):raise
        state.setdefault('ready_groups',{}).update(stage='failed',failure={
            'type':type(error).__name__,'message':str(error),
            'view_id':state.pop('building_view_id',None)})
        _save(checkpoint,save)
    with engine.store.transaction(write=True) as connection:
        _ensure_schema(connection)
        prior = _route(connection, spec.id)
    previous = classification_previous(engine,prior)
    _,_,fingerprints,revision=corpus_state(spec,snapshot)
    if (previous and prior and not prior['plan']['discovery'].get('partial_ready_groups')
            and previous.get('source_fingerprints')==fingerprints
            and previous.get('pipeline_key')==engine.pipeline_key and previous.get('request')==request):
        # Rebind the existing thematic classification, not its former answers.
        # Unchanged statements need no fresh LLM clustering on a map-policy upgrade.
        manifest=copy.deepcopy(previous);manifest['snapshot']=snapshot.descriptor()
        manifest['corpus_revision']=revision
        plan=copy.deepcopy(prior['plan']);plan['discovery']=manifest
        operation=checkpoint.get('_scheduler',{}).get('frontier',{}).get('rebuild_operation')
        if operation:plan['automatic']['rebuild_operation']=operation
        else:plan['automatic'].pop('rebuild_operation',None)
        route=publish_navigation_registry(engine,spec,plan)
        with engine.store.transaction() as c:
            row=c.execute('SELECT revision FROM memory_question_route_versions WHERE request_id=? '
                'AND view_id=? ORDER BY revision DESC LIMIT 1',(spec.id,route['view_id'])).fetchone()
        state.update(stage='build',registry_id=route['view_id'],route_revision=row['revision'])
        _save(checkpoint,save);return route
    if checkpoint.get('_scheduler',{}).get('frontier',{}).get('rebuild_operation'):
        previous = None  # Explicit whole generation reclassifies, never automatic.
    progress = state.setdefault('discovery', {})
    children, manifest = discover(engine, spec, request, snapshot, previous=previous, progress=progress,
        save_progress=lambda value: _save(checkpoint, save))
    manifest.update(explicit_dependency_ids=[], dependency_ids=children)
    plan = {'reuse_view_id': None, 'related_view_ids': [], 'subquestions': [], 'answer_metadata_requests': [],
        'depends_on': children, 'discover_topics': True, 'reason': 'System-owned initial shared thematic derivation.',
        'method': 'model', 'protocol': PLAN_PROTOCOL, 'request': request, 'discovery': manifest,
        'automatic': {'policy': POLICY, 'scope': spec.scopes[0], 'root_request_id': spec.id}}
    operation=checkpoint.get('_scheduler',{}).get('frontier',{}).get('rebuild_operation')
    if operation:plan['automatic']['rebuild_operation']=operation
    route = publish_navigation_registry(engine, spec, plan)
    if route['plan']['discovery']['corpus_revision'] != manifest['corpus_revision']:
        raise ModelError('background overview cannot consume a different frozen route frontier')
    with engine.store.transaction() as connection:
        revision = connection.execute('SELECT max(revision) FROM memory_question_route_versions '
            'WHERE request_id=? AND view_id=?', (spec.id, route['view_id'])).fetchone()[0]
    state.update(stage='build', registry_id=route['view_id'], route_revision=revision)
    state.pop('discovery', None)
    _save(checkpoint, save)
    return route


def _load_route(engine, spec, state):
    with engine.store.transaction() as connection:
        row = connection.execute('SELECT view_id,plan FROM memory_question_route_versions '
            'WHERE request_id=? AND revision=?', (spec.id, state['route_revision'])).fetchone()
    if row is None or row['view_id'] != state['registry_id']:
        raise ModelError('background overview checkpoint lost its immutable route')
    return {'view_id': row['view_id'], 'plan': json.loads(row['plan'])}


def _postorder(engine, roots):
    done, active, ordered = set(), set(), []
    def visit(key):
        if key in active:
            raise ModelError('background semantic dependencies contain a cycle')
        if key in done:
            return
        active.add(key)
        for child in engine._spec(key).depends_on:
            visit(child)
        active.remove(key)
        done.add(key)
        ordered.append(key)
    for root in roots:
        visit(root)
    return ordered


def _accepted_map(review):
    from .navigation_overview import accepted_navigation_review
    return accepted_navigation_review(review)


def _restore(engine, snapshot, state):
    from .query_snapshot import receipt
    memo, receipts = {}, {}
    for key, saved in state.get('accepted', {}).items():
        try:
            version = engine.get_version(saved['version_id'])
            check = receipt(engine, saved['check_id'])
        except ValueError:
            continue
        if (version.view_id == key and check['view_id'] == key and check['version_id'] == version.id
                and version.pipeline_key == engine.pipeline_key and check['query_snapshot'] == snapshot.descriptor()
                and _accepted_map(check['overview_review'])):
            if _accepted_map(check['overview_review']):
                from .navigation_overview import _digest,_text_hash
                if any(audit.get('final_text_sha256')!=_text_hash(version.summary)
                        or audit.get('evidence_sha256')!=_digest(version.evidence)
                        or audit.get('edges_sha256')!=_digest(version.edges)
                        for audit in (version.overview_review,check['overview_review'])):continue
                context=None
            else:context = navigation_context(engine, engine._spec(key), snapshot)
            if context is not None:
                from .navigation_index import validate_publication
                if not validate_publication(engine, snapshot, context, version, check):
                    continue
            memo[key], receipts[key] = version, check
    return memo, receipts


def _validate_leaf_work_manifest(engine, snapshot, route, state):
    """Authorize current leaf work, without granting complete NAV authority."""
    from .discovery_frontier import corpus_state
    with engine.store.transaction() as connection:
        row = connection.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions '
            'WHERE view_id=? AND revision=?', (state['registry_id'], state['route_revision'])).fetchone()
    if (row is None or row['view_id'] != route['view_id'] or json.loads(row['plan']) != route['plan']
            or not _owned_navigation_route(row, route['plan'])):
        raise ModelError('background leaf work lost its immutable current route')
    try:
        registry = registered_navigation_membership(engine, row)
    except ValueError as error:
        raise ModelError('background leaf work has invalid registry membership') from error
    manifest = route['plan']['discovery']
    spec, request, _ = _identity(engine, registry['scope'], snapshot)
    _, _, fingerprints, revision = corpus_state(spec, snapshot)
    if (row['request_id'] != spec.id or route['plan']['request'] != request
            or manifest.get('snapshot') != snapshot.descriptor()
            or manifest.get('pipeline_key') != engine.pipeline_key
            or manifest.get('corpus_revision') != revision
            or manifest.get('source_fingerprints') != fingerprints
            or manifest.get('dependency_ids') != route['plan']['depends_on']
            or manifest.get('explicit_dependency_ids') != []):
        raise ModelError('background leaf work frontier does not match the frozen snapshot')


def _durable_leaf_failure(engine, key, snapshot, error):
    """Never treat a failed proof write or an unrelated receipt as local failure."""
    from .query_snapshot import receipt
    version, check = getattr(error, 'failure_version', None), getattr(error, 'failure_check', None)
    if (getattr(error, 'failure_view_id', None) != key or version is None or check is None
            or getattr(error, 'acquisition', {}).get('persistence_error')):
        return None
    saved, stored_check = engine.get_version(version.id), receipt(engine, check['id'])
    classification='operational' if getattr(error,'operational_contract',False) else 'semantic'
    if (saved != version or stored_check != check or version.view_id != key
            or version.pipeline_key != engine.pipeline_key or check['view_id'] != key
            or check['version_id'] != version.id or check['pipeline_key'] != engine.pipeline_key
            or check['query_snapshot'] != snapshot.descriptor()
            or not check['overview_review'].get('failure_proof')
            or check['overview_review'].get('status') != 'unresolved'
            or any((audit or {}).get('failure_classification')!=classification
                   for audit in (version.overview_review,check['overview_review']))):
        return None
    failure={'view_id': key, 'version_id': version.id, 'check_id': check['id'], 'error': str(error),
        'failure_kind':classification}
    if classification=='semantic':
        _semantic_failure_receipt(engine,key,snapshot,failure)
    return failure


def _semantic_failure_receipt(engine,key,snapshot,reference):
    """Validate a durable bounded unavailable attempt, never an accepted answer."""
    from .query_snapshot import receipt
    from .overview_review import PROTOCOL
    error='controller lost exact semantic-unavailability receipt'
    if (reference.get('view_id')!=key or reference.get('failure_kind')!='semantic'
            or any(type(reference.get(field)) is not int for field in ('version_id','check_id'))):
        raise ModelError(error)
    version=engine.get_version(reference['version_id']); check=receipt(engine,reference['check_id'])
    if (version.view_id!=key or check['view_id']!=key or check['version_id']!=version.id
            or version.pipeline_key!=engine.pipeline_key or check['pipeline_key']!=engine.pipeline_key
            or check['query_snapshot']!=snapshot.descriptor() or version.summary
            or version.source_version!=snapshot.source_version or check['source_version']!=snapshot.source_version
            or version.valid_as_of!=snapshot.requested_valid_as_of
            or version.recorded_as_of!=snapshot.requested_recorded_as_of):
        raise ModelError(error)
    spec=engine._spec(key)
    for audit in (version.overview_review,check['overview_review']):
        if (not isinstance(audit,dict) or audit.get('protocol')!=PROTOCOL
                or audit.get('failure_proof') is not True or audit.get('status')!='unresolved'
                or audit.get('failure_classification')!='semantic' or audit.get('certificate')
                or audit.get('budget_failure')):
            raise ModelError(error)
        frame=audit.get('query_context') or {}
        if (frame.get('question')!=spec.query or frame.get('scopes')!=list(spec.scopes)
                or frame.get('cutoffs')!={'valid_as_of':snapshot.requested_valid_as_of,
                    'recorded_as_of':snapshot.requested_recorded_as_of}):
            raise ModelError(error)
    freshness=check.get('freshness') or {}
    if (freshness.get('answer_accepted') is not False or freshness.get('generation_status')!='unresolved'
            or freshness.get('covered_corpus_revision')!=snapshot.corpus(engine,spec)):
        raise ModelError(error)
    return {field:reference[field] for field in ('view_id','version_id','check_id')}


def _completed_children(engine,snapshot,route,state):
    """Readiness requires every map, not merely evidence of a failed attempt."""
    _validate_leaf_work_manifest(engine,snapshot,route,state)
    manifest=route['plan']['discovery']
    required={entry['view_id'] for entry in manifest['themes']}
    required.update(group['view_id'] for level in manifest.get('hierarchy',[]) for group in level.get('groups',[]))
    accepted=state.get('accepted',{})
    if set(accepted)!=required or state.get('failed'):
        raise ModelError('required thematic navigation maps remain missing')
    memo,receipts=_restore(engine,snapshot,state)
    if set(memo)!=required:raise ModelError('navigation completion lost exact map publications')
    from .navigation_overview import accepted_navigation_review,_digest,_text_hash
    for key,check in receipts.items():
        version=memo[key]
        for audit in (version.overview_review,check['overview_review']):
            if (not accepted_navigation_review(audit) or audit['final_text_sha256']!=_text_hash(version.summary)
                    or audit['evidence_sha256']!=_digest(version.evidence)
                    or audit['edges_sha256']!=_digest(version.edges)):
                raise ModelError('navigation completion map text or membership differs from proof')
        if ((check.get('freshness') or {}).get('covered_corpus_revision')
                !=snapshot.corpus(engine,engine._spec(key))):
            raise ModelError('navigation completion source or definition changed')
    if state.get('relation_boundary')!=_map_relation_boundary(snapshot,route['plan']['automatic']['scope']):
        raise ModelError('navigation completion relation frontier changed')
    # Completed publications do not imply a complete search map. Discovery may
    # retain eligible canonical records as unassigned; mandatory closure can
    # nevertheless cover some of them, so check actual published evidence.
    uncovered=sorted(set(manifest['source_fingerprints'])-
        {row['id'] for version in memo.values() for row in version.evidence})
    if uncovered:
        state['navigation_source_coverage']={
            'navigation_coverage':'partial','navigation_uncovered_count':len(uncovered),
            'uncovered_source_ids':uncovered,'uncovered_source_ids_sha256':_digest(uncovered),
            'registry_id':state['registry_id'],'route_revision':state['route_revision'],
            'required_map_count':len(required),'published_map_count':len(memo),
            'source_count':len(manifest['source_fingerprints']),
            'covered_source_count':len(manifest['source_fingerprints'])-len(uncovered)}
        error=ModelError('thematic navigation remains incomplete: '+str(len(uncovered))+
            ' stored assertions are outside every accepted map')
        # Existing discovery/residual/membership work has returned its bounded
        # result. Preserve it; a new source frontier may resume classification.
        error.terminal_discovery=True
        error.navigation_coverage_incomplete=True
        error.navigation_source_coverage=state['navigation_source_coverage']
        raise error
    state.pop('navigation_source_coverage',None)
    return [{'view_id':key,**proof} for key,proof in sorted(accepted.items())],[]


def validate_controller_completion(engine,scope,result,*,snapshot=None,operation=None):
    """Read-only validation shared by checkpoint restore and migration readiness."""
    from .query_snapshot import _restore_frozen
    if (not isinstance(result,dict) or result.get('completion_protocol')!=COMPLETION_PROTOCOL
            or result.get('pipeline_key')!=engine.pipeline_key or result.get('scope')!=scope):
        raise ModelError('controller completion protocol or owner changed')
    snapshot=snapshot or _restore_frozen(engine,result['query_snapshot'])
    spec,_,_=_identity(engine,scope,snapshot)
    state={'registry_id':result['registry_id'],'route_revision':result['route_revision'],
        'relation_boundary':result.get('relation_boundary')}
    for field,target in (('accepted_children','accepted'),('unavailable_children','failed')):
        rows=result.get(field)
        if (not isinstance(rows,list) or any(not isinstance(row,dict) or
                set(row)!={'view_id','version_id','check_id'} or not isinstance(row['view_id'],str)
                or any(type(row[key]) is not int for key in ('version_id','check_id')) for row in rows)
                or len({row['view_id'] for row in rows})!=len(rows)):
            raise ModelError('controller completion child references are invalid')
        state[target]={row['view_id']:({key:row[key] for key in ('version_id','check_id')} if target=='accepted'
            else {**row,'failure_kind':'semantic'}) for row in rows}
    route=_load_route(engine,spec,state)
    if operation is not None and route['plan']['automatic'].get('rebuild_operation')!=operation:
        raise ModelError('controller completion belongs to another rebuild operation')
    children,negative=_completed_children(engine,snapshot,route,state)
    if result!=_controller_result(scope,spec,snapshot,route,state,children,negative):
        raise ModelError('controller completion result binding changed')
    return result


def _build(engine, snapshot, route, state, checkpoint, save):
    """All registered themes/groups need faithful maps, never world answers."""
    _validate_leaf_work_manifest(engine,snapshot,route,state)
    memo,receipts=_restore(engine,snapshot,state)
    planned=[entry['view_id'] for entry in route['plan']['discovery']['themes']]
    planned.extend(_postorder(engine,route['plan']['depends_on']))
    from .navigation_overview import accepted_navigation_review
    for key in dict.fromkeys(planned):
        if key in memo and accepted_navigation_review(receipts[key]['overview_review']):continue
        state['building_view_id']=key;_save(checkpoint,save)
        version=engine.build_view(key,valid_as_of=snapshot.requested_valid_as_of,
            recorded_as_of=snapshot.requested_recorded_as_of,_snapshot=snapshot,_memo=memo,_receipts=receipts)
        if not accepted_navigation_review(receipts[key]['overview_review']):
            raise ModelError('required thematic map lacks an accepted navigation review')
        state.setdefault('accepted',{})[key]={'version_id':version.id,'check_id':receipts[key]['id']}
        state.get('failed',{}).pop(key,None);_save(checkpoint,save)
    state.pop('building_view_id',None)
    state['relation_boundary']=_map_relation_boundary(snapshot,route['plan']['automatic']['scope'])
    return _completed_children(engine,snapshot,route,state)


def _map_relation_boundary(snapshot,scope):
    value=snapshot.relation_gaps((scope,))
    return {key:value[key] for key in ('total','operational_count','unassessed_count',
        'complete','frontier_sha256','inventory_sha256','source_frontier_sha256')}


def _controller_result(scope,spec,snapshot,route,state,children,negative):
    manifest = route['plan']['discovery']
    unavailable=sorted((set(entry['view_id'] for entry in manifest['themes']) | {group['view_id'] for level in manifest.get('hierarchy',[]) for group in level.get('groups',[])}) - {row['view_id'] for row in children})
    partial=bool((state.get('relation_boundary') or {}).get('unassessed_count') or snapshot.source_gaps((scope,))['total'] or manifest['unassigned_source_ids'] or unavailable or any(row.get('unresolved_source_ids') for row in manifest.get('membership_acquisitions',[])))
    result={'scope':scope,'state':'idle','root_request_id':spec.id,'root_view_id':None,'version_id':None,'check_id':None,
        'registry_id':route['view_id'],'registry_protocol':REGISTRY_PROTOCOL,'route_revision':state['route_revision'],
        'manifest_id':f"{spec.id}@{state['route_revision']}",'accepted_children':children,
        'query_snapshot':snapshot.descriptor(),'relation_boundary':state.get('relation_boundary'),'semantic_coverage':'partial' if partial else 'complete',
        'navigation_coverage':'complete','domain_coverage':'not_assessed',
        'required_map_count':len(manifest['themes'])+sum(len(level.get('groups',[])) for level in manifest.get('hierarchy',[])),
        'published_map_count':len(children),
        'unavailable_view_ids':unavailable,'unassigned_count':len(manifest['unassigned_source_ids']),
        'theme_ids':[row['view_id'] for row in manifest['themes']], 'source_count':manifest['source_count'],
        'completion_protocol':COMPLETION_PROTOCOL,'pipeline_key':manifest['pipeline_key'],
        'unavailable_children':negative}
    return result


def _run_background_scope(engine, scope, snapshot, checkpoint, save_checkpoint):
    """No scheduling/leases here: the caller owns the frozen frontier and exact-call cache."""
    spec, request, generation = _identity(engine, scope, snapshot)
    from .navigation_overview import PROTOCOL as MAP_PROTOCOL
    if engine.model is None or not engine.verify_overviews:
        return {'scope': scope, 'state': 'waiting_model', 'root_request_id': spec.id,
                'root_view_id': None, 'version_id': None, 'check_id': None, 'unassigned_count': 0, 'theme_ids': []}
    if not any(row['scope'] == scope for row in snapshot.assertions.values()):
        result = {'scope': scope, 'state': 'idle', 'root_request_id': spec.id, 'root_view_id': None,
            'version_id': None, 'check_id': None, 'manifest_id': None, 'unassigned_count': 0,
            'theme_ids': [], 'source_count': 0, 'empty_scope': True}
        checkpoint['overview'] = {'generation': generation, 'stage': 'complete', 'accepted': {}, 'result': result}
        _save(checkpoint, save_checkpoint)
        return result
    state = checkpoint.get('overview')
    if (not isinstance(state,dict) or state.get('generation')!=generation
            or state.get('map_protocol')!=MAP_PROTOCOL):
        state={'generation':generation,'root_request_id':spec.id,'stage':'discovery','accepted':{},'map_protocol':MAP_PROTOCOL}
        checkpoint['overview'] = state
        _save(checkpoint, save_checkpoint)
    if state.get('stage') == 'complete':
        result=validate_controller_completion(engine,scope,state['result'],snapshot=snapshot,
            operation=checkpoint.get('_scheduler',{}).get('frontier',{}).get('rebuild_operation'))
        route=_load_route(engine,spec,state)
        children,negative=_completed_children(engine,snapshot,route,state)
        if result!=_controller_result(scope,spec,snapshot,route,state,children,negative):
            raise ModelError('completed controller checkpoint binding changed')
        return result
    route = (_load_route(engine, spec, state) if state.get('stage') == 'build' else
             _route_for_generation(engine, spec, request, snapshot, state, checkpoint, save_checkpoint))
    children,negative = _build(engine, snapshot, route, state, checkpoint, save_checkpoint)
    result=_controller_result(scope,spec,snapshot,route,state,children,negative)
    state.update(stage='complete', result=result)
    _save(checkpoint, save_checkpoint)
    return result


def _membership_cache(engine,c):
    cache=getattr(engine.store,'_managed_membership_cache',None)
    data_version=c.execute('PRAGMA data_version').fetchone()[0]
    if cache is None:
        c.execute('CREATE TEMP TABLE IF NOT EXISTS managed_membership_dirty(kind TEXT,id TEXT,PRIMARY KEY(kind,id))')
        for table,column,kind in (('memory_question_route_versions','rowid','route'),('memory_views','id','view'),('memory_assertions','assertion_id','source')):
            for action in ('INSERT','UPDATE','DELETE'):
                name='managed_'+kind+'_'+action.lower()
                ref='old' if action=='DELETE' else 'new'
                condition=" WHEN json_remove(old.definition,'$.title')!=json_remove(new.definition,'$.title')" if kind=='view' and action=='UPDATE' else ''
                c.execute('CREATE TEMP TRIGGER IF NOT EXISTS '+name+' AFTER '+action+' ON main.'+table+condition+" BEGIN INSERT OR IGNORE INTO managed_membership_dirty VALUES('"+kind+"',"+ref+'.'+column+'); END')
        cache={'registries':{},'view_routes':{},'source_routes':{},'data_version':data_version}
        # TEMP invalidations belong to the connection. All engines sharing its
        # store must consume the same verified cache, never erase each other's work.
        engine.store._managed_membership_cache=cache
        dirty=None
    elif cache['data_version']!=data_version:
        # Changes from another SQLite connection are not observed by TEMP triggers.
        # Conservatively prove history once again; unchanged calls remain O(1).
        cache['registries'].clear();cache['view_routes'].clear();cache['source_routes'].clear()
        cache['data_version']=data_version;dirty=None
    else:
        dirty=c.execute('SELECT kind,id FROM managed_membership_dirty').fetchall()
    c.execute('DELETE FROM managed_membership_dirty')
    return cache,dirty


def managed_view_ids(engine, pending_ids=()):
    """Cache verified ownership, invalidating only changed registry dependencies."""
    managed=set(pending_ids)
    with engine.store.transaction() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone():return managed
        cache,dirty=_membership_cache(engine,c)
        affected=None if dirty is None else set()
        if dirty is not None:
            for kind,key in dirty:
                if kind=='route':affected.add(int(key))
                else:affected.update(cache[kind+'_routes'].get(key,()))
        rows=c.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions').fetchall() if affected is None else c.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions WHERE rowid IN (SELECT value FROM json_each(?))',(json.dumps(sorted(affected)),)).fetchall()
        if affected is not None:
            for key in affected:
                old=cache['registries'].pop(key,None)
                if old:
                    for kind,keys in (('view',old['views']),('source',old['sources'])):
                        for item in keys:cache[kind+'_routes'][item].discard(key)
        for row in rows:
            plan=json.loads(row['plan']);discovery=plan.get('discovery',{})
            sources=set(discovery.get('source_fingerprints',{}))
            views={entry['view_id'] for entry in discovery.get('themes',[])}
            views.update(entry['view_id'] for level in discovery.get('hierarchy',[]) for entry in level.get('groups',[]))
            views.add(row['request_id']);key=row['route_watermark']
            for kind,keys in (('view',views),('source',sources)):
                for item in keys:cache[kind+'_routes'].setdefault(item,set()).add(key)
            cache['registries'][key]={'managed':set(),'views':views,'sources':sources}
            try:registry=registered_navigation_membership(engine,row)
            except ValueError:continue
            ids={entry['view_id'] for entry in registry['entries']}
            if registry['root_view_id']:ids.add(registry['root_view_id'])
            # Request metadata IDs do not become content through this cache.
            try:alias=engine._spec(registry['root_request_id'])
            except ValueError:alias=None
            if alias and alias.query==registry['purpose'] and alias.scopes==(registry['scope'],):ids.add(alias.id)
            plan=json.loads(row['plan']);sources=set(plan['discovery']['source_fingerprints'])
            key=row['route_watermark'];views=ids|{registry['root_request_id']}
            cache['registries'][key]={'managed':ids,'views':views,'sources':sources}
            for kind,keys in (('view',views),('source',sources)):
                for item in keys:cache[kind+'_routes'].setdefault(item,set()).add(key)
        # Reuse the aggregate set itself when no affected route changed.
        if dirty is None or affected:
            cache['managed']=set().union(*(row['managed'] for row in cache['registries'].values())) if cache['registries'] else set()
        managed.update(cache.get('managed',()))
    return managed


def run_background_scope(engine, scope, snapshot, checkpoint, save_checkpoint):
    """Retain acquired proof even before the first scope route can be published."""
    try:
        result = _run_background_scope(engine, scope, snapshot, checkpoint, save_checkpoint)
    except ModelError as error:
        state=checkpoint.setdefault('overview',{})
        if getattr(error,'navigation_source_coverage',None):
            state['navigation_source_coverage']=error.navigation_source_coverage
        from .topic_discovery import corpus_records
        records=() if getattr(error,'navigation_coverage_incomplete',False) else \
            corpus_records(NavigationRequest('',PURPOSE,(scope,)),snapshot)[0]
        state['failure']={'kind':'navigation_coverage' if getattr(error,'navigation_coverage_incomplete',False) else 'operational','scope':scope,'phase':state.get('stage','discovery'),
            'registry_id':state.get('registry_id'),'request_id':state.get('root_request_id'),
            'view_id':getattr(error,'failure_view_id',None),'version_id':getattr(getattr(error,'failure_version',None),'id',None),
            'check_id':(getattr(error,'failure_check',None) or {}).get('id'),'query_snapshot':snapshot.descriptor(),
            'source_ids':[row['id'] for row in records],'error':str(error),'error_type':type(error).__name__,
            'terminal_discovery':bool(getattr(error,'terminal_discovery',False))}
        if getattr(error,'navigation_coverage_incomplete',False):
            state['failure'].pop('source_ids',None)  # Exact residual already saved once above.
        _save(checkpoint,save_checkpoint);raise
    state = checkpoint.get('overview', {})
    if state.pop('failure', None) is not None:
        _save(checkpoint, save_checkpoint)
    return result




def technical_root(engine,view_id):
    """True only for a verified archival controller content handle."""
    with engine.store.transaction() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone():return False
        rows=c.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions WHERE view_id=? OR request_id=?',(view_id,view_id)).fetchall()
    for row in rows:
        try:registry=registered_navigation_membership(engine,row)
        except ValueError:continue
        if view_id==registry['root_view_id']:return True
        if view_id==registry['root_request_id']:
            try:spec=engine._spec(view_id)
            except ValueError:continue
            if spec.query==registry['purpose'] and spec.scopes==(registry['scope'],):return True
    return False


def _operational_sources(snapshot,scope):
    for row in snapshot.source_processing_evidence:
        if row['scope']==scope and not row.get('context_only') and (row.get('status')=='pending' or row.get('gap')) and not row.get('terminal_semantic'):
            raise ModelError('navigation source processing frontier has operational gaps')

def automatic_request(engine, spec, request):
    """Recognize the server identity before its discovery route is published."""
    if len(spec.scopes) != 1:
        return False
    scope = spec.scopes[0]
    body = [POLICY, scope, engine.pipeline_key]
    root_id = 'scope-overview-' + hashlib.sha256(json.dumps(body).encode()).hexdigest()[:32]
    return (spec.id == root_id and spec.query == PURPOSE and request.get('question') == PURPOSE
            and request.get('scopes') == [scope])

def _owned_navigation_route(row, plan):
    """Strict current NAV purpose; historical catalogs grant no build authority."""
    return _owned_automatic_route(row, plan, (PURPOSE,))


def _owned_automatic_route(row, plan, purposes):
    """Controller route identity, with an explicit caller-owned purpose allowlist."""
    if not isinstance(plan, dict):
        return False
    automatic, discovery = plan.get('automatic', {}), plan.get('discovery', {})
    if not isinstance(automatic, dict) or not isinstance(discovery, dict):
        return False
    scope = automatic.get('scope')
    if not isinstance(scope, str) or not scope:
        return False
    body = [POLICY, scope, discovery.get('pipeline_key')]
    root_id = 'scope-overview-' + hashlib.sha256(json.dumps(body).encode()).hexdigest()[:32]
    request = plan.get('request', {})
    if not isinstance(request, dict):
        return False
    return (automatic.get('policy') == POLICY and automatic.get('root_request_id') == root_id
        and row['request_id'] == root_id and plan.get('protocol') == PLAN_PROTOCOL
        and plan.get('method') == 'model' and plan.get('reuse_view_id') is None
        and isinstance(request.get('question'), str) and request['question'] in purposes
        and request.get('scopes') == [scope] and discovery.get('request') == request)


def registered_navigation_membership(engine, row):
    """Validate stored routing structure ONLY, including old/incomplete generations.

    This deliberately proves no current corpus, source readiness, child acceptance,
    domain coverage or full navigation authority. Never use it to authorize a build.
    """
    from .model import ModelError
    error = 'immutable_registry_invalid'
    try:
        plan = json.loads(row['plan'])
        if not _owned_automatic_route(row, plan, _RECORDED_AUTOMATIC_PURPOSES):
            raise ValueError(error)
        discovery, scope = plan['discovery'], plan['automatic']['scope']
        purpose = plan['request']['question']
        marker=plan['automatic'].get('registry_protocol')
        if marker not in (None,REGISTRY_PROTOCOL):raise ValueError(error)
        rootless=marker==REGISTRY_PROTOCOL
        if rootless and (purpose!=PURPOSE or plan['automatic'].get('controller')!=_controller(plan)):raise ValueError(error)
        historical_registry = purpose != PURPOSE
        pipeline = discovery['pipeline_key']
        if not isinstance(pipeline, str) or not pipeline:
            raise ValueError(error)
        request_id = row['request_id']
        # Recompute the immutable automatic materialization identity, as _publish does.
        identity = [request_id, purpose, (scope,), tuple(plan.get('depends_on', ())),
            plan['related_view_ids'], plan['protocol'], plan['answer_metadata_requests'], plan['subquestions'],
            tuple(discovery.get('unassigned_source_ids', ())), discovery['corpus_revision'], pipeline]
        root_id = _registry_id(plan) if rootless else request_id + '-plan-' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
        if row['view_id'] != root_id:
            raise ValueError(error)
        themes = discovery.get('themes', [])
        groups = [group for level in discovery.get('hierarchy', []) for group in level.get('groups', [])]
        entries = themes + groups
        by_id = {entry['view_id']: entry for entry in entries}
        leaves = {entry['view_id'] for entry in themes}
        if len(by_id) != len(entries):
            raise ValueError(error)
        def ids(value):
            if (not isinstance(value, list) or any(not isinstance(key, str) or not key for key in value)
                    or len(value) != len(set(value))):
                raise ValueError(error)
            return value
        unassigned = ids(discovery.get('unassigned_source_ids', []))
        required = ids(plan.get('depends_on', []))
        if not rootless:
            root = engine._spec(root_id)
            if (root.query != purpose or root.scopes != (scope,) or list(root.depends_on) != required
                    or list(root.root_ids) != unassigned):raise ValueError(error)
        reached, active, source_ids = set(), set(), set()
        def visit(key):
            if key in active or key not in by_id:
                raise ValueError(error)
            if key in reached:
                return
            entry, spec = by_id[key], engine._spec(key)
            question = entry['question']
            if (not isinstance(question, str) or not question.strip() or len(question) > 2000
                    or spec.scopes != (scope,) or spec.query != question):
                raise ValueError(error)
            active.add(key)
            if key in leaves:
                members = ids(entry['source_ids'])
                expected = 'theme-' + hashlib.sha256(json.dumps([question, (scope,), members], ensure_ascii=False).encode()).hexdigest()[:32]
                if not members or list(spec.root_ids) != members or spec.depends_on or expected != key:
                    raise ValueError(error)
                source_ids.update(members)
            else:
                members = ids(entry['depends_on'])
                identity = [question, (scope,), members]
                if not historical_registry:
                    identity.append('navigation_index')
                expected = 'theme-group-' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32]
                if not members or list(spec.depends_on) != members or spec.root_ids or expected != key:
                    raise ValueError(error)
                for child in members:
                    visit(child)
            active.remove(key)
            reached.add(key)
        for key in required:
            visit(key)
        fingerprints = discovery.get('source_fingerprints', {})
        if (reached != set(by_id) or not isinstance(fingerprints, dict)
                or set(fingerprints) != source_ids | set(unassigned)
                or discovery.get('source_count') != len(fingerprints)):
            raise ValueError(error)
        with engine.store.transaction() as connection:
            authorized = {r[0] for r in connection.execute('SELECT assertion_id FROM memory_assertions '
                'WHERE scope=? AND assertion_id IN (SELECT value FROM json_each(?))',
                (scope, json.dumps(sorted(fingerprints))))}
        if authorized != set(fingerprints):
            raise ValueError(error)
        unassessed = set()
        for acquisition in discovery.get('membership_acquisitions', []):
            unassessed.update(ids(acquisition.get('unresolved_source_ids', [])))
        if not unassessed <= authorized:
            raise ValueError(error)
        return {'root_request_id': request_id, 'root_view_id': None if rootless else root_id,
            'registry_id':root_id if rootless else None,'registry_protocol':marker,'scope': scope,
            'purpose': purpose, 'historical_registry': historical_registry,
            'route_revision': row['revision'], 'route_watermark': row['route_watermark'], 'pipeline_key': pipeline,
            'route_sha256': hashlib.sha256(row['plan'].encode()).hexdigest(),
            'entries': [{'view_id': key, 'question': by_id[key]['question'],
                         'kind': 'theme' if key in leaves else 'group'} for key in sorted(by_id)],
            'registered_theme_count': len(leaves), 'registered_canonical_count': len(source_ids),
            'unassigned_canonical_count': len(unassigned), 'registry_unassessed_canonical_count': len(unassessed),
            'snapshot': discovery['snapshot']}
    except (AttributeError, KeyError, TypeError, ValueError, ModelError, RecursionError) as failure:
        raise ValueError(error) from failure


def _navigation_routes(engine, spec):
    """Purpose authority comes from persisted server routes, never source data."""
    with engine.store.transaction() as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone()
        rows = connection.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions '
            "WHERE json_extract(plan,'$.automatic.policy')=? ORDER BY revision DESC", (POLICY,)).fetchall() if exists else []
    for row in rows:
        plan = json.loads(row['plan'])
        if not _owned_navigation_route(row, plan):
            continue
        discovery = plan['discovery']
        groups = [group for level in discovery.get('hierarchy', []) for group in level.get('groups', [])]
        group = next((group for group in groups if group.get('view_id') == spec.id), None)
        if (row['view_id'] == spec.id and not plan['automatic'].get('registry_protocol')) or group is not None:
            yield row, plan, group


def is_navigation_definition(engine, spec):
    """Exclude unbuilt automatic parents from domain-equivalent answer reuse."""
    return any(True for _ in _navigation_routes(engine, spec))


def thematic_navigation_context(engine,spec,snapshot=None):
    """Authenticate meaningful map membership from immutable server registries."""
    if len(spec.scopes)!=1:return None
    with engine.store.transaction() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_question_route_versions'").fetchone():return None
        rows=c.execute('SELECT *,rowid AS route_watermark FROM memory_question_route_versions '
            "WHERE json_extract(plan,'$.automatic.policy')=? AND json_extract(plan,'$.automatic.scope')=? "
            'ORDER BY rowid DESC',(POLICY,spec.scopes[0])).fetchall()
    for row in rows:
        try:registry=registered_navigation_membership(engine,row)
        except ValueError:continue
        if registry['historical_registry'] or not any(entry['view_id']==spec.id for entry in registry['entries']):continue
        plan=json.loads(row['plan']);manifest=plan['discovery']
        if snapshot is not None:
            with engine.store.transaction() as c:
                from .question_routes import _route
                latest=_route(c,row['request_id'])
            if (registry['pipeline_key']!=engine.pipeline_key or not latest
                    or latest['view_id']!=row['view_id'] or latest['plan']!=plan):
                raise ModelError('navigation map requires the current server registry')
            _validate_leaf_work_manifest(engine,snapshot,{'view_id':row['view_id'],'plan':plan},
                {'registry_id':row['view_id'],'route_revision':row['revision']})
        leaves={item['view_id']:item for item in manifest['themes']}
        groups={item['view_id']:item for level in manifest.get('hierarchy',[]) for item in level.get('groups',[])}
        def members(key):
            if key in leaves:return set(leaves[key]['source_ids'])
            return set().union(*(members(child) for child in groups[key]['depends_on']))
        required=members(spec.id)
        if snapshot is not None:
            if not required<=snapshot.assertions.keys():raise ModelError('navigation map membership needs current discovery')
            if spec.depends_on:
                from .evidence import _expand_evidence
                evidence,_=_expand_evidence(required,snapshot.assertions,snapshot.edges)
                required={item['id'] for item in evidence}
        return {'purpose':'thematic_navigation','view_id':spec.id,'scope':spec.scopes[0],
            'registry_id':registry['registry_id'],'route_revision':registry['route_revision'],
            'registry_protocol':registry['registry_protocol'],'required_source_ids':sorted(required),
            'required_view_ids':list(spec.depends_on),'route_sha256':registry['route_sha256'],
            **({'origin_prior_registry':manifest['origin_prior_registry']} if manifest.get('partial_ready_groups') else {})}
    return None


def navigation_context(engine, spec, snapshot, route_revision=None):
    """Resolve only an exact server root/group in a frozen automatic route.

    Leaves, ordinary questions and views borrowing a managed child stay semantic.
    The immutable revision, definition and entire frontier must all match.
    """
    from .topic_discovery import corpus_revision
    matched = False
    for row, plan, group in _navigation_routes(engine, spec):
        if route_revision is not None and row['revision'] != route_revision:
            continue
        matched = True
        registry=registered_navigation_membership(engine,row)
        automatic, discovery = plan['automatic'], plan['discovery']
        root, request, _ = _identity(engine, automatic['scope'], snapshot)
        if (row['request_id'] != root.id or plan.get('request') != request
                or discovery.get('pipeline_key') != engine.pipeline_key
                or discovery.get('snapshot') != snapshot.descriptor()
                or discovery.get('corpus_revision') != corpus_revision(root, snapshot)):
            continue
        if discovery.get('unassigned_source_ids') or any(item.get('unresolved_source_ids')
                for item in discovery.get('membership_acquisitions', [])):
            raise ModelError('navigation has unassigned required sources')
        required = plan.get('depends_on', []) if group is None else group.get('depends_on', [])
        question = PURPOSE if group is None else group.get('question')
        if (spec.scopes != (automatic['scope'],) or spec.query != question or list(spec.depends_on) != required
                or len(required) != len(set(required)) or not required or spec.root_ids):
            raise ModelError('navigation route membership does not match the immutable definition')
        leaves = {theme['view_id']: theme for theme in discovery.get('themes', [])}
        groups = [entry for level in discovery.get('hierarchy', []) for entry in level.get('groups', [])]
        by_group = {entry['view_id']: entry for entry in groups}
        reached, active = set(), set()
        def visit(key):
            if key in active:
                raise ModelError('navigation hierarchy contains a cycle')
            if key in leaves:
                leaf = engine._spec(key)
                if (leaf.query != leaves[key]['question'] or leaf.scopes != spec.scopes
                        or sorted(leaf.root_ids) != sorted(leaves[key]['source_ids']) or leaf.depends_on):
                    raise ModelError('navigation leaf membership changed')
                reached.add(key)
                return set(leaf.root_ids)
            if key not in by_group:
                raise ModelError('navigation hierarchy lost a required theme')
            defined, entry = engine._spec(key), by_group[key]
            if (defined.query != entry['question'] or defined.scopes != spec.scopes
                    or list(defined.depends_on) != entry['depends_on'] or defined.root_ids):
                raise ModelError('navigation group membership changed')
            active.add(key)
            sources = set()
            for child in entry['depends_on']:
                sources.update(visit(child))
            active.remove(key)
            return sources
        indexed = set()
        for key in plan.get('depends_on', []):
            indexed.update(visit(key))
        if (reached != set(leaves) or indexed != set(discovery.get('source_fingerprints', {}))
                or len(indexed) != discovery.get('source_count')):
            raise ModelError('navigation manifest membership is incomplete')
        sources = set()
        for key in required:
            sources.update(visit(key))
        return {'root_request_id': root.id, 'root_view_id': registry['root_view_id'],
            'registry_id':registry['registry_id'],'route_revision': row['revision'],
            'view_id': spec.id, 'required_view_ids': required, 'required_source_ids': sorted(sources),
            'query_snapshot': snapshot.descriptor(), 'corpus_revision': discovery['corpus_revision'],
            'pipeline_key': engine.pipeline_key, 'route_sha256': hashlib.sha256(row['plan'].encode()).hexdigest()}
    if matched:
        raise ModelError('navigation route frontier does not match the frozen snapshot')
    if route_revision is not None and is_navigation_definition(engine, spec):
        raise ModelError('navigation checkpoint lost its immutable route revision')
    return None
