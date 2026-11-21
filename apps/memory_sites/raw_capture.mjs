// Internal storage only: never expose raw receipts, job payloads, claims or
// authority through MCP or an HTTP request. Authentication is the caller's job.
import {CAPTURE_BYTES} from './queue.mjs';
import {normalizeCaptureMessage, prepareCaptureReceipt, compareCaptureReplay} from './capture_message.mjs';

const encoder = new TextEncoder();
const bound = (db,sql,...args) => db.prepare(sql).bind(...args);
const first = result => {
  if (result?.success===false || !Array.isArray(result?.results)) throw new Error('Capture storage error.');
  return result.results[0]??null;
};
async function digest(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
    byte=>byte.toString(16).padStart(2,'0')).join('');
}
function timestamp(now) {
  if (!Number.isSafeInteger(now) || now<0 || now>253402300799999)
    throw new TypeError('Invalid admission time.');
  return new Date(now).toISOString().replace(/\.(\d{3})Z$/,(_match,ms)=>'.'+ms+'000+00:00');
}
export class CapturePositionConflict extends Error {
  constructor() { super('Session transcript position already belongs to a different message.'); this.name='CapturePositionConflict'; }
}
export class UnsupportedCapture extends Error {
  constructor(message) { super(message); this.name='UnsupportedCapture'; }
}

export async function admitRawCapture(db,input,{now=Date.now(),isTimezone}={}) {
  // Reject even a structurally valid delivery claim because acknowledging it
  // without the canonical stream ledger would lose the producer's data.
  const capture=await normalizeCaptureMessage(input,{isTimezone});
  if (capture.delivery!==null || capture.artifact_ids.length)
    throw new UnsupportedCapture('Operational delivery and artifact references require their own transactional ledgers.');
  const ordinal=capture.position;
  if (ordinal!==null && typeof ordinal!=='number')
    throw new UnsupportedCapture('Transcript position exceeds exact D1 number support.');
  const {receipt,event_id,raw_metadata}=await prepareCaptureReceipt(capture);
  const rawJSON=JSON.stringify({
    id:event_id,session_id:capture.session_id,turn_id:capture.message_id,prompt:capture.text,
    actor:capture.actor_id,source:capture.source,scope:capture.scope,recorded_at:timestamp(now),
    metadata:raw_metadata,
  });
  const rawBytes=encoder.encode(rawJSON);
  if (rawBytes.length>CAPTURE_BYTES)
    throw new UnsupportedCapture('Raw event exceeds the current worker queue size limit.');
  const rawSHA=await digest(rawBytes);
  const [insertResult,findResult,ordinalResult]=await db.batch([
    // No writes on replay or an already-used position. The UNIQUE indices
    // remain authoritative; a job-ID collision aborts the insert trigger.
    bound(db,`INSERT INTO memory_raw_captures
      (event_id,session_id,message_id,position,receipt,raw_json,raw_sha256,captured_at)
      SELECT ?,?,?,?,?,?,?,? WHERE NOT EXISTS (
        SELECT 1 FROM memory_raw_captures WHERE session_id=? AND position=?
      ) ON CONFLICT(session_id,message_id) DO NOTHING RETURNING event_id`,
      event_id,capture.session_id,capture.message_id,ordinal,receipt,rawJSON,rawSHA,now,
      capture.session_id,ordinal),
    bound(db,`SELECT c.event_id,c.position,c.receipt,c.raw_json,
      j.state AS job_state,j.attempts AS job_attempts
      FROM memory_raw_captures c JOIN memory_jobs j ON j.id=c.event_id
      WHERE c.session_id=? AND c.message_id=?`,capture.session_id,capture.message_id),
    bound(db,'SELECT event_id FROM memory_raw_captures WHERE session_id=? AND position=?',
      capture.session_id,ordinal),
  ]);
  const created=first(insertResult)!==null;
  const found=first(findResult);
  const occupied=first(ordinalResult);
  if (!found) {
    if (occupied) throw new CapturePositionConflict();
    throw new Error('Captured event or job is missing.');
  }
  const raw=JSON.parse(found.raw_json);
  if (!created) {
    // Replay cannot overwrite raw provenance, old time, queue status or
    // already accepted order. This schema starts with hashed receipts only.
    await compareCaptureReplay(capture,{event_id:found.event_id,position:found.position,
      payload:found.receipt,metadata:raw.metadata});
  }
  return {created,event_id:found.event_id,position:found.position,recorded_at:raw.recorded_at,
    job:{id:found.event_id,state:found.job_state,attempts:found.job_attempts}};
}
