"""Query-local applicability of relation proposals, never approval of graph edges.

The capture queue is mutable. Each read boundary freezes complete proposal
identities and revisions before selecting a bounded independent-review packet.
Omitted proposals retain unknown applicability; routing rank is never a waiver.
"""
from __future__ import annotations
import json
import math

from .answer_source_gaps import MAX_GAPS, digest

# Reserve room for the writer/reviewer/contract-repair family without raising
# its existing request cap. Omitted candidates remain explicitly unassessed.
MAX_BODY_CHARACTERS = 8_000
from .evidence import _EVIDENCE_RELATIONS
from .overview import _model_record


FRESHNESS_PROTOCOL = 'settled-relation-freshness-v2'


def _settled_refresh(connection, job, candidates, proposals):
    """Only the committed LINK result may ignore subsequent refresh bookkeeping.

    Worker._link applies edges and Queue._save_links writes the complete link
    payload plus phase=refresh in one transaction, before any view refresh.
    Queue._claim rearms done jobs as LINK, which deliberately fails this test.
    This predicate grants no semantic approval to any relation or candidate.
    """
    nonempty = lambda value: isinstance(value, str) and bool(value.strip())
    if (not nonempty(job['assertion_id']) or job['event_id'] is not None
            or not nonempty(job['scope']) or job['error'] is not None
            or type(job['waiting_model']) is not int or job['waiting_model'] != 0
            or (job['phase'], job['status']) not in
                {('refresh','pending'), ('refresh','running'), ('done','completed')}
            or not isinstance(candidates, list) or not isinstance(proposals, list)):
        return False
    identities = {job['assertion_id']}
    for candidate in candidates:
        if (not isinstance(candidate, dict) or not nonempty(candidate.get('id'))
                or not nonempty(candidate.get('method'))
                or type(candidate.get('score')) not in (int, float)
                or not math.isfinite(candidate['score'])):
            return False
        identities.add(candidate['id'])
    for proposal in proposals:
        if (not isinstance(proposal, dict) or proposal.get('kind') not in _EVIDENCE_RELATIONS
                or proposal.get('status') not in ('applied','existing','rejected','pending','unresolved')
                or any(not nonempty(proposal.get(key)) for key in
                       ('from_id','to_id','source_quote','target_quote'))
                or proposal.get('verification_required', False) is not False
                or ('condition' in proposal and proposal['condition'] is not None
                    and not nonempty(proposal['condition']))
                or ('reason' in proposal and not nonempty(proposal['reason']))):
            return False
        if proposal['status'] in ('applied','existing') and not nonempty(proposal.get('edge_id')):
            return False
        if proposal['status'] == 'rejected' and not nonempty(proposal.get('reason')):
            return False
        if proposal['status'] in ('pending','unresolved'):
            proof=proposal.get('verification',{})
            if (proof.get('status')!='unresolved' or proof.get('version')!=1
                    or not nonempty(proof.get('reason'))):
                return False
        if ((proposal['kind'] == 'depends_on' or proposal.get('basis') in
                ('reported_decision','self_correction')) and 'verification' not in proposal):
            return False
        if 'verification' in proposal:
            proof = proposal['verification']
            expected = ('unsupported' if proposal['status'] == 'rejected' else
                'unresolved' if proposal['status'] in ('pending','unresolved') else 'accepted')
            if (not isinstance(proof, dict) or proof.get('status') != expected
                    or not nonempty(proof.get('reason'))
                    or type(proof.get('version')) is not int or proof['version'] != 1):
                return False
        identities.update((proposal['from_id'], proposal['to_id']))
    # Missing/cross-scope identities cannot silently inherit settled status.
    rows = connection.execute('SELECT assertion_id,scope,metadata,actor FROM memory_assertions WHERE assertion_id IN '
        '(SELECT value FROM json_each(?))', (json.dumps(sorted(identities)),)).fetchall()
    if {row['assertion_id'] for row in rows if row['scope'] == job['scope']} != identities:
        return False
    from .link_proof import requires_link_verification, reported_decision_proof, self_correction_proof
    records = {row['assertion_id']: {'metadata':json.loads(row['metadata']), 'actor':row['actor']} for row in rows}
    for proposal in proposals:
        source, target = records[proposal['from_id']], records[proposal['to_id']]
        if requires_link_verification(proposal['kind'], source['metadata'], target['metadata']):
            if 'verification' not in proposal:
                return False
        if proposal['status'] in ('applied','existing'):
            proof = proposal.get('verification', {})
            if proposal.get('basis') == 'reported_decision' or proof.get('basis') == 'reported_decision':
                if (proposal.get('basis') != 'reported_decision'
                        or reported_decision_proof(source, target, proof, resolved=False) is None):
                    return False
            if proposal.get('basis') == 'self_correction' or proof.get('basis') == 'self_correction':
                if proposal.get('basis') != 'self_correction' or not self_correction_proof(source, target, proof):
                    return False
    accepted = [row for row in proposals if row['status'] in ('applied','existing')]
    edges = {row['edge_id']: row for row in connection.execute(
        'SELECT edge_id,from_id,to_id,kind,metadata FROM memory_edges WHERE edge_id IN '
        '(SELECT value FROM json_each(?))', (json.dumps([row['edge_id'] for row in accepted]),))}
    for proposal in accepted:
        edge = edges.get(proposal['edge_id'])
        if edge is None or edge['kind'] != proposal['kind']:
            return False
        direct = (edge['from_id'],edge['to_id']) == (proposal['from_id'],proposal['to_id'])
        reverse_conflict = (proposal['kind'] == 'contradicts' and proposal['status'] == 'existing'
            and (edge['to_id'],edge['from_id']) == (proposal['from_id'],proposal['to_id']))
        if (not (direct or reverse_conflict)
                or json.loads(edge['metadata']).get('condition') != proposal.get('condition')):
            return False
    return True


def _freshness_revision(connection, job, proposals):
    candidates = json.loads(job['candidates'])
    payload = {key: job[key] for key in ('id','assertion_id','event_id','scope')}
    payload.update(candidates=candidates, proposals=proposals)
    state = ({'settled_link_result': True} if _settled_refresh(connection, job, candidates, proposals)
        else {key: job[key] for key in ('status','phase','error','waiting_model','updated_at')})
    return digest([FRESHNESS_PROTOCOL, payload, state])


def freeze(connection, cutoff):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_capture_jobs'").fetchone():
        return ()  # Canonical-only engines do not install the optional queue.
    rows=connection.execute('SELECT j.*,coalesce(e.scope,a.scope) AS scope FROM memory_capture_jobs j '
        'LEFT JOIN memory_raw_events e ON e.id=j.event_id '
        'LEFT JOIN memory_assertions a ON a.assertion_id=j.assertion_id '
        'WHERE j.updated_at<=? ORDER BY j.id',(cutoff,)).fetchall()
    output=[]
    for job in rows:
        proposals = json.loads(job['proposals'])
        freshness_revision = _freshness_revision(connection, job, proposals)
        # LINK already acquired and checked every verdict before phase=refresh.
        # A parsed unresolved verdict is terminal uncertainty, not pending I/O.
        link_complete = _settled_refresh(connection,job,json.loads(job['candidates']),proposals)
        for index,proposal in enumerate(proposals):
            if not isinstance(proposal,dict) or proposal.get('kind') not in _EVIDENCE_RELATIONS:
                continue
            revision=digest({'proposal':proposal,'job_id':job['id'],'status':job['status'],
                'phase':job['phase'],'error':job['error'],'waiting_model':job['waiting_model'],
                'updated_at':job['updated_at']})
            terminal=((link_complete or job['status']=='completed') and not job['error'] and not job['waiting_model']
                and all(isinstance(proposal.get(key),str) and proposal[key] for key in ('from_id','to_id')))
            output.append({**proposal,'proposal_id':job['id']+':'+str(index),
                'job_id':job['id'],'proposal_index':index,'scope':job['scope'],'revision':revision,
                'freshness_revision':freshness_revision,
                'job_status':job['status'],'job_phase':job['phase'],'terminal':terminal})
    return tuple(output)


def _known_scope(row):
    return isinstance(row.get('scope'), str) and bool(row['scope'].strip())


def inventory(evidence, scopes, assertions=None):
    eligible=[row for row in evidence if not _known_scope(row) or not scopes or row['scope'] in scopes]
    # Unknown authorization is an opaque operational blocker in every request,
    # including a global request. Its body stays solely in the frozen raw audit.
    unknown=[row for row in eligible if not _known_scope(row)]
    unresolved=[row for row in eligible if _known_scope(row) and row.get('status') in ('pending','unresolved')]
    gaps=[row for row in unresolved if row['terminal']]
    selected=[]; characters=0
    for row in gaps:
        entry=dict(row)
        if assertions is not None:
            endpoints=set((row.get('from_id'),row.get('to_id')))
            # Preserve complete interpreted meaning and source status, while
            # keeping redundant raw proof in the immutable source snapshot.
            entry['endpoints']=[_model_record(assertions[key]) for key in sorted(endpoints,key=str)
                if key in assertions and (not scopes or assertions[key]['scope'] in scopes)]
            entry['endpoint_evidence_complete']={proof['id'] for proof in entry['endpoints']}==endpoints
        size=len(json.dumps(entry,ensure_ascii=False))
        if len(selected)<MAX_GAPS and characters+size<=MAX_BODY_CHARACTERS:
            selected.append(entry); characters+=size
    return {'role':'relation_gap_inventory','gaps':selected,'total':len(gaps),
        'complete':len(selected)==len(gaps),'unassessed_count':len(gaps)-len(selected),
        'operational_count':len(unresolved)-len(gaps)+len(unknown),
        'frontier_sha256':digest(eligible),'inventory_sha256':digest(gaps),
        'freshness_sha256':digest([FRESHNESS_PROTOCOL, [
            [row['proposal_id'], row['scope'], row.get('freshness_revision', digest(row))]
            for row in eligible]])}


def public_inventory(value):
    return {**value,'gaps':[{key:item for key,item in row.items() if key!='endpoints'} for row in value['gaps']]}


REVIEW_RULES=(
 'relation_gap_inventory is a separate independent-review-only inventory of TERMINAL UNAPPROVED relation candidates, '
 'not source extraction gaps, accepted edges or domain facts. It freezes proposal_id, revision, endpoints, '
 'kind, condition, reasons and status for this question. Review EVERY supplied proposal against the whole '
 'question, exact draft and accepted canonical evidence, including simultaneous prerequisites, exceptions '
 'and uncertainty about authority or object identity. A terminal queue job does not make a relation irrelevant. '
 'Never approve an edge, assert a proposal reason as fact, drop a semantic condition or rewrite queue history. '
 'For global review return relation_gap_dispositions exactly once per supplied proposal: '
 '{proposal_id,revision,disposition:material|addressed|nonmaterial|unassessed,reason,source_ids:[]}. '
 'Both addressed and nonmaterial require valid consumed canonical source_ids plus a substantive meaning-based '
 'reason; confidence, top-k absence, or the fact that a candidate is unresolved cannot establish irrelevance. '
 'addressed means the exact answer already preserves all material conditions and uncertainties using those '
 'canonical records, never that this review validates the candidate relation. material/unassessed require '
 'answer_qualified:true and qualification_quote copied from the exact draft, with a reason showing that '
 'the ENTIRE answer conservatively gates the requested conclusion/action. If inventory.complete=false, '
 'omitted potentially material proposals remain UNKNOWN: also return unassessed_relation_gap_qualification '
 '{answer_qualified:true,quote:exact draft qualification,reason}. Otherwise revise/unresolved. '
 'Pending/unread sources, failed extraction/audit, failed required children and mandatory unread conditions '
 'remain blockers independently of any relation disposition. ')


def review(verdict,text,ledger,consumed,frame):
    import hashlib
    from .model import ModelError
    value=ledger.relation_gap_inventory
    if not value or not value['total'] or verdict['status']!='accepted': return
    rows=verdict.get('relation_gap_dispositions')
    if not isinstance(rows,list) or len(rows)!=len(value['gaps']):
        raise ModelError('accepted answer must assess every supplied relation candidate')
    expected={(row['proposal_id'],row['revision']):row for row in value['gaps']}
    seen=set(); material=False; unassessed=not value['complete']
    for row in rows:
        if not isinstance(row,dict): raise ModelError('invalid relation gap disposition')
        key=(row.get('proposal_id'),row.get('revision'))
        if not all(isinstance(item,str) for item in key) or key not in expected or key in seen:
            raise ModelError('relation gap review requires exact frozen proposal identity/revision')
        seen.add(key)
        choice=row.get('disposition')
        if choice not in ('material','addressed','nonmaterial','unassessed'):
            raise ModelError('unsupported relation gap disposition')
        if not isinstance(row.get('reason'),str) or not row['reason'].strip() or not 1<=len(row['reason'])<=2000:
            raise ModelError('relation gap review requires a substantive bounded reason')
        ids=row.get('source_ids',[])
        if (not isinstance(ids,list) or len(ids)>3 or
                any(not isinstance(key,str) or key not in consumed or key not in ledger.visible for key in ids)):
            raise ModelError('relation applicability proof must reference consumed canonical evidence')
        if choice in ('addressed','nonmaterial'):
            if not expected[key]['terminal'] or not expected[key].get('endpoint_evidence_complete') or not ids:
                raise ModelError('relation clearance requires terminal review and valid canonical proof')
            for proof_id in ids:
                current=ledger.visible[proof_id].get('current_source_revalidation')
                if not current or current['source_run_id']==current['last_positive_claim_run_id']: continue
                latest=ledger.source_revision_inputs.get((current['event_id'],current['source_run_id']))
                positive=ledger.source_revision_inputs.get((current['event_id'],current['last_positive_claim_run_id']))
                if latest is None or positive is None or latest!=positive:
                    raise ModelError('relation clearance requires canonical proof for current source input')
        if choice in ('material','unassessed'):
            quote=row.get('qualification_quote')
            if row.get('answer_qualified') is not True or not isinstance(quote,str) or not 1<=len(quote)<=2000 or quote not in text:
                raise ModelError('potentially material relation requires reviewed action qualification')
        material |= choice=='material'; unassessed |= choice=='unassessed'
    qualification=verdict.get('unassessed_relation_gap_qualification')
    if not value['complete']:
        quote=qualification.get('quote') if isinstance(qualification,dict) else None
        if (not isinstance(qualification,dict) or qualification.get('answer_qualified') is not True
                or not isinstance(quote,str) or not 1<=len(quote)<=2000 or quote not in text
                or not isinstance(qualification.get('reason'),str) or not 1<=len(qualification['reason'])<=2000):
            raise ModelError('omitted relation candidates retain unknown applicability requiring action qualification')
    verdict['relation_gap_review']={'inventory':public_inventory(value),'dispositions':rows,
        'unassessed_gap_qualification':qualification,
        'processing_gaps_apply_to_query':True if material else None if unassessed else False,
        'final_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'query_context_sha256':digest(frame)}


def guard_repair(original, repaired):
    from .model import ModelError
    before=original.get('relation_gap_dispositions',[])
    after=repaired.get('relation_gap_dispositions',[])
    if not isinstance(before,list): return
    identity=lambda row:(row.get('proposal_id'),row.get('revision'),row.get('disposition'))
    intents={identity(row) for row in before if isinstance(row,dict) and
        all(isinstance(row.get(key),str) for key in ('proposal_id','revision','disposition'))}
    fixed={identity(row):row for row in after if isinstance(row,dict) and
        all(isinstance(row.get(key),str) for key in ('proposal_id','revision','disposition'))}
    if not intents<=fixed.keys():
        raise ModelError('overview contract repair changed or lost relation gap disposition')
    for row in before:
        if isinstance(row,dict) and row.get('answer_qualified') is False and identity(row) in fixed:
            if fixed[identity(row)].get('answer_qualified') is not False:
                raise ModelError('overview contract repair reversed relation action qualification')
    qualification=original.get('unassessed_relation_gap_qualification')
    if isinstance(qualification,dict) and qualification.get('answer_qualified') is False:
        if (repaired.get('unassessed_relation_gap_qualification') or {}).get('answer_qualified') is not False:
            raise ModelError('overview contract repair reversed omitted relation action qualification')
