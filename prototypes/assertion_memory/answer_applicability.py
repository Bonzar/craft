"""Exact-request certificates for immutable answers; never domain publications."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from .overview_review import PROTOCOL as REVIEW_PROTOCOL, _digest, _hash_text, review_fixed_answer
from .question_routes import _request_spec, answer_reusable, dependency_definitions, routing_review
from .model import ModelError, SemanticUnavailable

PROTOCOL='answer-applicability-v1'
KIND='answer_applicability'


def route_identity(engine,request_spec,route):
    with engine.store.transaction() as db:
        row=db.execute('SELECT revision,view_id,plan FROM memory_question_route_versions '
            'WHERE request_id=? ORDER BY revision DESC LIMIT 1',(request_spec.id,)).fetchone()
    if not row or row['view_id']!=route['view_id'] or _digest(json.loads(row['plan']))!=_digest(route['plan']):
        raise ModelError('applicability route changed before review')
    return {'request_id':request_spec.id,'route_revision':row['revision'],'route_sha256':_digest(route)}


def _domain_bound(engine,version,check):
    review=check.get('overview_review') or {}; frame=review.get('query_context',{})
    spec=engine._spec(version.view_id)
    return bool(check['version_id']==version.id and check['view_id']==version.view_id
        and check['pipeline_key']==engine.pipeline_key and version.pipeline_key==engine.pipeline_key
        and review.get('status')=='accepted' and review.get('protocol')==REVIEW_PROTOCOL
        and not review.get('certificate') and not routing_review(review) and not routing_review(version.overview_review)
        and review.get('final_text_sha256')==_hash_text(version.summary)
        and review.get('query_context_sha256')==_digest(frame)
        and frame.get('question')==spec.query and frame.get('scopes')==list(spec.scopes)
        and frame.get('cutoffs')=={'valid_as_of':version.valid_as_of,'recorded_as_of':version.recorded_as_of})


def candidate(engine,route,request,snapshot):
    """Side-effect-free candidate load: no historical old-question build."""
    from .semantic_reuse import _prior
    from .evidence import _expand_evidence
    spec=engine._spec(route['view_id'])
    if spec.scopes!=tuple(request['scopes']) or not answer_reusable(engine,spec.id,request=request): return None
    prior=_prior(engine,spec,request['valid_as_of'],request['recorded_as_of'])
    if not prior: return None
    version,check=prior
    # A mechanical original-question invocation appends a normal receipt but
    # supplies no new independent grounding. Pin the first still-valid proof
    # for these exact immutable bytes, rather than its invocation clock.
    from .engine import _load_check
    with engine.store.transaction() as db:
        row=db.execute("SELECT * FROM memory_view_checks WHERE view_id=? AND version_id=? "
            "AND coalesce(json_extract(overview_review,'$.certificate.kind'),'') != 'answer_applicability' "
            "AND json_extract(overview_review,'$.status')='accepted' "
            'AND pipeline_key=? AND valid_cutoff=? AND recorded_cutoff=? ORDER BY id LIMIT 1',
            (spec.id,version.id,engine.pipeline_key,request['valid_as_of'] or '',request['recorded_as_of'] or '')).fetchone()
    check=_load_check(row) if row else None
    if version.missing_roots or check is None or not _domain_bound(engine,version,check): return None
    if version.valid_as_of!=request['valid_as_of'] or version.recorded_as_of!=request['recorded_as_of']: return None
    if set(spec.root_ids)-snapshot.assertions.keys(): return None
    ids={row['id'] for row in version.evidence}
    for row in version.evidence:
        original=snapshot.assertions.get(row['id'])
        if original is None or {key:row.get(key) for key in original}!=original: return None
    current_edges={row['id']:row for row in snapshot.edges}
    if any(current_edges.get(row['id'])!=row for row in version.edges): return None
    expanded,required=_expand_evidence(ids,snapshot.assertions,snapshot.edges)
    if {row['id'] for row in expanded}!=ids or not {row['id'] for row in required}<={row['id'] for row in version.edges}: return None
    # Required semantic children retain independently current domain proofs.
    if set(spec.depends_on)!=set(version.upstream): return None
    for child_id,version_id in version.upstream.items():
        child=engine.get_version(version_id); childcheck=engine.latest_check(child)
        if (not childcheck or not _domain_bound(engine,child,childcheck) or child.missing_roots
                or child.valid_as_of!=version.valid_as_of or child.recorded_as_of!=version.recorded_as_of
                or childcheck.get('freshness',{}).get('covered_corpus_revision')!=snapshot.corpus(engine,engine._spec(child_id))): return None
    return version,check



def frontier(engine,spec,snapshot):
    """Existing conservative canonical closure with authorized source frontiers.

    Global source revision epochs alone do not change this exact request's
    eligible proof. Keep relevant revision histories and scoped zero-claim
    outcomes, including inventories omitted from the bounded review packet.
    """
    ids,links,reviews=snapshot._corpus_parts(engine,spec)
    events={row['event_id'] for row in snapshot.source_processing_evidence
        if not spec.scopes or row['scope'] in spec.scopes}
    revisions=[row for row in snapshot.source_revision_evidence if row[0] in events or
        ids.intersection(row[3].get('assertion_ids',())) or
        any(item.get('assertion_id') in ids for item in row[3].get('reconciliation',()))]
    return _digest([[snapshot.assertions[key] for key in sorted(ids)],links,[asdict(row) for row in reviews],
        dependency_definitions(engine,spec),revisions,snapshot.source_gaps(spec.scopes)['frontier_sha256'],
        snapshot.relation_gaps(spec.scopes)['freshness_sha256']])


def _freshness(engine,spec,snapshot):
    observed=engine.capture_snapshot(valid_as_of=snapshot.requested_valid_as_of,
        recorded_as_of=snapshot.requested_recorded_as_of)
    covered=frontier(engine,spec,snapshot); latest=frontier(engine,spec,observed)
    changed=covered!=latest
    return {'status':'new_eligible_evidence_unchecked' if changed else 'unchanged_at_check',
        'refresh_required':changed,'newer_eligible_evidence_unchecked':changed,
        'latest_observed_version':observed.source_version,'checked_at':observed.captured_at,
        'covered_corpus_revision':covered,'observed_corpus_revision':latest,
        'observed_effective_valid_as_of':observed.effective_valid_as_of,
        'observed_effective_recorded_as_of':observed.effective_recorded_as_of}

def _key(engine,spec,request,identity,version,base,snapshot=None,*,authorized_frontier=None):
    return PROTOCOL+':'+_digest([PROTOCOL,REVIEW_PROTOCOL,engine.pipeline_key,request,identity,
        version.view_id,version.id,_hash_text(version.summary),_digest(version.evidence),_digest(version.edges),
        base['id'],_digest(base),authorized_frontier if authorized_frontier is not None else frontier(engine,spec,snapshot),dependency_definitions(engine,spec)])


def _invocation(engine,check,spec,snapshot):
    # The saved immutable review is reusable; this invocation still checks races.
    return {**check,'invocation_query_snapshot':snapshot.descriptor(),'freshness':_freshness(engine,spec,snapshot)}


def attempt(engine,route,snapshot):
    """Return (same version, exact receipt), or None for original-question fallback."""
    from .engine import _load_check, _json
    request=route['plan']['request']
    request_spec,_=_request_spec(request['question'],tuple(request['scopes']),request['valid_as_of'],request['recorded_as_of'])
    selected=candidate(engine,route,request,snapshot)
    if selected is None or engine.model is None or not engine.verify_overviews: return None
    version,base=selected; spec=engine._spec(version.view_id)
    identity=route_identity(engine,request_spec,route)
    key=_key(engine,spec,request,identity,version,base,snapshot)
    with engine.store.transaction() as db:
        row=db.execute('SELECT * FROM memory_view_checks WHERE view_id=? AND input_fingerprint=? '
            "AND json_extract(overview_review,'$.certificate.kind')=? ORDER BY id LIMIT 1",(spec.id,key,KIND)).fetchone()
    if row:
        check=_load_check(row)
        if (check.get('overview_review') or {}).get('source_omissions'):
            failure=_saved_operational_failure(check['overview_review'])
            if failure is not None:_raise_operational_omission(engine,route,failure,check['id'])
            raise _omission_rejection(check['id'])
        if bound(engine,check,version,request['question']): return version,_invocation(engine,check,spec,snapshot)
        return None
    # Supplied spec stays ephemeral: corpus machinery always uses stored owner.
    failure=None
    try:
        audit=review_fixed_answer(engine,request_spec,version,snapshot)
    except (ModelError,ValueError,OSError,sqlite3.Error) as error:
        audit=getattr(error,'source_omission_review',None)
        if audit is None:raise
        if not isinstance(error,SemanticUnavailable):failure=error
    audit['certificate']={**identity,'kind':KIND,'protocol':PROTOCOL,
        'candidate_view_id':version.view_id,'candidate_version_id':version.id,
        'candidate_summary_sha256':_hash_text(version.summary),'candidate_proof_sha256':_digest([version.evidence,version.edges]),
        'authorized_frontier_sha256':frontier(engine,spec,snapshot),'frozen_snapshot_sha256':_digest(snapshot.descriptor()),
        'original_question':spec.query,'base_domain_check_id':base['id'],'base_domain_check_sha256':_digest(base),'input_key':key}
    from .query_snapshot import receipt
    with engine.store.transaction(write=True) as db:
        from .publication_fences import check_guard
        check_guard(engine,db)
        if route_identity(engine,request_spec,route)!=identity or engine.get_version(version.id)!=version:
            raise ModelError('applicability immutable route or candidate changed')
        fresh=_freshness(engine,spec,snapshot)
        winner=db.execute('SELECT id FROM memory_view_checks WHERE view_id=? AND input_fingerprint=? '
            "AND json_extract(overview_review,'$.certificate.kind')=? ORDER BY id LIMIT 1",(spec.id,key,KIND)).fetchone()
        if winner:
            check_id=winner[0]
            audit=json.loads(db.execute('SELECT overview_review FROM memory_view_checks WHERE id=?',(check_id,)).fetchone()[0])
        else:
            check_id=db.execute('INSERT INTO memory_view_checks '
                '(view_id,version_id,source_version,coverage_version,valid_cutoff,recorded_cutoff,pipeline_key,'
                'input_fingerprint,checked_at,overview_review,query_snapshot,freshness) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (spec.id,version.id,snapshot.source_version,snapshot.coverage(engine,spec),request['valid_as_of'] or '',
                 request['recorded_as_of'] or '',engine.pipeline_key,key,fresh['checked_at'],_json(audit),
                 _json(snapshot.descriptor()),_json(fresh))).lastrowid
        from .source_omission import persist as persist_omissions
        if not winner:persist_omissions(engine,db,spec,snapshot,audit,check_id)
        check=receipt(engine,check_id)
    if audit.get('source_omissions'):
        failure=failure or _saved_operational_failure(audit)
        if failure is not None:_raise_operational_omission(engine,route,failure,check_id)
        raise _omission_rejection(check_id)
    return version,_invocation(engine,check,spec,snapshot)


def bound(engine,check,version,question):
    """Purpose-specific integrity branch; ordinary grounding never calls this."""
    from .query_snapshot import receipt
    try:
        audit=check.get('overview_review') or {}; cert=audit.get('certificate') or {}; frame=audit.get('query_context') or {}
        if (cert.get('kind')!=KIND or cert.get('protocol')!=PROTOCOL or audit.get('protocol')!=REVIEW_PROTOCOL
                or audit.get('status')!='accepted' or audit.get('assembly_coverage')!='fixed_answer_question_applicability'
                or cert.get('candidate_view_id')!=version.view_id or cert.get('candidate_version_id')!=version.id
                or check.get('view_id')!=version.view_id or check.get('version_id')!=version.id
                or check.get('pipeline_key')!=engine.pipeline_key or engine.get_version(version.id)!=version
                or cert.get('candidate_summary_sha256')!=_hash_text(version.summary)
                or cert.get('candidate_proof_sha256')!=_digest([version.evidence,version.edges])
                or audit.get('final_text_sha256')!=_hash_text(version.summary)
                or audit.get('query_context_sha256')!=_digest(frame)
                or (question is not None and frame.get('question')!=question)): return False
        saved=receipt(engine,check['id'])
        if saved['overview_review']!=audit or saved['input_fingerprint']!=cert.get('input_key'): return False
        spec=engine._spec(version.view_id)
        if frame.get('scopes')!=list(spec.scopes): return False
        request={'question':frame['question'],'scopes':frame['scopes'],**frame['cutoffs']}
        if frame['cutoffs']!={'valid_as_of':version.valid_as_of,'recorded_as_of':version.recorded_as_of}: return False
        request_spec,_=_request_spec(request['question'],tuple(request['scopes']),request['valid_as_of'],request['recorded_as_of'])
        with engine.store.transaction() as db:
            row=db.execute('SELECT view_id,plan FROM memory_question_route_versions WHERE request_id=? AND revision=?',
                (request_spec.id,cert.get('route_revision'))).fetchone()
        if not row: return False
        route={'view_id':row['view_id'],'plan':json.loads(row['plan'])}
        if route['plan'].get('request')!=request or route['view_id']!=version.view_id or route['plan'].get('reuse_view_id')!=version.view_id: return False
        identity={'request_id':request_spec.id,'route_revision':cert['route_revision'],'route_sha256':_digest(route)}
        if any(cert.get(k)!=v for k,v in identity.items()): return False
        base=receipt(engine,cert['base_domain_check_id'])
        if not _domain_bound(engine,version,base) or _digest(base)!=cert['base_domain_check_sha256']: return False
        if cert.get('frozen_snapshot_sha256')!=_digest(saved['query_snapshot']): return False
        # The accepted immutable receipt pins its reviewed authorized frontier.
        # Rehydrating global mutable proposal history here would invalidate this
        # proof merely because a different scope's proposal was revised later.
        covered=cert.get('authorized_frontier_sha256')
        if not isinstance(covered,str) or len(covered)!=64: return False
        return cert['input_key']==_key(engine,spec,request,identity,version,base,authorized_frontier=covered)
    except (ValueError,KeyError,TypeError):
        return False


def _omission_rejection(check_id):
    error=SemanticUnavailable('A grounded source omission requires actual-question materialization before recovery.')
    error.source_omission_check_id=check_id
    return error


def _saved_operational_failure(audit):
    kind=audit.get('failure_type')
    if audit.get('failure_classification')=='semantic' or not kind or kind=='SemanticUnavailable':return None
    classes=(ModelError,ValueError,OSError,TimeoutError,PermissionError,FileNotFoundError,
        sqlite3.Error,sqlite3.DatabaseError,sqlite3.OperationalError,sqlite3.InterfaceError,
        sqlite3.IntegrityError,sqlite3.ProgrammingError,sqlite3.DataError,sqlite3.NotSupportedError)
    constructor={value.__name__:value for value in classes}.get(kind,ModelError)
    error=constructor(audit.get('generation_error','Saved operational applicability failure.'))
    error.source_omission_failure_type=kind
    return error


def _raise_operational_omission(engine,route,error,check_id):
    """Bind durable repair intent without authorizing another foreground model."""
    from .question_routes import materialize_after_reuse_rejection
    error.source_omission_check_id=check_id
    try:
        materialize_after_reuse_rejection(engine,route,error)
    except (ModelError,ValueError,OSError,sqlite3.Error) as persistence_error:
        error.source_omission_persistence_error=str(persistence_error)
        raise error from persistence_error
    raise error
