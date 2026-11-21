// Standalone internal writer; no queue consumer, HTTP route, provider or
// connector configuration. A fresh, trusted authorizer + independent
// adapters are required. Terminal provisional artifacts cannot be promoted.
import {prepareInitialSourcePrompts} from './source_model_prompt.mjs';
import {prepareCapturedSource} from './source_evidence.mjs';
import {validateModelReply} from './model_contract.mjs';
import {pinCanonicalRawBasis,encodeCanonicalThought} from './semantic_encoding.mjs';

const encoder=new TextEncoder();
const rawCap=64*1024;
const wall = "CAST(strftime('%s','now') AS INTEGER)*1000+CAST(substr(strftime('%f','now'),4,3) AS INTEGER)";
const target = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}(?![\s\S])/;
const eventId = /^event-[0-9a-f]{64}(?![\s\S])/;
const bind=(db,sql,...args)=>db.prepare(sql).bind(...args);
function first(result) {
  if (result?.success===false || !Array.isArray(result?.results)) throw new Error('Source commit storage error.');
  return result.results[0]??null;
}
function bounded(value) {
  if(typeof value!=='string'||value.length>rawCap||!value.isWellFormed()||encoder.encode(value).length>rawCap)
    throw new TypeError('Invalid or oversized source response.');
  return value;
}
async function hash(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))),
    b=>b.toString(16).padStart(2,'0')).join('');
}
function time(clock) {
  const now=clock();
  if(!Number.isSafeInteger(now)||now<0||now>253402300799999) throw new TypeError('Invalid source commit clock.');
  return now;
}
function stamp(now) {
  return new Date(now).toISOString().replace(/\.(\d{3})Z$/,(_match,ms)=>ms!=='000'?'.'+ms+'000+00:00':'+00:00');
}
const live = `FROM memory_jobs j JOIN memory_raw_captures c ON c.event_id=j.id
  WHERE j.id=? AND j.state='running' AND j.kind='source-thoughts' AND j.lease_token=?
    AND j.lease_expires_at>MAX(?,${wall}) AND j.payload_sha256=c.raw_sha256 AND j.payload=c.raw_json
    AND NOT EXISTS (SELECT 1 FROM memory_raw_captures other
      WHERE other.session_id=c.session_id AND other.event_id<>c.event_id)
    AND NOT EXISTS (SELECT 1 FROM memory_job_publications p WHERE p.job_id=j.id)`;
const receiptCols='r.job_id,r.thought_id,r.thought_sha256,r.evidence_sha256';
const report=(status,result)=>result ? {status,event_id:result.job_id,thought_id:result.thought_id,
  version:1,content_hash:result.thought_sha256} : {status};

/**
 * For trusted scheduler only. authorizeSource must explicitly authorize
 * processing the exact source without context. construct and review receive
 * canonical program-generated requests, never caller-provided approval flags.
 * On refusal or lost lease, no thought or job completion is recorded.
 */
export function createLiveSourceCommitter({authorizeSource,construct,review,clock=Date.now}={}) {
  if ([authorizeSource,construct,review,clock].some(fn=>typeof fn!=='function') || construct===review)
    throw new TypeError('Trusted authorizer and distinct source adapters are required.');
  return async function commit(db,{id,leaseToken}={}) {
    if (typeof id!=='string'||!eventId.test(id)||typeof leaseToken!=='string'||!target.test(leaseToken))
      throw new TypeError('Invalid leased event reference.');
    const leaseSHA=await hash(leaseToken);
    const replay=()=>bind(db,`SELECT ${receiptCols}
      FROM memory_live_source_commits r JOIN memory_jobs j ON r.job_id=j.id
      JOIN memory_thought_versions v ON v.thought_id=r.thought_id AND v.version=1
      WHERE r.job_id=? AND r.lease_sha256=? AND j.state='done' AND v.content_hash=r.thought_sha256`,id,leaseSHA);
    const [admission,receipt]=await db.batch([
      bind(db,`SELECT c.raw_json,c.raw_sha256 ${live}`,id,leaseToken,time(clock)),
      replay(),
    ]);
    const previous=first(receipt);
    if(previous) return report('committed',previous);
    const admitted=first(admission);
    if(!admitted) return report('lost');
    const source=bounded(admitted.raw_json);
    const raw=JSON.parse(source);
    if(raw.id!==id||await hash(source)!==admitted.raw_sha256) return report('lost');
    const subject=Object.freeze({id,source,sourceSha256:admitted.raw_sha256});
    if(await authorizeSource(subject)!==true) return report('refused');
    const prepared=prepareCapturedSource({source});
    if(!prepared.eligible) return report('refused');
    let prompts;
    try { prompts=prepareInitialSourcePrompts({source}); }
    catch { return report('unsupported'); }
    const stillLive=async ()=>{
      const [row]=await db.batch([bind(db,`SELECT 1 alive ${live} AND c.raw_json=? AND c.raw_sha256=?`,
        id,leaseToken,time(clock),source,admitted.raw_sha256)]);
      return first(row)?.alive===1;
    };
    if(!await stillLive()) return report('lost');
    const construction=bounded(await construct(prompts.construct));
    let drafts;
    try { drafts=validateModelReply('Construct thought',construction,{supportCount:prepared.fragments.length}); }
    catch { return report('rejected'); }
    if(drafts.length!==1) return report('unsupported');
    if(await authorizeSource(subject)!==true) return report('refused');
    if(!await stillLive()) return report('lost');
    const reviewRequest=prompts.review(construction);
    const checked=bounded(await review(reviewRequest));
    try { validateModelReply('Verify thought',checked); }
    catch { return report('rejected'); }
    if(JSON.parse(checked).verdict!=='accept') return report('rejected');
    if(await authorizeSource(subject)!==true) return report('refused');

    const draft=drafts[0];
    const bases=await Promise.all(draft.support_fragments.map(index=>{
      const fragment=prepared.fragments[index];
      const [sourceId,start,end]=JSON.parse(fragment.id);
      if(sourceId!==id) throw new Error('Prepared source binding diverged.');
      return pinCanonicalRawBasis({event_id:id,span:{start,end},meaning:fragment.text},raw);
    }));
    const reviewedAt=time(clock);
    const thought_id='thought-'+crypto.randomUUID().replaceAll('-','');
    const thought={
      id:thought_id,version:1,text:draft.text,question:draft.question,
      origin:{actor:raw.actor,source:raw.source,session_id:raw.session_id,recorded_at:raw.recorded_at,
        role:raw.metadata.role,timezone:null,source_message_at:null,source_message_clock_basis:null,
        kind:'source_interpretation',event_id:id},
      bases,metadata:{protocol:'unified-thought-v1',verification:'accepted'},created_at:stamp(reviewedAt),
    };
    const {payload,content_hash}=await encodeCanonicalThought(thought);
    // Commit to both exact model responses and their fixed request packets,
    // exact source bytes, and resulting logical version without exposing them.
    const evidenceSHA=await hash(JSON.stringify(['live-single-source/v1',admitted.raw_sha256,content_hash,
      prompts.construct.prompt,construction,reviewRequest.prompt,checked]));
    const committedAt=time(clock);
    const [insertResult,replayResult]=await db.batch([
      bind(db,`INSERT INTO memory_live_source_commits
        (job_id,thought_id,source_sha256,lease_sha256,thought_payload,thought_sha256,evidence_sha256,committed_at)
        SELECT j.id,?,c.raw_sha256,?,?,?,?,? ${live}
        AND c.raw_json=? AND c.raw_sha256=?
        ON CONFLICT(job_id) DO NOTHING RETURNING job_id,thought_id,thought_sha256,evidence_sha256`,
        thought_id,leaseSHA,payload,content_hash,evidenceSHA,committedAt,
        id,leaseToken,committedAt,source,admitted.raw_sha256),
      replay(),
    ]);
    const admittedResult=first(insertResult)??first(replayResult);
    return report(admittedResult?'committed':'lost',admittedResult);
  };
}
