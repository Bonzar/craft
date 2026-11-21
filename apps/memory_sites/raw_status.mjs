// Internal raw-capture queue diagnostics; intentionally not memory_status MCP.
// No semantic completeness, linked thoughts, raw payloads, or lease secrets.
const missing = Object.freeze(['thought_work','transport','policy_coverage','unresolved',
  'source_audits','navigation_catalog','model_usage','repair_tracking']);
const phase = Object.freeze({ready:'queued',running:'processing',done:'queue_done',failed:'failed'});
const countFields = Object.freeze(['ready','running','done','failed']);
const whitespace = /[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
function optionalKey(value) {
  return value===null || (typeof value==='string' && value.isWellFormed() && !value.includes('\0') &&
    value.length<=4096 && [...value].length<=2048 && whitespace.test(value));
}

export class UnsupportedStatusSection extends Error {
  constructor() { super('Only internal raw overview and job diagnostics are implemented.'); this.name='UnsupportedStatusSection'; }
}

export function normalizeRawStatus(input={}) {
  const {event_id=null,scope=null,offset=0,limit=100,section='overview',catalog_id=null}=input;
  if (!['overview','job'].includes(section)) throw new UnsupportedStatusSection();
  if (catalog_id!==null) throw new TypeError('catalog_id is not supported in raw status.');
  if (!optionalKey(scope) || !optionalKey(event_id)) throw new TypeError('Invalid scope or event ID.');
  if (section==='job' && event_id===null) throw new TypeError('job section requires event_id');
  const max=section==='job'?12000:1000;
  if (!Number.isSafeInteger(offset) || offset<0 || !Number.isSafeInteger(limit) || limit<1 || limit>max)
    throw new TypeError('Invalid diagnostic page bounds.');
  return {event_id,scope,offset,limit,section};
}

const query = (db,sql,...args) => db.prepare(sql).bind(...args);
const rows = result => {
  if (result?.success===false || !Array.isArray(result?.results)) throw new Error('Raw status storage error.');
  return result.results;
};
// Only join durable raw admission records, not arbitrary rows in the queue.
const admitted = `FROM memory_raw_captures c JOIN memory_jobs j ON c.event_id=j.id
  WHERE j.kind='source-thoughts' AND (? IS NULL OR json_extract(c.raw_json,'$.scope') = ?)`;
const jobCols = 'j.id job_id,j.state,j.attempts,j.available_at,j.updated_at,j.failure_code';
const safeFailure = new Set(['non_retryable','attempts_exhausted','lease_exhausted','retryable']);
const failure = value => value===null ? null : (safeFailure.has(value)?value:'unspecified_failure');
function job(row) {
  if (!row) throw new RangeError('Unknown raw job or event ID in requested scope.');
  return {job_id:row.job_id,state:row.state,progress:phase[row.state],attempts:row.attempts,
    available_at_ms:row.available_at,updated_at_ms:row.updated_at,failure_code:failure(row.failure_code)};
}
function page(offset,limit,total) {
  return {offset,limit,total,next_offset:offset < total-limit ? offset+limit : null};
}
function jobPage(row,{event_id,scope,offset,limit,section}) {
  const serialized = [...JSON.stringify(job(row))];
  if (offset>serialized.length) throw new RangeError('Offset is outside the requested section.');
  const end=Math.min(serialized.length,offset+limit);
  const next_arguments=end<serialized.length?{event_id,scope,section,offset:end,limit}:null;
  return {coverage:'raw_capture_jobs_only',event_id,scope,section,encoding:'json',offset,end,
    total_characters:serialized.length,content:serialized.slice(offset,end).join(''),
    response_complete:next_arguments===null,next_arguments};
}

export async function getRawStatus(db,input) {
  const options=normalizeRawStatus(input);
  const {event_id,scope,offset,limit,section}=options;
  const exact=()=>query(db,`SELECT ${jobCols} ${admitted} AND c.event_id=?`,scope,scope,event_id);
  if (section==='job') {
    const [result]=await db.batch([exact()]);
    return jobPage(rows(result)[0],options);
  }

  const filters=[scope,scope];
  const pending="j.state <> 'done'";
  const errors='j.failure_code IS NOT NULL';
  const sorted='ORDER BY j.available_at,j.id LIMIT ? OFFSET ?';
  const requests=[
    query(db,`SELECT count(*) total,sum(j.failure_code IS NOT NULL) errors,j.state ${admitted} GROUP BY j.state`,...filters),
    query(db,`SELECT ${jobCols} ${admitted} AND ${pending} ${sorted}`,...filters,limit,offset),
    query(db,`SELECT ${jobCols} ${admitted} AND ${errors} ${sorted}`,...filters,limit,offset),
  ];
  if (event_id!==null) requests.push(exact());
  const result=await db.batch(requests);
  const job_counts=Object.fromEntries(countFields.map(state=>[state,0]));
  let raw_error_count=0;
  for (const row of rows(result[0])) {
    if (!Object.hasOwn(job_counts,row.state)) throw new Error('Unexpected queue state.');
    job_counts[row.state]=row.total;
    raw_error_count+=row.errors;
  }
  const event_count=Object.values(job_counts).reduce((a,b)=>a+b,0);
  const raw_pending_count=event_count-job_counts.done; // canonical unfinished includes failed rows
  const pagination={pending_jobs:page(offset,limit,raw_pending_count),errors:page(offset,limit,raw_error_count)};
  return {
    coverage:'raw_capture_jobs_only',unavailable:[...missing],diagnostic_scope:scope===null?'global':scope,
    event_count,raw_pending_count,raw_error_count,job_counts,
    pending_jobs:rows(result[1]).map(job),errors:rows(result[2]).map(job),
    pagination,response_complete:Object.values(pagination).every(p=>p.next_offset===null),
    ...(event_id===null?{}:{job:job(rows(result[3])[0])}),
  };
}
