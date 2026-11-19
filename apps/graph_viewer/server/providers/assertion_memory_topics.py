"""Publish only source view memberships and verified route subquestions."""
import json
import math
import re
from ..common import APIError, digest, timestamp
from .version_identity import topic_version_id
from .assertion_memory_names import ReadOnlyNames, capture_names, resolve_name

TOPIC_PROJECTION_POLICY = 'verified-thematic-maps-v3'


def publication_metadata(source, tables, row, definition, version, snapshot, cutoff):
    """Describe this immutable material, never authorize a current domain answer."""
    audit = snapshot.get('overview_review')
    audit = audit if isinstance(audit, dict) else {}
    failure = audit.get('failure_proof')
    metadata = {key: audit.get(key) if isinstance(audit.get(key), str) else None
                for key in ('purpose', 'assembly_coverage', 'domain_coverage')}
    metadata.update(overview_status=audit.get('status') if isinstance(audit.get('status'), str) else None,
                    overview_protocol=audit.get('protocol') if isinstance(audit.get('protocol'), str) else None,
                    overview_failure=failure if type(failure) is bool else None,
                    accepted_at_that_snapshot=None, current_validation='unknown', domain_answer_current=False)
    if failure is True or metadata['overview_status'] in ('unresolved', 'failed'):
        metadata['accepted_at_that_snapshot'] = False
    elif audit.get('status') == 'accepted' and 'memory_view_checks' in tables:
        metadata['accepted_at_that_snapshot'] = recorded_acceptance(
            source, row, definition, version, snapshot, audit, cutoff)
    return metadata


def reviews_match_material(check, frontier, definition, snapshot, audit, review):
    """Bind both saved reviews to this material's question, text and cutoffs."""
    from prototypes.assertion_memory.navigation_overview import _digest, _text_hash
    cutoffs = {'valid_as_of': snapshot.get('valid_as_of'), 'recorded_as_of': snapshot.get('recorded_as_of')}
    if not all(check[stored] == (cutoffs[field] or '') == (frontier.get(requested) or '')
               for field, stored, requested in (('valid_as_of', 'valid_cutoff', 'requested_valid_as_of'),
                                                ('recorded_as_of', 'recorded_cutoff', 'requested_recorded_as_of'))):
        return False
    return all(item.get('status') == 'accepted' and
               item.get('protocol') == audit.get('protocol') and
               item.get('final_text_sha256') == _text_hash(snapshot['summary']) and
               item.get('query_context_sha256') == _digest(item.get('query_context')) and
               (item.get('query_context') or {}).get('question') == definition.get('query') and
               (item.get('query_context') or {}).get('scopes') == definition.get('scopes', []) and
               (item.get('query_context') or {}).get('cutoffs') == cutoffs
               for item in (audit, review))


def recorded_acceptance(source, row, definition, version, snapshot, audit, cutoff):
    """Reuse Core protocol acceptance, with the selected material/check binding."""
    from prototypes.assertion_memory.navigation_catalog import _recorded_review
    from prototypes.assertion_memory.navigation_overview import _digest
    required = {'overview_review', 'query_snapshot', 'pipeline_key', 'valid_cutoff',
                'recorded_cutoff', 'source_version', 'checked_at'}
    for check in source.execute('SELECT * FROM memory_view_checks WHERE view_id=? AND version_id=? ORDER BY id DESC',
                                (row['id'], version['id'])):
        if not required <= set(check.keys()):
            continue
        try:
            review = json.loads(check['overview_review']) if check['overview_review'] else {}
            frontier = json.loads(check['query_snapshot']) if check['query_snapshot'] else {}
            if not isinstance(review, dict) or not isinstance(frontier, dict):
                continue
            if cutoff and (not check['checked_at'] or timestamp(check['checked_at']) > cutoff):
                continue
            if any((item.get('certificate') or {}).get('kind') == 'answer_applicability' or
                   item.get('assembly_coverage') == 'fixed_answer_question_applicability'
                   for item in (audit, review)):
                continue
            scopes = definition.get('scopes', [])
            if (version['view_id'] != row['id'] or snapshot.get('view_id') != row['id'] or
                snapshot.get('missing_roots') != [] or review.get('failure_proof') is True or
                check['pipeline_key'] != snapshot.get('pipeline_key') or
                not isinstance(check['pipeline_key'], str) or not re.fullmatch('[0-9a-f]{64}', check['pipeline_key']) or
                check['source_version'] != frontier.get('source_version') or
                check['source_version'] != snapshot.get('source_version') or
                not all(frontier.get(key) for key in ('effective_valid_as_of', 'effective_recorded_as_of', 'captured_at')) or
                any(scopes and item.get('scope') not in scopes for item in snapshot.get('evidence', []))):
                continue
            if not reviews_match_material(check, frontier, definition, snapshot, audit, review):
                continue
            proof = {'check_protocol': review.get('protocol'),
                     'version_assembly_coverage': audit.get('assembly_coverage'),
                     'check_navigation_review': json.dumps(review) if review.get('assembly_coverage') == 'thematic_navigation' else None,
                     'version_navigation_review': json.dumps(audit) if audit.get('assembly_coverage') == 'thematic_navigation' else None}
            if not _recorded_review(proof):
                continue
            if audit.get('assembly_coverage') == 'thematic_navigation' and not all(
                item.get('evidence_sha256') == _digest(snapshot.get('evidence')) and
                item.get('edges_sha256') == _digest(snapshot.get('edges')) and
                item.get('query_snapshot') == frontier and item.get('pipeline_key') == check['pipeline_key']
                for item in (audit, review)):
                continue
            return True
        except (APIError, TypeError, KeyError, ValueError, AttributeError):
            continue
    return None


def topic_policy_current(manifest):
    return (manifest.get('preparation', {}).get('topic_projection_policy') == TOPIC_PROJECTION_POLICY
            and (not manifest.get('selector',{}).get('session_id') or manifest.get('preparation',{}).get('session_result_policy')=='core-run-provenance-pins-v2-request-context'))


def verified_routing_root_ids(source, tables, *, cutoff=None, memberships=None):
    """Only immutable core registry proof grants omission authority."""
    if 'memory_question_route_versions' not in tables:
        return set()
    from prototypes.assertion_memory.background_overviews import registered_navigation_membership
    engine = ReadOnlyNames(source)
    roots = set(); registries = []
    for row in source.execute('SELECT *, rowid AS route_watermark FROM memory_question_route_versions ORDER BY rowid'):
        try:
            if cutoff and (not row['recorded_at'] or timestamp(row['recorded_at']) > cutoff):
                continue
            registry = registered_navigation_membership(engine, row)
        except (APIError, ValueError):
            continue
        registries.append(registry)
        roots.add(registry['root_view_id'])
        alias = source.execute('SELECT definition FROM memory_views WHERE id=?',
                               (registry['root_request_id'],)).fetchone()
        if alias is not None:
            definition = json.loads(alias['definition'])
            if definition.get('query') == registry['purpose'] and definition.get('scopes') == [registry['scope']]:
                roots.add(registry['root_request_id'])
    if memberships is not None:
        scopes = set()
        # Current-purpose registries take precedence; rowid orders generations.
        for registry in sorted(registries, key=lambda item: (not item['historical_registry'], item['route_watermark']), reverse=True):
            selected = registry['scope'] not in scopes and not registry['historical_registry']
            scopes.add(registry['scope'])
            for entry in registry['entries']:
                memberships.setdefault(entry['view_id'], 'selected_registry' if selected else 'historical_registry')
    return roots


def position(key, radius=350):
    h = digest(key)
    z = int(h[:8],16)/0xffffffff*2-1
    angle = int(h[8:16],16)/0xffffffff*math.tau
    r = math.sqrt(max(0,1-z*z))*radius
    return [r*math.cos(angle),r*math.sin(angle),radius*z]


def excerpt(text, limit=56):
    if len(text)<=limit:
        return text
    cut=text.rfind(' ',0,limit)
    return text[:cut if cut>0 else limit].rstrip()+'…'


def read_pinned_material(source,view_id,version_id,cutoff):
    from .version_identity import topic_version_id
    topic_version_id(view_id,version_id)
    row=source.execute('SELECT * FROM memory_views WHERE id=?',(view_id,)).fetchone()
    version=source.execute('SELECT * FROM memory_view_versions WHERE id=? AND view_id=?',(version_id,view_id)).fetchone()
    if row is None or version is None:raise ValueError('Pinned Source version is unavailable')
    snap=json.loads(version['snapshot']);created=version['created_at'] if 'created_at' in version.keys() else snap.get('created_at')
    if not created or not cutoff or timestamp(created)>cutoff:raise ValueError('Pinned Source version exceeds temporal cutoff')
    if not isinstance(snap,dict) or not isinstance(snap.get('summary'),str) or not isinstance(snap.get('evidence'),list):raise ValueError('Invalid pinned Source material')
    if any(not isinstance(item,dict) or not isinstance(item.get('id',item.get('assertion_id')),str) or not item.get('id',item.get('assertion_id')).strip() for item in snap['evidence']):raise ValueError('Invalid exact Source membership')
    return dict(row),version,snap,created


def versions_for(source, selector, *, version_pins=None, pin_cutoff=None):
    cutoff=selector['at'] if selector['mode']=='known_then' else None
    tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'memory_views','memory_view_versions'}<=tables:
        return [],[],[]
    views=[]
    if version_pins is not None:
        pins=[dict(value,view_id=key) for key,value in version_pins.items()] if isinstance(version_pins,dict) else version_pins
        for pin in sorted(pins,key=lambda value:(value['view_id'],value['version_id'])):
            row,version,snap,created=read_pinned_material(source,pin['view_id'],pin['version_id'],pin_cutoff)
            row['_projection_pin']=pin;views.append((row,json.loads(row['definition']),(version,snap,created)))
        return views,[],tables
    for row in source.execute('SELECT * FROM memory_views ORDER BY id'):
        candidates=[]
        for version in source.execute('SELECT * FROM memory_view_versions WHERE view_id=? ORDER BY id DESC',(row['id'],)):
            snap=json.loads(version['snapshot'])
            created=snap.get('created_at') or (version['created_at'] if 'created_at' in version.keys() else None)
            if cutoff is None or (created and timestamp(created)<=cutoff):
                candidates.append((version,snap,created))
                if cutoff is not None or version['id']==row['current_id']:
                    break
        if not candidates:
            continue
        selected=next((v for v in candidates if v[0]['id']==row['current_id']),candidates[0])
        if selector['mode']=='known_now':
            historical=historical_materialization(source,row['id'],selector['at'],tables)
            if historical:
                selected=historical
        views.append((dict(row),json.loads(row['definition']),selected))
    routes=[]
    if 'memory_question_route_versions' in tables:
        for row in source.execute('SELECT rowid,* FROM memory_question_route_versions ORDER BY revision DESC'):
            if cutoff and (not row['recorded_at'] or timestamp(row['recorded_at'])>cutoff):
                continue
            if not any(r['request_id']==row['request_id'] for r in routes):
                routes.append(dict(row))
    return views,routes,tables


def publish_topics(source, disk, id_ordinals, selector, *, inactive_ids=frozenset(),
                   presentation_snapshot=None, captured_at=None, published_at=None, version_pins=None):
    cutoff=selector['at'] if selector['mode']=='known_then' else None
    views,routes,tables=versions_for(source,selector,version_pins=version_pins,pin_cutoff=cutoff or captured_at)
    navigation_memberships={}
    routing_roots=verified_routing_root_ids(source,tables,cutoff=cutoff,memberships=navigation_memberships)
    if selector['mode']=='current' and version_pins is None:
        views=[entry for entry in views if entry[0]['id'] not in routing_roots]
    presentation=capture_names(source,tables,snapshot=presentation_snapshot,captured_at=captured_at,
                               recorded_as_of=cutoff,published_at=published_at)
    topics,memberships,links=[],[],[]; membership_filtered=False
    definitions={r['id']:definition for r,definition,_ in views}
    indices={r['id']:i for i,(r,_,_) in enumerate(views)}
    for i,(row,definition,(version,snapshot,created)) in enumerate(views):
        ids=[r.get('id',r.get('assertion_id')) for r in snapshot.get('evidence',[]) if isinstance(r,dict)]
        filtered=bool(inactive_ids.intersection(ids)); membership_filtered |= filtered
        members=sorted({id_ordinals[id] for id in ids if id in id_ordinals})
        name=resolve_name(source,tables,row['id'],definition,presentation,version_id=version['id'],allow_legacy=selector['mode']!='current')
        title=name['display_title'] or definition.get('query') or definition.get('title') or ''
        pin=row.get('_projection_pin');topic_id=topic_version_id(row['id'],version['id']) if pin is not None else 'topic:'+row['id']
        topics.append({'id':topic_id,'index':i,'label':title,'full_title':title,
                       **name,'definition_title':definition.get('title'),'query':definition.get('query'),
                       'label_method':'source_editorial_name' if name['naming_status']=='accepted' else 'source_question' if name['naming_status']=='naming_pending' else 'source_title_unreviewed','source_ref':{'view_id':row['id'],'version_id':version['id'],'route_revision':None},
                       **publication_metadata(source,tables,row,definition,version,snapshot,cutoff),
                       'navigation_membership':navigation_memberships.get(row['id']),
                       'position':position(row['id']),'radius':90,'depth':0,'member_count':len(set(ids)) if version_pins is not None else len(members),'freshness':'saved' if version_pins is not None else 'stale' if filtered else source_freshness(source,row,version,selector),'available_at':created})
        if version_pins is not None:
            topics[-1].update(session_result={key:value for key,value in pin.items() if key!='view_id'},unavailable_member_count=len(set(ids))-len(members),accepted_at_that_snapshot=None)
        if 'source_version_id' in snapshot:
            topics[-1]['source_ref']['source_version_id']=snapshot['source_version_id']
        if isinstance(snapshot.get('summary'),str):
            disk.execute('INSERT INTO overviews VALUES(?,?,?,?)',(i,topic_id,snapshot['summary'],digest(snapshot['summary'])))
        memberships.append({'topic_index':i,'node_indices':members})
        disk.executemany('INSERT OR IGNORE INTO memberships VALUES(?,?)',((i,n) for n in members))
    if version_pins is not None:
        pairs={(topic['source_ref']['view_id'],topic['source_ref']['version_id']):topic['index'] for topic in topics}
        for topic in topics:
            pin=topic['session_result']
            for upstream in pin.get('upstream',[]):
                target=pairs.get((upstream['view_id'],upstream['version_id']))
                if target is None:raise ValueError('Exact upstream input is missing')
                links.append({'parent':topic['index'],'child':target,'relation':'prerequisite','provenance':{'run_id':pin['run_id'],'event_id':pin['event_id'],'exact_version_input':True}})
        return {'topics':topics,'memberships':memberships,'links':links,'completeness':'partial','unknowns':['unrecorded_run_history_unknown'],'presentation_snapshot':presentation}
    if 'viewer_topic_links' in tables:
        links=explicit_topic_links(source,indices,cutoff)
    for row,definition,_ in ([] if 'viewer_topic_links' in tables else views):
        verified={}
        for route in routes:
            if route['view_id']!=row['id']:
                continue
            plan=json.loads(route['plan'])
            if plan.get('reuse_view_id') is not None:
                continue
            for sub in plan.get('subquestions',[]):
                question=sub if isinstance(sub,str) else sub.get('question')
                for dep in definition.get('depends_on',[]):
                    other=definitions.get(dep,{})
                    if other.get('query')==question and other.get('scopes',[])==definition.get('scopes',[]):
                        verified[dep]=route['revision']
        for dep in definition.get('depends_on',[]):
            if dep not in indices:
                continue
            links.append({'parent':indices[row['id']],'child':indices[dep],
                          'relation':'subquestion' if dep in verified else 'prerequisite',
                          'provenance':{'view_id':row['id'],'route_revision':verified.get(dep)}})
            if dep in verified:
                topics[indices[row['id']]]['source_ref']['route_revision']=verified[dep]
    unknowns=hierarchy(topics,links)
    if membership_filtered:
        unknowns.append('source_topic_membership_filtered_by_revision')
    if selector['mode']=='known_now' and any(not dict(version).get('historical_exact') for _,_,(version,_,_) in views):
        unknowns.append('historical_topic_membership_partial_current_materialization')
    return {'topics':topics,'memberships':memberships,'links':links,'completeness':'partial' if unknowns else 'complete','unknowns':unknowns,'presentation_snapshot':presentation}


def explicit_topic_links(source, indices, cutoff):
    """Supplied IR relationships have source provenance, never planner routes."""
    links=[]
    for row in source.execute('SELECT * FROM viewer_topic_links ORDER BY id'):
        if cutoff is not None and timestamp(row['recorded_at'])>cutoff:
            continue
        if row['parent_id'] not in indices or row['child_id'] not in indices:
            continue
        links.append({'parent':indices[row['parent_id']],'child':indices[row['child_id']],
                      'relation':row['relation'],
                      'provenance':{'source_link_id':row['id'],'recorded_at':row['recorded_at'],
                                    'source_provenance':json.loads(row['provenance'])}})
    return links


def hierarchy(topics, links):
    indegrees=[0]*len(topics); adjacency=[[] for _ in topics]
    for link in links:
        if link['relation']=='subquestion':
            adjacency[link['parent']].append(link['child']); indegrees[link['child']]+=1
    queue=[i for i,n in enumerate(indegrees) if n==0]; visited=0
    for node in queue:
        visited+=1
        for child in adjacency[node]:
            topics[child]['depth']=max(topics[child]['depth'],topics[node]['depth']+1)
            indegrees[child]-=1
            if indegrees[child]==0:
                queue.append(child)
    return ['source_topic_hierarchy_cycle'] if visited<len(topics) else []


def historical_materialization(source,view_id,at,tables):
    candidate=None
    if 'memory_historical_checks' in tables:
        candidate=source.execute("SELECT v.*,h.source_version AS historical_source_version FROM memory_historical_checks h JOIN memory_view_versions v ON v.id=h.version_id WHERE h.view_id=? AND h.valid_cutoff=? AND h.recorded_cutoff='' ORDER BY h.source_version DESC LIMIT 1",(view_id,at)).fetchone()
    if candidate is None and 'memory_view_checks' in tables:
        columns={r[1] for r in source.execute('PRAGMA table_info(memory_view_checks)')}
        if {'valid_cutoff','recorded_cutoff'}<=columns:
            candidate=source.execute("SELECT v.*,h.source_version AS historical_source_version FROM memory_view_checks h JOIN memory_view_versions v ON v.id=h.version_id WHERE h.view_id=? AND h.valid_cutoff=? AND h.recorded_cutoff='' ORDER BY h.id DESC LIMIT 1",(view_id,at)).fetchone()
    if candidate is None:
        return None
    version=dict(candidate); version['historical_exact']=True
    snapshot=json.loads(version['snapshot'])
    return version,snapshot,snapshot.get('created_at') or version.get('created_at')


def source_freshness(source,row,version,selector):
    if selector['mode']=='known_then':
        return 'unknown'
    state=source.execute('SELECT version FROM memory_state LIMIT 1').fetchone()[0]
    version=dict(version)
    covered=version.get('historical_source_version',row.get('checked_version'))
    if covered is None:
        return 'unknown'
    return 'current' if covered==state else 'stale'
