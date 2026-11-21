// Server-only. Use a trusted semantic verifier installed by application code.
// A model response, client-provided flag or request body cannot stand in for it.
// This publishes an immutable verified artifact, not changes to the live graph.
import {QueueInputError} from './queue.mjs';
const bytes = new TextEncoder();
const MAX_PAYLOAD = 64 * 1024;
const verifyId = /^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,95}$/;
const hashPattern = /^[0-9a-f]{64}$/;
const tokenPattern = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const fields = 'job_id,source_sha256,payload_sha256,verifier,policy_version,evidence_sha256,published_at';
const bind = (db, sql, ...args) => db.prepare(sql).bind(...args);
function first(result) {
  if (result?.success === false || !Array.isArray(result?.results)) throw new Error('Publication storage error.');
  return result.results[0] ?? null;
}
async function hash(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes.encode(value))),
    b => b.toString(16).padStart(2,'0')).join('');
}

export class VerificationRejected extends Error {
  constructor() { super('Publication failed semantic verification.'); this.name = 'VerificationRejected'; }
}
export class PublicationConflict extends Error {
  constructor() { super('This lease already published a different payload.'); this.name = 'PublicationConflict'; }
}

export function createPublisher({verify, clock = Date.now} = {}) {
  if (typeof verify !== 'function' || typeof clock !== 'function')
    throw new TypeError('A server-side publication verifier and clock are required.');
  function now() {
    const value = clock();
    if (!Number.isSafeInteger(value) || value < 0 || value > 8_000_000_000_000_000)
      throw new TypeError('Invalid server clock.');
    return value;
  }

  return async function publish(db, {id, leaseToken, payload} = {}) {
    if (typeof id !== 'string' || !id.trim() || id.length > 512 || !id.isWellFormed() || id.includes('\0') ||
        typeof leaseToken !== 'string' || !tokenPattern.test(leaseToken) ||
        typeof payload !== 'string' || payload.length > MAX_PAYLOAD || !payload.isWellFormed() ||
        bytes.encode(payload).byteLength > MAX_PAYLOAD)
      throw new QueueInputError('Invalid publication input.');
    let candidate;
    try { candidate = JSON.parse(payload); } catch { throw new QueueInputError('Invalid publication JSON.'); }
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate))
      throw new QueueInputError('A publication must be a JSON object.');
    const [payloadDigest, leaseDigest] = await Promise.all([hash(payload), hash(leaseToken)]);
    const readPrior = () => bind(db, `SELECT ${fields} FROM memory_job_publications WHERE job_id=? AND lease_sha256=?`, id, leaseDigest);
    function replay(prior) {
      if (!prior) return null;
      if (prior.payload_sha256 !== payloadDigest) throw new PublicationConflict();
      return {created:false, publication:prior};
    }

    const [jobResult, priorResult] = await db.batch([
      bind(db,`SELECT id,kind,payload,payload_sha256 FROM memory_jobs
        WHERE id=? AND state='running' AND lease_token=? AND lease_expires_at>?`, id, leaseToken, now()),
      readPrior(),
    ]);
    const prior = replay(first(priorResult));
    if (prior) return prior;
    const job = first(jobResult);
    if (!job) return null;
    // Bind the verifier to authoritative source bytes from storage and to the
    // exact candidate bytes that will be written. The verifier must implement
    // the appropriate evidence/completeness policy for this job kind.
    const receipt = await verify(Object.freeze({id, kind:job.kind, source:job.payload,
      sourceSha256:job.payload_sha256, payload, payloadSha256:payloadDigest}));
    const {verifier, policyVersion, evidenceSha256} = receipt ?? {};
    if (typeof verifier !== 'string' || !verifyId.test(verifier) ||
        typeof policyVersion !== 'string' || !verifyId.test(policyVersion) ||
        typeof evidenceSha256 !== 'string' || !hashPattern.test(evidenceSha256))
      throw new VerificationRejected();

    const publicationTime = now();
    // INSERT ... SELECT checks the actual lease token and immutable input
    // digest. The AFTER INSERT trigger completes the job inside that same
    // SQLite statement. One D1 batch also covers the replay read.
    const [writeResult, latestResult] = await db.batch([
      bind(db,`INSERT INTO memory_job_publications
        (job_id,source_sha256,lease_sha256,payload,payload_sha256,verifier,policy_version,evidence_sha256,published_at)
        SELECT id,payload_sha256,?,?,?,?,?,?,? FROM memory_jobs
        WHERE id=? AND state='running' AND lease_token=? AND lease_expires_at>? AND payload_sha256=?
        ON CONFLICT(job_id) DO NOTHING RETURNING ${fields}`,
      leaseDigest,payload,payloadDigest,verifier,policyVersion,evidenceSha256,publicationTime,
      id,leaseToken,publicationTime,job.payload_sha256),
      readPrior(),
    ]);
    const inserted = first(writeResult);
    return inserted ? {created:true,publication:inserted} : replay(first(latestResult));
  };
}
