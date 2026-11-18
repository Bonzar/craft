"""Server-owned exact empty base-history proof, never a world absence assertion.

Inventory queries inspect only immutable identifiers, clocks and reference metadata.
They deliberately bypass validity, interpretation, withdrawal and retrieval filters.
"""
from __future__ import annotations

import hashlib
import json

from .storage import _timestamp

RECORDED_HISTORY_PROTOCOL='recorded-history-inventory-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def authorization(engine,spec):
    from .question_routes import dependency_definitions
    definitions=dependency_definitions(engine,spec)
    return definitions,digest({'policy':'scopes-explicit-roots-dependencies-evidence-closure-v1',
                              'scopes':list(spec.scopes),'definitions':definitions})


def inventory(engine,spec,snapshot):
    """Count the whole authorized frozen history, without fetching source bodies.

    Every potentially selectable dependency/index scope is included conservatively;
    explicit roots and accepted branches can navigate across scope boundaries.
    Unknown legacy raw capability fails closed, even when no raw rows are visible.
    """
    absent=snapshot.raw_store_absent
    if (type(absent) is not bool or type(snapshot.assertion_watermark) is not int
            or (absent and snapshot.raw_event_watermark is not None)
            or (not absent and type(snapshot.raw_event_watermark) is not int)):
        return None
    try:
        definitions,auth=authorization(engine,spec)
    except ValueError:
        return None  # Standalone/unknown definitions cannot certify authorization.
    scopes=set(); roots=set(); all_scopes=False
    for row in definitions:
        definition=row['definition']
        scopes.update(definition['scopes']); all_scopes |= not definition['scopes']
        roots.update(definition['root_ids'])
    views={row['view_id'] for row in definitions}
    roots.update(row.assertion_id for row in snapshot.reviews
                 if row.view_id in views and row.decision=='accepted')
    scopes=sorted(scopes); roots=sorted(roots)
    predicate='1' if all_scopes else 'scope IN ('+','.join('?' for _ in scopes)+')'
    scope_args=[] if all_scopes else scopes
    root_predicate='assertion_id IN ('+','.join('?' for _ in roots)+')' if roots else '0'
    from .evidence import _EVIDENCE_RELATIONS
    kinds=sorted(_EVIDENCE_RELATIONS)
    # Include future explicit roots as graph entry points too. This is
    # conservative: they must not hide earlier cross-scope prerequisites/raw refs.
    cte=('WITH RECURSIVE authorized(id) AS ('
         'SELECT assertion_id FROM memory_assertions WHERE rowid<=? AND '
         '(('+predicate+' AND recorded_at<=?) OR '+root_predicate+') '
         'UNION SELECT CASE WHEN e.from_id=u.id THEN e.to_id ELSE e.from_id END '
         'FROM memory_edges e JOIN authorized u ON u.id=e.from_id OR u.id=e.to_id '
         'WHERE e.rowid<=? AND e.recorded_at<=? AND e.kind IN ('+','.join('?' for _ in kinds)+')) ')
    cutoff=snapshot.effective_recorded_as_of
    args=[snapshot.assertion_watermark,*scope_args,cutoff,*roots,
          snapshot.edge_watermark,cutoff,*kinds]
    with engine.store.transaction() as connection:
        canonical=connection.execute(cte+'SELECT count(*) FROM memory_assertions a JOIN authorized u '
            'ON a.assertion_id=u.id WHERE a.rowid<=? AND a.recorded_at<=?',
            (*args,snapshot.assertion_watermark,cutoff)).fetchone()[0]
        raw=0
        if not absent:
            # Metadata references authorize only their exact raw source, never
            # every source sharing a root's otherwise unrelated scope.
            refs=set()
            for row in connection.execute(cte+'SELECT a.metadata FROM memory_assertions a JOIN authorized u '
                    'ON a.assertion_id=u.id WHERE a.rowid<=?',(*args,snapshot.assertion_watermark)):
                metadata=json.loads(row[0])
                if isinstance(metadata.get('event_id'),str): refs.add(metadata['event_id'])
                for ref in metadata.get('evidence_refs',[]):
                    if isinstance(ref,dict) and isinstance(ref.get('event_id'),str): refs.add(ref['event_id'])
            refs=sorted(refs)
            ref_predicate='id IN ('+','.join('?' for _ in refs)+')' if refs else '0'
            raw=connection.execute('SELECT count(*) FROM memory_raw_events WHERE rowid<=? '
                'AND recorded_at<=? AND ('+predicate+' OR '+ref_predicate+')',
                (snapshot.raw_event_watermark,cutoff,*scope_args,*refs)).fetchone()[0]
    return {'authorization_sha256':auth,'raw_store_absent':absent,'raw_known_count':raw,
            'canonical_known_count_before_validity_filter':canonical}


def certificate(engine,spec,snapshot):
    counts=inventory(engine,spec,snapshot)
    if counts is None or counts['raw_known_count'] or counts['canonical_known_count_before_validity_filter']:
        return None
    proof={'kind':'empty_recorded_history','protocol':RECORDED_HISTORY_PROTOCOL,'complete':True,
           'scopes':list(spec.scopes),**counts,
           'requested_recorded_as_of':(_timestamp(snapshot.requested_recorded_as_of)
                                      if snapshot.requested_recorded_as_of is not None else None),
           'effective_recorded_as_of':snapshot.effective_recorded_as_of,
           'assertion_watermark':snapshot.assertion_watermark,'edge_watermark':snapshot.edge_watermark,
           'review_watermark':snapshot.review_watermark,'raw_event_watermark':snapshot.raw_event_watermark,
           'source_revision_watermark':snapshot.source_revision_watermark,
           'source_frontier_sha256':snapshot.source_gaps(spec.scopes)['frontier_sha256'],
           'relation_frontier_sha256':snapshot.relation_gaps(spec.scopes)['frontier_sha256'],
           'relation_processing_frontier':snapshot.relation_processing_frontier,
           'pipeline_key':engine.pipeline_key,'domain_facts':'not_established'}
    proof={**proof,'proof_sha256':digest(proof)}
    return proof if valid(proof) else None


def valid(proof,frame=None):
    """Check server proof structure/binding; this is not a model-issued credential."""
    if not isinstance(proof,dict): return False
    required={'kind','protocol','complete','scopes','authorization_sha256','raw_store_absent',
        'raw_known_count','canonical_known_count_before_validity_filter','requested_recorded_as_of',
        'effective_recorded_as_of','assertion_watermark','edge_watermark','review_watermark',
        'raw_event_watermark','source_revision_watermark','source_frontier_sha256',
        'relation_frontier_sha256','relation_processing_frontier','pipeline_key','domain_facts','proof_sha256'}
    if set(proof)!=required: return False
    if (proof['kind']!='empty_recorded_history' or proof['protocol']!=RECORDED_HISTORY_PROTOCOL
            or proof['complete'] is not True or proof['domain_facts']!='not_established'
            or any(type(proof[key]) is not int or proof[key]!=0
                   for key in ('raw_known_count','canonical_known_count_before_validity_filter'))
            or type(proof['raw_store_absent']) is not bool): return False
    if (any(type(proof[key]) is not int or proof[key]<0 for key in
            ('assertion_watermark','edge_watermark','review_watermark'))
            or (proof['raw_store_absent'] and proof['raw_event_watermark'] is not None)
            or (not proof['raw_store_absent'] and (type(proof['raw_event_watermark']) is not int
                                                  or proof['raw_event_watermark']<0))): return False
    if (not isinstance(proof['scopes'],list)
            or any(not isinstance(scope,str) or not scope.strip() for scope in proof['scopes'])
            or len(proof['scopes'])!=len(set(proof['scopes']))): return False
    revision=proof['source_revision_watermark']
    if revision is not None and (type(revision) is not int or revision<0): return False
    if revision is None and not proof['raw_store_absent']: return False
    if any(not isinstance(proof[key],str) or len(proof[key])!=64
           or any(char not in '0123456789abcdef' for char in proof[key]) for key in (
            'authorization_sha256','source_frontier_sha256','relation_frontier_sha256',
            'relation_processing_frontier','pipeline_key')): return False
    if proof['proof_sha256']!=digest({k:v for k,v in proof.items() if k!='proof_sha256'}): return False
    try:
        effective=_timestamp(proof['effective_recorded_as_of'])
        if effective!=proof['effective_recorded_as_of']: return False
        requested=proof['requested_recorded_as_of']
        if requested is not None and (_timestamp(requested)!=requested or effective!=requested): return False
        if frame is not None:
            requested=frame['cutoffs'].get('recorded_as_of')
            requested=_timestamp(requested) if requested is not None else None
            if (frame.get('scopes')!=proof['scopes'] or frame.get('knowledge_at_cutoff')!=proof
                    or requested!=proof['requested_recorded_as_of']): return False
    except (KeyError,TypeError,ValueError): return False
    return True


def from_audit(engine,spec,review,descriptor):
    """Validate the pinned server frame without recapturing time or raw bodies."""
    frame=review.get('query_context',{})
    proof=frame.get('knowledge_at_cutoff')
    if not valid(proof,frame) or proof['pipeline_key']!=engine.pipeline_key: return None
    if frame.get('question')!=spec.query or proof['authorization_sha256']!=authorization(engine,spec)[1]: return None
    keys=('assertion_watermark','edge_watermark','review_watermark','raw_event_watermark',
          'source_revision_watermark','effective_recorded_as_of','raw_store_absent','relation_processing_frontier')
    if any(key not in descriptor or proof[key]!=descriptor[key] for key in keys): return None
    requested=descriptor.get('requested_recorded_as_of')
    if proof['requested_recorded_as_of']!=(_timestamp(requested) if requested is not None else None): return None
    return proof


def metadata(proof,*,notice=False):
    result={'knowledge_at_cutoff':proof}
    if notice:
        result['notice']={'purpose':'knowledge_limit_notice','notice_payload_complete':True,
            'proof_sha256':proof['proof_sha256'],
            'text':'At the requested knowledge cutoff, this base had no available source records or canonical '
                   'assertions in the authorized scope. The requested facts cannot be established from that snapshot.'}
    return result


KNOWLEDGE_LIMIT_RULES=(
 'Narrow metadata exception: only a server answer_metadata.knowledge_at_cutoff certificate with '
 'kind=empty_recorded_history and complete=true permits a database-knowledge limitation in answer prose: '
 'this base had no authorized source records or canonical assertions by that recorded cutoff, so the '
 'requested facts remain unestablished. No absent source citation is needed for that machine-attested '
 'availability statement. This does NOT establish that a person never reported something, that an event '
 'never happened, any real-world date, absence outside this base/scope/cutoff, or permission to act. '
 'Review the unchanged exact user question and all positive claims/prerequisites independently. '
 'A truthful limited inability to establish asked facts may be accepted without establishing those facts; '
 'enumerate every material unknown using basis=empty_eligible_corpus for this certificate only. '
 'The server code validates it; empty top-k/selected/validity-filtered records are not this proof. '
 'Do not request repetitive unavailable searches merely to re-prove this certified empty base history. '
 'Other material obligations still apply. All other prompt, cache, version and envelope commentary remains banned. ')
