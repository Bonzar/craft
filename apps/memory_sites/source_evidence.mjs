// Single captured RawEvent only. Caller must be trusted intake: a source's
// actor/transport/metadata cannot authenticate themselves. No context or
// historical thought is inferred from an untrusted envelope.
import {CAPTURE_BYTES} from './queue.mjs';

export const SOURCE_POLICY = 'codex-visible-dialogue-only-v1';
const automaticTransports = new Set(['codex-native-client','codex-desktop']);
const privateChannels = new Set(['analysis','reasoning']);
const toolTypes = new Set(['function_call','function_call_output','tool_call','tool_result',
  'custom_tool_call','custom_tool_call_output']);
const typeFields = ['type','message_type','payload_type','response_item_type'];
const record = value => value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype;
const own = (object,key,fallback) => Object.hasOwn(object,key) ? object[key] : fallback;
const encoder = new TextEncoder();
// Python's str.isspace / regex \s includes U+001C..U+001F, excludes BOM.
const nonSpace = /[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
const textFields = ['id','session_id','turn_id','prompt','actor','source','scope','recorded_at'];
const string = v => typeof v === 'string' && v.isWellFormed();
const key = v => string(v) && v.length <= 512 && nonSpace.test(v) && !v.includes('\0');
const useful = v => string(v) && nonSpace.test(v);

function getMetadata(event) {
  let metadata = own(event,'metadata',{});
  if (typeof metadata === 'string') metadata = JSON.parse(metadata);
  if (metadata === null) metadata = {};
  if (!record(metadata)) throw new TypeError('Invalid event metadata.');
  return metadata;
}

/** Direct raw-event policy; metadata wins over top-level fields when present. */
export function eventEligibility(event) {
  if (!record(event)) throw new TypeError('Invalid captured event.');
  const metadata = getMetadata(event);
  const source = own(event,'source','');
  const actor = own(event,'actor','');
  const automatic = (typeof source === 'string' && source.startsWith('codex://')) ||
    automaticTransports.has(own(metadata,'transport')) || own(metadata,'ingest_provider') === 'codex';
  let reason = null;
  if (automatic) {
    const role = own(metadata,'role',own(event,'role',actor));
    const channel = own(metadata,'channel',own(event,'channel'));
    const contentType = own(metadata,'content_type',own(event,'content_type'));
    if (role === 'tool' || actor === 'tool' || (typeof actor === 'string' && actor.startsWith('tool:'))) {
      reason = 'automatic_codex_tool';
    } else if (privateChannels.has(channel) || contentType === 'reasoning' || privateChannels.has(role)) {
      reason = 'automatic_codex_private_channel';
    } else if (typeFields.some(field => toolTypes.has(own(metadata,field,own(event,field))))) {
      reason = 'automatic_codex_tool_payload';
    } else if (['system','developer'].includes(role) || ['system','developer','reasoning'].includes(actor)) {
      reason = 'automatic_codex_non_dialogue_role';
    }
  }
  return {allowed:reason === null,reason,policy:SOURCE_POLICY};
}

/** Lossless Python PreparedSupport._spans equivalent, full source only. */
export function sourceSpans(prompt) {
  if (!string(prompt)) throw new TypeError('Invalid source text.');
  const results = [];
  const ends = prompt.matchAll(/(?<=[.!?;])[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|\n+/gu);
  let from = 0;
  let offset = 0;
  const take = to => {
    const chunk = prompt.slice(from,to);
    const start = offset;
    for (const _ of chunk) offset++;
    from = to;
    if (!nonSpace.test(chunk) && results.length) {
      const prev = results.pop();
      results.push({start:prev.start,end:offset,text:prev.text+chunk});
    } else {
      results.push({start,end:offset,text:chunk});
    }
  };
  let scanned = 0;
  let content = false;
  for (const m of ends) {
    const end = m.index + m[0].length;
    content ||= nonSpace.test(prompt.slice(scanned,end));
    scanned = end;
    if (end > from && content) {
      take(end);
      content = false;
    }
  }
  if (prompt.length > from) take(prompt.length);
  return results;
}

/**
 * Trusted prepare adapter for createVerifiedWorker. Does not receive or
 * expose queue credentials, nor store evidence. The reversible reference
 * encodes raw event identity + exact span, matching the canonical basis
 * coordinates rather than a model-picked string.
 */
export function prepareCapturedSource({source} = {}) {
  const invalid = {eligible:false,reason:'invalid_raw_event',policy:SOURCE_POLICY,fragments:[]};
  if (!string(source) || source.length > CAPTURE_BYTES || encoder.encode(source).length > CAPTURE_BYTES)
    return invalid;
  let event, decision;
  try {
    event = JSON.parse(source);
    decision = eventEligibility(event);
  } catch {
    return invalid;
  }
  if (!decision.allowed) return {eligible:false,reason:decision.reason,policy:decision.policy,fragments:[]};
  if (!textFields.every(f => Object.hasOwn(event,f) && (f === 'prompt' ? useful(event[f]) : key(event[f])))) return invalid;
  let workerBytes=0;
  const fragments = sourceSpans(event.prompt).map(({start,end,text}) => {
    const id = JSON.stringify([event.id,start,end]);
    workerBytes += encoder.encode(id).length + encoder.encode(text).length;
    return {id,text};
  });
  if (fragments.length > 128 || fragments.some(fragment=>fragment.id.length>512) || workerBytes>CAPTURE_BYTES) {
    return {eligible:false,reason:'prepared_source_exceeds_bounds',policy:SOURCE_POLICY,fragments:[]};
  }
  return {eligible:true,reason:null,policy:SOURCE_POLICY,fragments};
}
