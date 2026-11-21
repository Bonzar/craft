// Exact compact-evidence checks ported from assertion_memory/link_proof.py.
// This verifies anchoring and attribution of a verifier verdict, not whether a
// semantic relation was discovered, reviewed independently, or published.
const modifying=new Set(['exception_to','supersedes','contradicts']);
const object=x=>x!=null&&typeof x==='object'&&!Array.isArray(x);

export function requiresLinkVerification(kind,source,target) {
  return kind==='depends_on'||(modifying.has(kind)&&
    (source?.interpretation?.status==='model_verified'||target?.interpretation?.status==='model_verified'));
}

function anchor(reference,span,resolved) {
  if(!object(reference)) return null;
  const {event_id,start,end}=span, left=reference.start,right=reference.end;
  if(typeof event_id!=='string'||!event_id||reference.event_id!==event_id||
     ![start,end,left,right].every(Number.isSafeInteger)||!(0<=left&&left<=start&&start<end&&end<=right)) return null;
  const body=Object.hasOwn(reference,'text')?reference.text:reference.quote;
  if((body!=null||resolved)&&(typeof body!=='string'||[...body].length!==right-left)) return null;
  const result={event_id,start,end,role:reference.role??null,
    relation:Object.hasOwn(reference,'relation')?reference.relation:'statement'};
  if(!resolved&&(['role','relation'].some(key=>(span[key]??null)!==result[key]))) return null;
  return result;
}

export function selfCorrectionProof(source,target,verdict) {
  const evidence=verdict?.correction_evidence;
  if(verdict?.status!=='accepted'||verdict?.basis!=='self_correction'||
     source.actor!==target.actor||!object(evidence)) return false;
  return [['source',source],['target',target]].every(([side,record])=>{
    const span=evidence[side], meta=record.metadata;
    return object(span)&&['user','assistant','tool'].includes(span.role)&&
      (meta.event_id==null||span.event_id===meta.event_id)&&
      (meta.evidence_refs??[]).some(ref=>anchor(ref,{...span,relation:'statement'},false));
  });
}

function decisionRefs(record,spans,resolved) {
  if(!Array.isArray(spans)||spans.length<1||spans.length>64) return null;
  const refs=record.metadata?.evidence_refs??[], origin=record.metadata?.event_id;
  if(!Array.isArray(refs)||spans.some(span=>!object(span))) return null;
  const accepted=spans.map(span=>refs.map(ref=>anchor(ref,span,resolved)).find(x=>x));
  if(accepted.some(x=>!x)||!accepted.some(x=>x.role==='user'&&x.relation==='statement'&&
       (origin==null||x.event_id===origin))) return null;
  return accepted;
}

export function reportedDecisionProof(source,target,verdict,{resolved=true}={}) {
  const evidence=verdict?.decision_evidence;
  if(verdict?.status!=='accepted'||verdict?.basis!=='reported_decision'||!object(evidence)) return null;
  const source_refs=decisionRefs(source,evidence.source_refs,resolved);
  const target_refs=decisionRefs(target,evidence.target_refs,resolved);
  return source_refs&&target_refs ? {basis:'reported_decision',decision_evidence:{source_refs,target_refs}} : null;
}
