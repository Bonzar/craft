// Fixed synthetic demonstration, dedicated marked D1 only. No routes or network.
import {normalizeCaptureMessage,prepareCaptureReceipt} from './capture_message.mjs';
import {admitRawCapture} from './raw_capture.mjs';
import {createLiveSourceCommitter} from './live_source_commit.mjs';
import {prepareInitialSourcePrompts} from './source_model_prompt.mjs';
import {encodeCanonicalThought,pinCanonicalRawBasis} from './semantic_encoding.mjs';

const purpose='private-synthetic-assertion-smoke/v1';
const kind='synthetic-permanent-smoke';
const scope='synthetic-assertion-permanent-v2';
const wall="CAST(strftime('%s','now') AS INTEGER)*1000+CAST(substr(strftime('%f','now'),4,3) AS INTEGER)";
const common={message_id:'fixture-1',actor_id:'user',role:'user',position:0,scope};
const fixtures={
  accepted:{...common,session_id:scope+'-accepted',source:'fixture://permanent/v2/accepted',
    text:'The imaginary greenhouse has a blue door.'},
  rejected:{...common,session_id:scope+'-rejected',source:'fixture://permanent/v2/rejected',
    text:'The imaginary shed has a wooden roof.'},
};
const replies={
  accepted:{draft:'The imaginary greenhouse has a blue door.',review:'The draft follows the fictional source.'},
  rejected:{draft:'The imaginary shed has a metal roof.',review:'The fictional source says wood, not metal.'},
};
const encode=new TextEncoder();
const hash=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode(value))),
  b=>b.toString(16).padStart(2,'0')).join('');
const hex=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const bind=(db,sql,...args)=>db.prepare(sql).bind(...args);
const get=result=>{
  if(result?.success===false||!Array.isArray(result?.results)) throw new Error('Synthetic proof storage error.');
  return result.results;
};
const match=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
const status=(phase,accepted=null,rejected=null)=>({
  ok:phase==='verified',kind,phase,provider_called:false,live_graph_changed:false,accepted,rejected,
});

async function marker(db) {
  try {
    const [row]=await db.batch([bind(db,'SELECT purpose FROM memory_synthetic_smoke_environment WHERE singleton=1 AND purpose=?',purpose)]);
    return get(row)[0]?.purpose===purpose;
  } catch { return false; }
}
async function expected(label) {
  const fixture=fixtures[label];
  const normalized=await normalizeCaptureMessage(fixture);
  const {event_id,receipt,raw_metadata}=await prepareCaptureReceipt(normalized);
  const construct=JSON.stringify({thoughts:[{text:replies[label].draft,question:null,support_fragments:[0]}],needs_detail:null});
  const verify=JSON.stringify({verdict:label==='accepted'?'accept':'reject',text:replies[label].review});
  return {fixture,event_id,receipt,raw_metadata,construct,verify};
}
function fixedSource(row,ref) {
  if(!row||row.receipt!==ref.receipt||row.kind!=='source-thoughts'||
    row.payload!==row.raw_json||row.payload_sha256!==row.raw_sha256||!hex(row.raw_sha256)) return null;
  const raw=JSON.parse(row.raw_json);
  const {fixture:f}=ref;
  if(!match(raw,{id:ref.event_id,session_id:f.session_id,turn_id:f.message_id,prompt:f.text,
    actor:f.actor_id,source:f.source,scope:f.scope,recorded_at:raw.recorded_at,metadata:ref.raw_metadata}) ||
    typeof raw.recorded_at!=='string') return null;
  return raw;
}
async function rejectionProof(source,sha,ref) {
  const prompts=prepareInitialSourcePrompts({source});
  return JSON.stringify({kind:'scripted-synthetic-rejection/v2',evidence_sha256:await hash(JSON.stringify(
    [scope,sha,prompts.construct.prompt,ref.construct,prompts.review(ref.construct).prompt,ref.verify]))});
}
const joined=`SELECT c.receipt,c.raw_json,c.raw_sha256,j.kind,j.payload,j.payload_sha256,j.state,j.attempts,
  j.checkpoint,j.failure_code,r.thought_id,r.source_sha256,r.thought_payload,r.thought_sha256,r.evidence_sha256,
  r.lease_sha256,t.current_version,v.payload AS version_payload,v.content_hash AS version_sha256,
  (SELECT count(*) FROM memory_thought_versions z WHERE z.thought_id=r.thought_id) version_count,
  (SELECT count(*) FROM memory_thought_version_bases b WHERE b.basis_event_id=c.event_id) basis_event_count,
  (SELECT count(*) FROM memory_thought_versions z WHERE json_extract(z.payload,'$.origin.event_id')=c.event_id) origin_count,
  (SELECT count(*) FROM memory_job_publications p WHERE p.job_id=c.event_id) staged_count
  FROM memory_raw_captures c JOIN memory_jobs j ON j.id=c.event_id
  LEFT JOIN memory_live_source_commits r ON r.job_id=c.event_id
  LEFT JOIN memory_thoughts t ON t.id=r.thought_id
  LEFT JOIN memory_thought_versions v ON v.thought_id=r.thought_id AND v.version=1
  WHERE c.event_id=?`;
async function basisStatement(db,event) {
  return bind(db,`SELECT b.thought_id,b.version,b.basis_index,b.basis_event_id,b.basis_thought_id,b.basis_thought_version,
    b.span_start,b.span_end,b.content_hash
    FROM memory_thought_version_bases b
    WHERE b.basis_event_id=? OR b.thought_id=(SELECT thought_id FROM memory_live_source_commits WHERE job_id=?)
    LIMIT 2`,event,event);
}
async function verifyRows(db,refs) {
  const basis=await basisStatement(db,refs[0].event_id);
  const [ar,rr,br]=await db.batch([...refs.map(ref=>bind(db,joined,ref.event_id)),basis]);
  const rows=[get(ar)[0],get(rr)[0]];
  const bases=get(br);
  const checks=await Promise.all(rows.map(async(row,index)=>{
    if(!row) return null;
    const ref=refs[index], raw=fixedSource(row,ref);
    if(!raw||await hash(row.raw_json)!==row.raw_sha256||
      row.attempts!==1||row.staged_count!==0) return null;
    if(index===1){
      if(row.state!=='failed'||row.failure_code!=='non_retryable'||row.thought_id!==null||
        row.basis_event_count!==0||row.origin_count!==0||row.checkpoint!==await rejectionProof(row.raw_json,row.raw_sha256,ref)) return null;
      return {outcome:'rejected',event_id:ref.event_id,permanent:false};
    }
    if(row.state!=='done'||row.failure_code!==null||row.current_version!==1||row.version_count!==1||
      row.basis_event_count!==1||row.origin_count!==1||bases.length!==1||row.source_sha256!==row.raw_sha256||
      !hex(row.evidence_sha256)||!hex(row.lease_sha256)||row.thought_payload!==row.version_payload||
      row.thought_sha256!==row.version_sha256||row.checkpoint!==null) return null;
    const thought=JSON.parse(row.version_payload);
    const end=[...raw.prompt].length;
    const pinned=await pinCanonicalRawBasis({event_id:raw.id,span:{start:0,end},meaning:raw.prompt},raw);
    const b=bases[0];
    if(b.thought_id!==row.thought_id||b.version!==1||b.basis_index!==0||b.basis_event_id!==raw.id||
      b.basis_thought_id!==null||b.basis_thought_version!==null||b.span_start!==0||b.span_end!==end||b.content_hash!==pinned.content_hash)
      return null;
    const rebuilt=await encodeCanonicalThought({
      id:row.thought_id,version:1,text:replies.accepted.draft,question:null,
      origin:{actor:raw.actor,source:raw.source,session_id:raw.session_id,recorded_at:raw.recorded_at,role:'user',
        timezone:null,source_message_at:null,source_message_clock_basis:null,kind:'source_interpretation',event_id:raw.id},
      bases:[pinned],metadata:{protocol:'unified-thought-v1',verification:'accepted'},created_at:thought.created_at,
    });
    if(rebuilt.payload!==row.version_payload||rebuilt.content_hash!==row.version_sha256) return null;
    const packet=prepareInitialSourcePrompts({source:row.raw_json});
    const evidence=await hash(JSON.stringify(['live-single-source/v1',row.raw_sha256,rebuilt.content_hash,
      packet.construct.prompt,ref.construct,packet.review(ref.construct).prompt,ref.verify]));
    if(evidence!==row.evidence_sha256) return null;
    return {outcome:'committed',permanent:true,event_id:ref.event_id,thought_id:row.thought_id,version:1,
      content_hash:row.thought_sha256,scope,text:replies.accepted.draft};
  }));
  return status(checks.every(Boolean)?'verified':'pending',...checks);
}

/** Independent, read-only fresh proof. Never triggers a claim or a provider. */
export async function getSyntheticPermanentSmoke(db) {
  if(!await marker(db)) return status('unavailable');
  return verifyRows(db,await Promise.all(['accepted','rejected'].map(expected)));
}

/** Advance fresh fixed fixtures only. An interrupted/occupied lease is never stolen. */
export async function runSyntheticPermanentSmoke(db) {
  if(!await marker(db)) return status('unavailable');
  const labels=['accepted','rejected'];
  const refs=await Promise.all(labels.map(expected));
  const baseline=await verifyRows(db,refs);
  if(baseline.ok) return baseline;
  for(const [index,ref] of refs.entries()){
    if(baseline[labels[index]]) continue;
    const now=Date.now();
    await admitRawCapture(db,ref.fixture,{now});
    const leaseToken=crypto.randomUUID();
    const [claimed]=await db.batch([bind(db,`UPDATE memory_jobs SET state='running',attempts=1,
      lease_token=?,lease_expires_at=?,updated_at=?
      WHERE id=? AND kind='source-thoughts' AND state='ready' AND attempts=0 AND available_at<=?
      AND EXISTS (SELECT 1 FROM memory_raw_captures c
       WHERE c.event_id=memory_jobs.id AND c.receipt=? AND c.raw_json=memory_jobs.payload AND c.raw_sha256=memory_jobs.payload_sha256)
      RETURNING payload,payload_sha256`,leaseToken,now+60_000,now,ref.event_id,now,ref.receipt)]);
    const job=get(claimed)[0];
    if(!job) continue;
    const source=fixedSource({...job,kind:'source-thoughts',receipt:ref.receipt,raw_json:job.payload,raw_sha256:job.payload_sha256},ref);
    if(!source||await hash(job.payload)!==job.payload_sha256) throw new Error('Synthetic source binding diverged.');
    const packet=prepareInitialSourcePrompts({source:job.payload});
    let constructed=false,reviewed=false;
    const commit=createLiveSourceCommitter({
      authorizeSource:subject=>subject.id===ref.event_id&&subject.source===job.payload&&subject.sourceSha256===job.payload_sha256,
      construct:request=>{
        if(!match(request,packet.construct)) throw new Error('Synthetic construction prompt diverged.');
        constructed=true;
        return ref.construct;
      },
      review:request=>{
        if(!constructed||!match(request,packet.review(ref.construct))) throw new Error('Synthetic review prompt diverged.');
        reviewed=true;
        return ref.verify;
      },
    });
    const result=await commit(db,{id:ref.event_id,leaseToken});
    if(!constructed||!reviewed||result.status!==(index===0?'committed':'rejected'))
      throw new Error('Synthetic processing did not reach the expected decision.');
    if(index===1){
      const proof=await rejectionProof(job.payload,job.payload_sha256,ref);
      const finishedAt=Date.now();
      const [finished]=await db.batch([bind(db,`UPDATE memory_jobs SET state='failed',checkpoint=?,
        failure_code='non_retryable',lease_token=NULL,lease_expires_at=NULL,updated_at=?
        WHERE id=? AND state='running' AND lease_token=? AND lease_expires_at>MAX(?,${wall})
          AND payload=? AND payload_sha256=? AND NOT EXISTS
           (SELECT 1 FROM memory_live_source_commits r WHERE r.job_id=memory_jobs.id)
          AND NOT EXISTS (SELECT 1 FROM memory_job_publications p WHERE p.job_id=memory_jobs.id)
        RETURNING id`,proof,finishedAt,ref.event_id,leaseToken,finishedAt,job.payload,job.payload_sha256)]);
      if(get(finished)[0]?.id!==ref.event_id) throw new Error('Synthetic rejection lease was unavailable.');
    }
  }
  return verifyRows(db,refs);
}
