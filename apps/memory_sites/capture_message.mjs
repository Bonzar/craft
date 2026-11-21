// Pure canonical memory_capture_message identity and receipt preparation.
// These helpers do not admit data or substitute for the original transaction.
const whitespace = /[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
const object = value => value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype;
const has = (value,key) => Object.hasOwn(value,key);
const clockFields = ['source_message_at','source_message_clock_basis'];
const fingerprintFields = [...clockFields,'timezone','artifact_refs'];
const encoder = new TextEncoder();
const artifactPattern = /^artifact-[0-9a-f]{64}(?![\s\S])/;
const prefix = 'source-clock-v1:';
const error = message => { throw new TypeError(message); };
const str = value => typeof value === 'string' && value.isWellFormed();
const count = value => { let result=0; for(const _ of value) result++; return result; };
const empty = value => !str(value) || !whitespace.test(value);
function order(a,b) {
  const left=[...a], right=[...b];
  for(let i=0;i<Math.min(left.length,right.length);i++){
    const diff=left[i].codePointAt(0)-right[i].codePointAt(0);
    if(diff) return diff;
  }
  return left.length-right.length;
}
// Python json.dumps(..., ensure_ascii=False), with default separators.
function pyjson(value, sort=false) {
  if (str(value)) return JSON.stringify(value);
  if (value === null || typeof value === 'boolean' || Number.isSafeInteger(value))
    return JSON.stringify(value);
  if (typeof value === 'bigint') return String(value);
  if (Array.isArray(value)) return '['+value.map(v=>pyjson(v,sort)).join(', ')+']';
  if (object(value)) return '{'+(sort?Object.keys(value).sort(order):Object.keys(value))
    .map(k=>pyjson(k)+': '+pyjson(value[k],sort)).join(', ')+'}';
  return error('Unsupported canonical JSON value.');
}
async function digest(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))),
    b=>b.toString(16).padStart(2,'0')).join('');
}
const fingerprint = async value => 'sha256:'+await digest(value);

export function normalizeProducerInstant(value) {
  const match=str(value) && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))(?![\s\S])/.exec(value);
  if (!match) return error('source message clock requires a full offset-aware ISO datetime');
  const [,y,m,d,h,min,sec,fraction='',zone,sign,oh,om] = match;
  const date=new Date(0);
  date.setUTCFullYear(+y,+m-1,+d);
  date.setUTCHours(+h,+min,+sec,0);
  if (+y===0 || date.getUTCFullYear()!==+y || date.getUTCMonth()!==+m-1 || date.getUTCDate()!==+d ||
      +h>23 || +min>59 || +sec>59 || +oh>23 || +om>59) return error('invalid source message clock instant');
  if (zone!=='Z') date.setTime(date.getTime()-(sign==='+'?1:-1)*60_000*(60*+oh + +om));
  if (date.getUTCFullYear()<1 || date.getUTCFullYear()>9999) return error('invalid UTC source message clock instant');
  return date.toISOString().slice(0,19)+'.'+fraction.padEnd(6,'0')+'+00:00';
}

function defaultTimezone(value) {
  // Worker ICU recognizes common IANA entries; an exact tzdb membership
  // adapter can be injected where byte-for-byte Python compatibility matters.
  if (!str(value) || !/^[A-Za-z_][A-Za-z0-9_+./-]*$/.test(value) || value.includes('..')) return false;
  try { new Intl.DateTimeFormat('en',{timeZone:value}); return true; } catch { return false; }
}

function normalizeMetadata(metadata, source, messageId, isTimezone, createDefaultBasis=false) {
  if (!object(metadata)) return error('Invalid message metadata.');
  const result={};
  if (metadata.timezone != null) {
    if (!str(metadata.timezone) || isTimezone(metadata.timezone) !== true) return error('source timezone must be a valid IANA timezone');
    result.timezone=metadata.timezone;
  }
  const supplied=metadata.source_message_at;
  const basis=metadata.source_message_clock_basis ??
    (createDefaultBasis && supplied != null ? {kind:'producer',field:'source_message_at',value:supplied,source,message_id:messageId}:null);
  if (supplied == null) {
    if (basis != null) return error('source message clock basis requires source_message_at');
    return result;
  }
  const stamp=normalizeProducerInstant(supplied);
  const required=['kind','field','value','source'];
  if (!object(basis) || basis.kind!=='producer' ||
      !required.every(key=>has(basis,key)&&str(basis[key])&&count(basis[key])>=1&&count(basis[key])<=4096) ||
      Object.keys(basis).some(key=>![...required,'message_id'].includes(key)))
    return error('malformed source message clock basis');
  if (basis.source!==source || (has(basis,'message_id') && basis.message_id!==messageId))
    return error('source message clock basis belongs to different provenance');
  if (normalizeProducerInstant(basis.value)!==stamp) return error('source message clock contradicts its basis value');
  result.source_message_at=stamp;
  result.source_message_clock_basis={...basis,value:stamp};
  return result;
}

function positionValue(v) {
  if (v===null || (Number.isSafeInteger(v)&&v>=0) || (typeof v==='bigint'&&v>=0n)) return v;
  return error('position must be a nonnegative producer transcript ordinal or null');
}

export async function normalizeCaptureMessage(input,{isTimezone=defaultTimezone}={}) {
  if (!object(input)) return error('Invalid capture input.');
  const {session_id,message_id,actor_id,role,text,source,scope='default',position=null,artifact_ids=null,delivery=null}=input;
  const identifiers=[session_id,message_id,actor_id,source,scope];
  if ([...identifiers,text].some(empty)) return error('session/message/actor IDs, text, source and scope must be nonempty');
  if (count(text)>1_000_000 || identifiers.some(v=>count(v)>2048)) return error('message exceeds supported size');
  if (!['user','assistant','tool'].includes(role) || (role==='user'&&['assistant','tool'].includes(actor_id)) ||
      ['system','developer','reasoning'].includes(actor_id))
    return error('role must be user, assistant or tool, with a consistent actor_id');
  positionValue(position);
  if (artifact_ids!==null && (!Array.isArray(artifact_ids) || artifact_ids.length>8 ||
      artifact_ids.some(id=>!str(id)||!artifactPattern.test(id)))) return error('invalid artifact_ids');
  const metadata=normalizeMetadata(input,source,message_id,isTimezone,true);
  const fields=Object.freeze([session_id,message_id,actor_id,text,source,scope,role]);
  const event_id='event-'+await digest(pyjson([session_id,message_id,text,actor_id,message_id]));
  if (metadata.source_message_clock_basis) Object.freeze(metadata.source_message_clock_basis);
  return Object.freeze({session_id,message_id,actor_id,role,text,source,scope,position,event_id,fields,
    metadata:Object.freeze(metadata),artifact_ids:Object.freeze([...new Set(artifact_ids??[])]),
    delivery,isTimezone});
}

const base = (capture,position) => fingerprint(pyjson([...capture.fields,positionValue(position)]));
const clockFingerprint = metadata => fingerprint(pyjson(Object.fromEntries(
  fingerprintFields.filter(key=>has(metadata,key)).map(key=>[key,metadata[key]])
),true));
function attachArtifacts(capture,refs) {
  if (!Array.isArray(refs) || refs.length!==capture.artifact_ids.length ||
      refs.some((ref,i)=>!object(ref)||Object.keys(ref).length!==5||ref.artifact_id!==capture.artifact_ids[i] ||
        ref.sha256!==ref.artifact_id.slice(9)||!Number.isSafeInteger(ref.size_bytes)||ref.size_bytes<1||
        !str(ref.mime_type)||ref.not_interpreted!==true))
    return error('authoritative artifact descriptors required in capture order');
  return {...capture.metadata,...(refs.length?{artifact_refs:refs}:{})};
}
export async function prepareCaptureReceipt(capture,{artifactRefs=[]}={}) {
  const metadata=attachArtifacts(capture,artifactRefs);
  const value=await base(capture,capture.position);
  const receipt=Object.keys(metadata).length ?
    prefix+pyjson({base:value,clock:await clockFingerprint(metadata)},true):value;
  return {receipt,event_id:capture.event_id,raw_metadata:{role:capture.role,message_id:capture.message_id,...metadata}};
}

/** existing must come from one consistent trusted transaction view. This
 * guard returns an optional legacy-payload rewrite, never writes anything. */
export async function compareCaptureReplay(capture,existing,{artifactRefs=[]}={}) {
  const {payload,event_id,position:priorPosition=null,metadata:priorMetadata}=existing;
  const next=attachArtifacts(capture,artifactRefs);
  positionValue(priorPosition);
  if (capture.position!==null && priorPosition!==null && BigInt(capture.position)!==BigInt(priorPosition))
    return error('message identity already has a different producer transcript ordinal');
  const expected=await base(capture,priorPosition);
  const clockReceipt=payload.startsWith(prefix)?JSON.parse(payload.slice(prefix.length)):null;
  const legacy=!clockReceipt&&!payload.startsWith('sha256:');
  const previous=clockReceipt?clockReceipt.base:legacy?await fingerprint(payload):payload;
  if (expected!==previous) return error('message identity already has different immutable content or provenance');
  if (pyjson(priorMetadata.artifact_refs??[],true)!==pyjson(next.artifact_refs??[],true))
    return error('message identity already has different immutable artifact references');
  const before=normalizeMetadata(priorMetadata,capture.source,capture.message_id,capture.isTimezone);
  if (has(before,clockFields[0]) && has(next,clockFields[0]) &&
      clockFields.some(key=>pyjson(before[key],true)!==pyjson(next[key],true)))
    return error('conflicting source message clock or exact clock basis');
  if (has(next,'timezone') && has(priorMetadata,'timezone') && next.timezone!==priorMetadata.timezone)
    return error('conflicting source speaker timezone');
  if (clockReceipt && clockReceipt.clock!==await clockFingerprint(priorMetadata))
    return error('source message clock receipt contradicts immutable raw metadata');
  return {event_id,legacy_rewrite:legacy?expected:null};
}
