// Local internal slice of original memory_source. Never bind to an MCP
// dispatcher without a verified owner and one exact authorized data scope.
const fields=new Set(['record_id','start','end','section','offset','limit','snapshot_id']);
const slots=new Set(['overview','text','metadata','references','edges','extraction_history']);
const directSections=new Set(['text','metadata']);
const PAGE_CHARACTERS=6000, RESPONSE_CHARACTERS=28_000, MAX_RAW=64*1024;
const nonblank=/[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
const string=(value)=>typeof value==='string'&&value.isWellFormed();
const identity=(value)=>string(value)&&value.length<=4096&&Array.from(value).length<=2048&&nonblank.test(value)&&!value.includes('\0');
const object=(value)=>value!==null&&typeof value==='object'&&Object.getPrototypeOf(value)===Object.prototype;
const chars=body=>[...body];
const continuation=arguments_=>({tool:'memory_source',arguments:arguments_});
const integer=value=>Number.isSafeInteger(value);
const select=(result)=>{
  if(result?.success===false||!Array.isArray(result?.results)) throw new Error('Source storage is unavailable.');
  return result.results[0];
};
const missing=()=>new RangeError('Unknown source event in the authorized scope.');

export class UnavailableSourceSection extends Error {
  constructor() {
    super('This raw-source slice supports text, metadata, and an exact character span; canonical overview, history, and thought sections are unavailable.');
    this.name='UnavailableSourceSection';
  }
}

function fits(value) {
  const content=[{type:'text',text:JSON.stringify(value,null,2)}];
  const envelope={content,structuredContent:value,isError:false};
  return chars(JSON.stringify(envelope)).length+256<=RESPONSE_CHARACTERS;
}
function unicodeOrder(a,b) {
  const aa=chars(a),bb=chars(b);
  for(let i=0;i<Math.min(aa.length,bb.length);i++) {
    const diff=aa[i].codePointAt(0)-bb[i].codePointAt(0);
    if(diff)return diff;
  }
  return aa.length-bb.length;
}
function metadataJSON(value,depth=0) {
  if(depth>32)throw new TypeError('Raw metadata exceeds supported depth.');
  if(value===null||string(value)||typeof value==='boolean'||integer(value)&&!Object.is(value,-0))return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(v=>metadataJSON(v,depth+1)).join(',')+']';
  if(object(value))return '{'+Object.keys(value).sort(unicodeOrder)
    .map(key=>JSON.stringify(key)+':'+metadataJSON(value[key],depth+1)).join(',')+'}';
  throw new TypeError('Raw metadata has unsupported JSON values.');
}
function options(input) {
  if(!object(input)||Object.keys(input).some(k=>!fields.has(k)))throw new TypeError('Invalid memory_source arguments.');
  const {record_id,start=null,end=null,section='overview',offset=0,limit=6000,snapshot_id=null}=input;
  if(!identity(record_id)||!slots.has(section)||(snapshot_id!==null&&!integer(snapshot_id)))
    throw new TypeError('Invalid memory_source record reference.');
  if((start===null)!==(end===null))throw new TypeError('Source span requires both start and end.');
  if(start===null&&!directSections.has(section))throw new UnavailableSourceSection();
  if(start===null&&(!integer(offset)||offset<0||!integer(limit)||limit<1||limit>12000))
    throw new RangeError('Offset must be nonnegative and limit between 1 and 12000 characters.');
  if(start!==null&&(!integer(start)||!integer(end)||start<0||start>=end||end-start>PAGE_CHARACTERS))
    throw new RangeError('Invalid source span or span exceeds 6000 characters.');
  return {record_id,start,end,section,offset,limit,snapshot_id};
}
function page(event,{record_id,section,snapshot_id,offset,limit}) {
  const plain=section==='text';
  const body=chars(plain?event.prompt:metadataJSON(event.metadata));
  if(offset>body.length)throw new RangeError('Offset is outside the requested section.');
  const base={record_id,section,snapshot_id};
  let end=Math.min(offset+limit,body.length);
  while(true) {
    const cursor=end<body.length?continuation({...base,offset:end,limit}):null;
    const result={...base,encoding:plain?'text':'json',offset,end,total_characters:body.length,item_count:null,
      content:body.slice(offset,end).join(''),response_complete:cursor===null,continuation:cursor};
    if(fits(result))return result;
    if(end<=offset+1)throw new RangeError('Section metadata exceeds the response budget.');
    end=offset+Math.floor((end-offset)/2);
  }
}
function span(event,{record_id,start,end}) {
  const body=chars(event.prompt);
  if(end>body.length)throw new RangeError('Source span is outside its source.');
  const requested_end=end;
  const descriptor={id:event.id,session_id:event.session_id,turn_id:event.turn_id,actor:event.actor,
    source:event.source,scope:event.scope,recorded_at:event.recorded_at,text_characters:body.length,
    metadata_fields:Object.keys(event.metadata).length,
    role:Object.hasOwn(event.metadata,'role')?event.metadata.role:(event.actor==='assistant'?'assistant':'user')};
  while(true) {
    const range={record_id,start,end,text:body.slice(start,end).join(''),source:event.source,actor:event.actor,event_id:event.id};
    const result={event:descriptor,span:range,response_complete:end===requested_end,
      source_text_complete:start===0&&end===body.length,requested_end,remaining_characters:requested_end-end,
      continuation:end<requested_end?continuation({record_id,start:end,end:requested_end}):null};
    if(fits(result))return result;
    if(end<=start+1)throw new RangeError('Source metadata exceeds the response budget.');
    end=start+Math.floor((end-start)/2);
  }
}

/**
 * Configuration comes solely from trusted server access control; callers may
 * supply only original tool arguments. The auth callback must verify this
 * invocation's owner and permission to disclose the exact requested scope,
 * raw ID and section. SQL independently restricts the row to that scope.
 */
export function createRawSourceReader(db,{authorizedScope,authorize}={}) {
  if(!db||!identity(authorizedScope)||['*','all','ALL'].includes(authorizedScope)||typeof authorize!=='function')
    throw new TypeError('One exact trusted scope and source authorizer are required.');
  return async function memory_source(input) {
    const args=options(input);
    const context=Object.freeze({record_id:args.record_id,scope:authorizedScope,
      section:args.start===null?args.section:'span'});
    if(await authorize(context)!==true)throw missing();
    const [query]=await db.batch([db.prepare(`SELECT raw_json FROM memory_raw_captures
      WHERE event_id=? AND json_extract(raw_json,'$.scope')=?
        AND length(CAST(raw_json AS BLOB))<=?`).bind(args.record_id,authorizedScope,MAX_RAW)]);
    const row=select(query);
    if(!row)throw missing();
    const event=JSON.parse(row.raw_json);
    if(!object(event)||event.id!==args.record_id||event.scope!==authorizedScope||
        ![event.id,event.session_id,event.turn_id,event.actor,event.source,event.scope,event.recorded_at,event.prompt].every(string)||
        !object(event.metadata))throw missing();
    return args.start===null?page(event,args):span(event,args);
  };
}
