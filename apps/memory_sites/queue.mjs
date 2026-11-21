// Durable execution primitives, not model processing or a permanent daemon.
// Times are server-supplied epoch milliseconds. Use one stable capture ID per
// logical input; IDs and job bodies are private and must not appear in logs.
export const CAPTURE_BYTES = 64 * 1024;
const CHECKPOINT_BYTES = 32 * 1024;
const RESULT_BYTES = 64 * 1024;
const MAX_LEASE_MS = 60*60*1000;
const MAX_CLOCK = 8_000_000_000_000_000;
const MAX_DELAY = 24*60*60*1000;
const MAX_BATCH = 25;
const decoder = new TextEncoder();

export class QueueInputError extends Error { constructor(message) { super(message); this.name='QueueInputError'; this.status=400; } }
export class CaptureConflict extends Error { constructor() { super('Capture ID already belongs to different content or options.'); this.name='CaptureConflict'; this.status=409; } }
const inputError = (message) => { throw new QueueInputError(message); };
const integer = (value, min, max, label) => {
  if (!Number.isSafeInteger(value)||value<min||value>max) inputError('Invalid '+label+'.');
  return value;
};
const clock = (now=Date.now()) => integer(now,0,MAX_CLOCK,'queue time');
const idValue = (id) => {
  if (typeof id!=='string'||!id.trim()||id.length>512||!id.isWellFormed()||id.includes('\0')) inputError('Invalid job ID.');
  return id;
};
const tokenValue = (token) => {
  if (typeof token!=='string'||!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(token))
    inputError('Invalid lease token.');
  return token;
};
const jsonText = (raw, limit, label) => {
  if (typeof raw!=='string'||raw.length>limit||!raw.isWellFormed()||decoder.encode(raw).byteLength>limit)
    inputError('Invalid or oversized '+label+'.');
  try { JSON.parse(raw); } catch { inputError('Invalid '+label+' JSON.'); }
  return raw;
};
function prepare(db,sql,...args) {
  const statement=db.prepare(sql);
  return args.length?statement.bind(...args):statement;
}
function rowSet(result) {
  if (result?.success===false||!Array.isArray(result?.results)) throw new Error('Job storage error.');
  return result.results;
}
async function runRow(db,sql,...args) {
  const [result]=await db.batch([prepare(db,sql,...args)]);
  return rowSet(result)[0]??null;
}
async function sha256(text) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',decoder.encode(text))),
    (byte)=>byte.toString(16).padStart(2,'0')).join('');
}
const publicFields='id,kind,state,attempts,max_attempts,created_at,updated_at,available_at,lease_expires_at,failure_code';
const privateClaimFields=publicFields+',payload,payload_sha256,lease_token,checkpoint';

export async function captureJob(db,{id,kind,body,maxAttempts=3,now}={}) {
  idValue(id);
  now=clock(now);
  if (typeof kind!=='string'||!/^[a-z][a-z0-9_-]{0,63}$/.test(kind)) inputError('Invalid job kind.');
  integer(maxAttempts,1,10,'max attempts');
  jsonText(body,CAPTURE_BYTES,'capture body');
  const digest=await sha256(body);
  const [insertResult, findResult]=await db.batch([
    prepare(db,`INSERT INTO memory_jobs
      (id,kind,payload,payload_sha256,state,max_attempts,created_at,updated_at,available_at)
      VALUES(?,?,?,?,'ready',?,?,?,?) ON CONFLICT(id) DO NOTHING RETURNING id`,
      id,kind,body,digest,maxAttempts,now,now,now),
    prepare(db,`SELECT ${publicFields},payload_sha256 FROM memory_jobs WHERE id=?`,id),
  ]);
  const created=rowSet(insertResult).length===1;
  const existing=rowSet(findResult)[0];
  if (!existing) throw new Error('Captured job unavailable.');
  if (existing.payload_sha256!==digest||existing.kind!==kind||existing.max_attempts!==maxAttempts) throw new CaptureConflict();
  const {payload_sha256: _, ...job}=existing;
  return {created,job};
}

export async function sweepExpired(db,{now,limit=MAX_BATCH}={}) {
  now=clock(now);
  integer(limit,1,MAX_BATCH,'sweep limit');
  const [result]=await db.batch([prepare(db,`UPDATE memory_jobs SET state='failed',
    lease_token=NULL,lease_expires_at=NULL,updated_at=?,failure_code='lease_exhausted'
    WHERE id IN (SELECT id FROM memory_jobs WHERE state='running' AND lease_expires_at<=?
      AND attempts>=max_attempts ORDER BY lease_expires_at,id LIMIT ?) RETURNING id`,now,now,limit)]);
  return rowSet(result).length;
}

export async function claimJob(db,{now,leaseMs=60_000}={}) {
  now=clock(now);
  integer(leaseMs,1,MAX_LEASE_MS,'lease duration');
  const token=crypto.randomUUID();
  // One conditional UPDATE; SQLite serializes claims on the same D1 state.
  // An expired job is eligible only if it still has an unspent attempt.
  return runRow(db,`UPDATE memory_jobs SET state='running',attempts=attempts+1,
    lease_token=?,lease_expires_at=?,updated_at=?,failure_code=NULL
    WHERE id=(SELECT id FROM memory_jobs WHERE attempts<max_attempts AND
      ((state='ready' AND available_at<=?) OR (state='running' AND lease_expires_at<=?))
      ORDER BY CASE WHEN state='running' THEN lease_expires_at ELSE available_at END,
        created_at,id LIMIT 1) RETURNING ${privateClaimFields}`,
    token,now+leaseMs,now,now,now);
}

const fencedWhere="WHERE id=? AND state='running' AND lease_token=? AND lease_expires_at>?";
function validateFence(id,leaseToken,now) {
  idValue(id);
  tokenValue(leaseToken);
  return clock(now);
}

export async function saveCheckpoint(db,{id,leaseToken,checkpoint,now}={}) {
  now=validateFence(id,leaseToken,now);
  jsonText(checkpoint,CHECKPOINT_BYTES,'checkpoint');
  return runRow(db,`UPDATE memory_jobs SET checkpoint=?,updated_at=?
    ${fencedWhere} RETURNING ${publicFields}`,checkpoint,now,id,leaseToken,now);
}

export async function renewJobLease(db,{id,leaseToken,now,leaseMs=60_000}={}) {
  now=validateFence(id,leaseToken,now);
  integer(leaseMs,1,MAX_LEASE_MS,'lease duration');
  return runRow(db,`UPDATE memory_jobs SET lease_expires_at=MAX(lease_expires_at,?),updated_at=?
    ${fencedWhere} RETURNING ${publicFields}`,now+leaseMs,now,id,leaseToken,now);
}

export async function finishJob(db,{id,leaseToken,result=null,now}={}) {
  now=validateFence(id,leaseToken,now);
  if (result!==null) jsonText(result,RESULT_BYTES,'job result');
  return runRow(db,`UPDATE memory_jobs SET state='done',result=?,updated_at=?,
    lease_token=NULL,lease_expires_at=NULL,failure_code=NULL
    ${fencedWhere} RETURNING ${publicFields}`,result,now,id,leaseToken,now);
}

export async function failJob(db,{id,leaseToken,retryable=false,retryDelayMs=30_000,now}={}) {
  now=validateFence(id,leaseToken,now);
  if (typeof retryable!=='boolean') inputError('Invalid retry policy.');
  integer(retryDelayMs,0,MAX_DELAY,'retry delay');
  return runRow(db,`UPDATE memory_jobs SET
    state=CASE WHEN ?=1 AND attempts<max_attempts THEN 'ready' ELSE 'failed' END,
    available_at=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL,
    failure_code=CASE WHEN ?=0 THEN 'non_retryable'
      WHEN attempts>=max_attempts THEN 'attempts_exhausted' ELSE 'retryable' END
    ${fencedWhere} RETURNING ${publicFields}`,
    Number(retryable),now+retryDelayMs,now,Number(retryable),id,leaseToken,now);
}

export async function processJobs(db,{limit=1,leaseMs=60_000,handle,now=Date.now}={}) {
  integer(limit,1,MAX_BATCH,'processing limit');
  integer(leaseMs,1,MAX_LEASE_MS,'lease duration');
  if (typeof handle!=='function'||typeof now!=='function') inputError('Invalid queue processor.');
  const counts={swept:await sweepExpired(db,{now:now(),limit}),claimed:0,done:0,failed:0,retried:0,lost:0};
  for (let i=0;i<limit;i++) {
    const job=await claimJob(db,{leaseMs,now:now()});
    if (!job) break;
    counts.claimed++;
    const key={id:job.id,leaseToken:job.lease_token};
    const checkpoint=(value)=>saveCheckpoint(db,{...key,now:now(),checkpoint:value});
    const renew=()=>renewJobLease(db,{...key,now:now(),leaseMs});
    let outcome;
    try { outcome=await handle(job,{checkpoint,renew}); }
    catch { outcome={ok:false,retryable:true}; }
    // The concrete handler owns external side effects and idempotency. This
    // driver executes no model or network actions unless given such a handler.
    const updated=outcome?.ok===true
      ? await finishJob(db,{...key,now:now(),result:outcome.result??null})
      : await failJob(db,{...key,now:now(),retryable:outcome?.retryable===true,
          retryDelayMs:outcome?.retryDelayMs??30_000});
    if (!updated) counts.lost++;
    else if (updated.state==='done') counts.done++;
    else if (updated.state==='ready') counts.retried++;
    else counts.failed++;
  }
  return counts;
}
