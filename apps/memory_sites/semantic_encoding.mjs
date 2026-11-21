// Pure encoding and pinning for decoded canonical Thought records. This
// does NOT authenticate a model review, own a lease, or write semantic state.
import {eventEligibility} from './source_evidence.mjs';
const encoder=new TextEncoder();
const maxEncoded=256*1024;
const isObject=v=>v!==null&&typeof v==='object'&&Object.getPrototypeOf(v)===Object.prototype;
const str=v=>typeof v==='string'&&v.isWellFormed();
const text=v=>str(v)&&/[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u.test(v);
const positive=v=>Number.isSafeInteger(v)&&v>0;
const has=(o,k)=>Object.hasOwn(o,k);
const invalid=label=>{throw new TypeError('Invalid canonical '+label+'.');};
const hashPattern=/^[0-9a-f]{64}(?![\s\S])/;
const rawKeys=['id','session_id','turn_id','prompt','actor','source','scope','recorded_at','metadata'];
const thoughtKeys=['id','version','text','question','origin','bases','metadata','created_at'];
function cmp(a,b) {
  const left=[...a],right=[...b];
  for(let i=0;i<Math.min(left.length,right.length);i++) {
    const order=left[i].codePointAt(0)-right[i].codePointAt(0);
    if(order) return order;
  }
  return left.length-right.length;
}
const exact=(value,keys)=>isObject(value)&&Object.keys(value).length===keys.length&&keys.every(k=>has(value,k));
function serialize(value,spaces=false) {
  const comma=spaces?', ':',', colon=spaces?': ':':';
  let bytes=0;
  function token(v) {
    bytes+=encoder.encode(v).length;
    if (bytes>maxEncoded) return invalid('encoded size');
    return v;
  }
  function visit(v,depth) {
    if(depth>32) return invalid('encoded depth');
    if(str(v)||v===null||typeof v==='boolean'||Number.isSafeInteger(v)&&!Object.is(v,-0))
      return token(JSON.stringify(v));
    if(Array.isArray(v)) {
      if (v.length>maxEncoded || Object.keys(v).length!==v.length) return invalid('array');
      const fields=[];
      token('[');
      for(let i=0;i<v.length;i++) {
        if(!has(v,i)) return invalid('array');
        if(i) token(comma);
        fields.push(visit(v[i],depth+1));
      }
      return '['+fields.join(comma)+token(']');
    }
    if(isObject(v)) {
      const keys=Object.keys(v).sort(cmp);
      const fields=[];
      token('{');
      for(const key of keys) {
        if(fields.length) token(comma);
        fields.push(visit(key,depth+1)+token(colon)+visit(v[key],depth+1));
      }
      return '{'+fields.join(comma)+token('}');
    }
    return invalid('JSON value or unsupported number');
  }
  return visit(value,0);
}
async function sha(payload) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(payload))),
    byte=>byte.toString(16).padStart(2,'0')).join('');
}
function shape(thought) {
  if(!exact(thought,thoughtKeys)||!text(thought.id)||!positive(thought.version)||!text(thought.text)||
      !(thought.question===null || text(thought.question))||!isObject(thought.origin)||!Array.isArray(thought.bases)||
      !isObject(thought.metadata)||!text(thought.created_at)) invalid('Thought');
  return thought;
}
function slab(body,span) {
  if(span==null) return body;
  if(!exact(span,['start','end'])||!Number.isSafeInteger(span.start)||!Number.isSafeInteger(span.end)||
      span.start<0||span.end<=span.start) return invalid('span');
  const chars=[...body];
  if(span.end>chars.length) return invalid('span');
  return chars.slice(span.start,span.end).join('');
}
function basisShape(basis,kind) {
  const allowed=['span','meaning','content_hash',...(kind==='event'?['event_id']:['thought_id','version','observed_head_version'])];
  if(!isObject(basis)||Object.keys(basis).some(key=>!allowed.includes(key))||
     (has(basis,'meaning')&&!text(basis.meaning))||(has(basis,'content_hash')&&(!str(basis.content_hash)||!hashPattern.test(basis.content_hash))))
    return invalid('basis');
}
async function finalizeBasis(basis,source,selected) {
  const content_hash=await sha(serialize({source,span:basis.span??null,text:selected}));
  if(has(basis,'content_hash')&&basis.content_hash!==content_hash) invalid('basis content hash');
  return {...basis,content_hash};
}

/** Original _pin_basis hashes the SQLite row, with metadata still encoded
 * using queue.py's sorted Python JSON WITH spaces (not phase-0 compact JSON).
 * Strict input represents the decoded D1 RawEvent of the supported generic path. */
export async function pinCanonicalRawBasis(basis,event) {
  basisShape(basis,'event');
  if(!exact(event,rawKeys)||!rawKeys.filter(k=>k!=='metadata').every(k=>text(event[k]))||
     !isObject(event.metadata)||!text(basis.event_id)||basis.event_id!==event.id||
     !eventEligibility(event).allowed) return invalid('raw evidence');
  const selected=slab(event.prompt,basis.span);
  return finalizeBasis(basis,{...event,metadata:serialize(event.metadata,true)},selected);
}

/** These exact immutable rows and active eligible head MUST come from one
 * trusted consistent storage view. The eventual writer must recheck the
 * same head and canonical lease in its write transaction. */
export async function pinCanonicalThoughtBasis(basis,{selected,currentHead}={}) {
  basisShape(basis,'thought');
  shape(selected); shape(currentHead);
  if(!text(basis.thought_id)||basis.thought_id!==selected.id||selected.id!==currentHead.id||
      (basis.observed_head_version!=null && !positive(basis.observed_head_version))) return invalid('thought evidence');
  const chosen=has(basis,'version')?basis.version:currentHead.version;
  if(!positive(chosen)||chosen!==selected.version||
      (basis.observed_head_version??chosen)!==currentHead.version) return invalid('observed head');
  const {observed_head_version:_,...binding}=basis;
  return finalizeBasis({...binding,version:chosen},selected,slab(selected.text,basis.span));
}

export async function encodeCanonicalThought(thought) {
  shape(thought);
  for(const basis of thought.bases) {
    if(!isObject(basis)||!str(basis.content_hash)||!hashPattern.test(basis.content_hash)||
       (has(basis,'thought_id')===has(basis,'event_id')) ||
       (has(basis,'thought_id')&&(!text(basis.thought_id)||!positive(basis.version))) ||
       (has(basis,'event_id')&&!text(basis.event_id)) || has(basis,'observed_head_version')) invalid('pinned basis');
    basisShape(basis,has(basis,'event_id')?'event':'thought');
  }
  const payload=serialize(thought);
  return {payload,content_hash:await sha(payload)};
}
