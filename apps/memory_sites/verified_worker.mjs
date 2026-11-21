// Standalone one-claim runner. No scheduling, model implementation, graph
// mutation, API, or partial-source repair. Adapters are installed by trusted
// server code; never accept them, their receipt, or fragments from request flags.
import {validateModelReply, ModelContractError} from './model_contract.mjs';
import {createPublisher, VerificationRejected} from './publication.mjs';
import {renewJobLease, failJob, finishJob} from './queue.mjs';

const text = new TextEncoder();
const maxBytes = 64 * 1024;
const maxFragments = 128;
const legacyPolicy = 'source-batch/v1';
const canonicalPolicy = 'source-single-initial/v1';
const responseBounded = (value) => {
  if (typeof value !== 'string' || !value.isWellFormed() ||
      value.length > maxBytes || text.encode(value).byteLength > maxBytes)
    throw new ModelContractError('Invalid or oversized model response.');
  return value;
};
async function hash(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', text.encode(value))),
    b=>b.toString(16).padStart(2,'0')).join('');
}

class LeaseLost extends Error {}
class RejectedInput extends Error {}

function freezeFragments(rows) {
  if (!Array.isArray(rows) || !rows.length || rows.length > maxFragments) throw new RejectedInput();
  const keys = new Set();
  let size = 0;
  return Object.freeze(rows.map(row => {
    if (!row || typeof row.id !== 'string' || !row.id.trim() || row.id.length > 512 ||
        !row.id.isWellFormed() || row.id.includes('\0') || keys.has(row.id) ||
        typeof row.text !== 'string' || !row.text.isWellFormed() || !row.text.trim() ||
        row.text.length > maxBytes) throw new RejectedInput();
    size += text.encode(row.text).byteLength + text.encode(row.id).byteLength;
    if (size > maxBytes) throw new RejectedInput();
    keys.add(row.id);
    return Object.freeze({id:row.id,text:row.text});
  }));
}

/**
 * job comes directly from the queue, not from a caller or MCP payload. Only
 * claim matching kinds in the scheduling layer. This runner leaves other
 * job kinds untouched. prepare must enforce actual source eligibility and
 * construct the offered fragments from the authoritative immutable source.
 */
export function createVerifiedWorker({
  kind = 'source-thoughts', prepare, construct, review, clock = Date.now, leaseMs = 60_000,
  canonicalInitialReview = false,
} = {}) {
  if (typeof canonicalInitialReview !== 'boolean') throw new TypeError('Invalid trusted review mode.');
  for (const fn of [prepare,construct,review,clock]) {
    if (typeof fn !== 'function') throw new TypeError('Trusted worker adapters are required.');
  }
  if (construct === review) throw new TypeError('Construction and independent review require distinct adapters.');
  return async function runClaimed(db, job) {
    if (job?.state !== 'running' || job.kind !== kind) return {status:'unsupported'};
    const key = {id:job.id,leaseToken:job.lease_token};
    async function own() {
      if (!await renewJobLease(db,{...key,now:clock(),leaseMs})) throw new LeaseLost();
    }
    try {
      await own();
      const input = Object.freeze({id:job.id,kind,source:job.payload,sourceSha256:job.payload_sha256});
      if (await hash(input.source) !== input.sourceSha256) throw new RejectedInput();
      const prepared = await prepare(input,db);
      if (prepared?.eligible !== true) throw new RejectedInput();
      const fragments = freezeFragments(prepared.fragments);
      await own();
      const candidateReply = responseBounded(await construct(Object.freeze({...input,fragments})));
      const drafts = validateModelReply('Construct thought',candidateReply,{supportCount:fragments.length});
      if ((!drafts.length && !canonicalInitialReview) || drafts.length > 128) throw new RejectedInput();
      const candidate = JSON.stringify({schema:'source-thought-candidates/v1',drafts:drafts.map(draft=>({
        text:draft.text, question:draft.question,
        support_ids:draft.support_fragments.map(index=>fragments[index].id),
      }))});
      responseBounded(candidate);
      const verify = async authoritative => {
        if (authoritative.source !== input.source || authoritative.sourceSha256 !== input.sourceSha256 ||
            authoritative.payload !== candidate) throw new VerificationRejected();
        await own();
        const reviewInput = {...authoritative,fragments};
        if (canonicalInitialReview) reviewInput.candidateReply = candidateReply;
        const reviewReply = responseBounded(await review(Object.freeze(reviewInput),db));
        const individual = !canonicalInitialReview || drafts.length > 1;
        validateModelReply('Verify thought',reviewReply,individual ? {sourceDraftCount:drafts.length} : {});
        if (JSON.parse(reviewReply).verdict !== 'accept') throw new VerificationRejected();
        // Commit to exact review, candidate and offered evidence; do not
        // duplicate their private text in the returned receipt.
        const policyVersion = canonicalInitialReview ? canonicalPolicy : legacyPolicy;
        const evidence = [policyVersion, authoritative.sourceSha256, authoritative.payloadSha256, fragments, reviewReply];
        if (canonicalInitialReview) evidence.push(candidateReply);
        const evidenceSha256 = await hash(JSON.stringify(evidence));
        return {verifier:individual ? 'source-batch-review' : 'source-initial-review',policyVersion,evidenceSha256};
      };
      if (!drafts.length) {
        // Canonical zero-draft capture must be semantically reviewed, then
        // finish without a graph record or synthetic publication artifact.
        const receipt = await verify(Object.freeze({...input,payload:candidate,payloadSha256:await hash(candidate)}));
        const done = await finishJob(db,{...key,now:clock(),result:JSON.stringify({
          kind:'reviewed-empty-source',...receipt,
        })});
        return {status:done ? 'reviewed-empty' : 'lost'};
      }
      const publish = createPublisher({clock,verify});
      const result = await publish(db,{...key,payload:candidate});
      return result ? {status:'published',...result} : {status:'lost'};
    } catch (error) {
      if (error instanceof LeaseLost) return {status:'lost'};
      // Recovery is bounded by the original queue budget. No partial
      // publication or persisted provisional candidate is represented here.
      const updated = await failJob(db,{...key,now:clock(),retryable:!(error instanceof RejectedInput)});
      return {status:updated?.state === 'ready' ? 'retrying' : updated?.state === 'failed' ? 'failed' : 'lost'};
    }
  };
}
