// Internal canonical ThoughtService memory_history projection. It does not
// implement legacy view material/routes, or expose an MCP/HTTP route.
import {eventEligibility} from './source_evidence.mjs';
import {normalizeProducerInstant} from './capture_message.mjs';

const validSections=new Set(['overview','summary','evidence','edges','metadata','versions','routes','checks']);
const inputKeys=new Set(['view_id','version_id','section','offset','limit','snapshot_id','presentation_cutoff','name_watermark']);
const HISTORY_LIMIT=128, CLOSURE_LIMIT=512, EVIDENCE_LIMIT=2048;
const utf8=new TextEncoder();
const object=v=>v!==null&&typeof v==='object'&&Object.getPrototypeOf(v)===Object.prototype;
const count=v=>[...v].length;
const string=v=>typeof v==='string'&&v.isWellFormed();
const id=v=>string(v)&&v.length<=4096&&count(v)<=2048&&!!v.trim()&&!v.includes('\0');
const integer=v=>Number.isSafeInteger(v);
const missing=()=>new RangeError('Unknown thought identity in the authorized scope.');
const boundedError=()=>new RangeError('Thought history exceeds the bounded local reader.');
const chars=v=>[...v];
const compareUnicode=(a,b)=>{
  const aa=chars(a),bb=chars(b);
  for(let i=0;i<Math.min(aa.length,bb.length);i++) {
    const d=aa[i].codePointAt(0)-bb[i].codePointAt(0);
    if(d)return d;
  }
  return aa.length-bb.length;
};
const bind=(db,query,...params)=>db.prepare(query).bind(...params);
const rows=result=>{
  if(result?.success===false||!Array.isArray(result?.results))throw new Error('Canonical thought history storage is not available.');
  return result.results;
};

const closure=`WITH RECURSIVE family(maximum) AS (
  SELECT coalesce(nullif(?,0),max(version)) FROM memory_thought_versions WHERE thought_id=?
), chosen(thought_id,version) AS (
  SELECT thought_id,version FROM memory_thought_versions,family
  WHERE thought_id=? AND version<=family.maximum AND (? IS NULL OR version=?)
  ORDER BY version LIMIT ${HISTORY_LIMIT+1}
), exact_refs(thought_id,version) AS (
  SELECT thought_id,version FROM chosen
  UNION
  SELECT b.basis_thought_id,b.basis_thought_version
  FROM exact_refs r JOIN memory_thought_version_bases b
  ON r.thought_id=b.thought_id AND r.version=b.version
  WHERE b.basis_thought_id IS NOT NULL LIMIT ${CLOSURE_LIMIT+1}
), raw_edges(thought_id,version,event_id) AS (
  SELECT b.thought_id,b.version,b.basis_event_id FROM exact_refs r
  JOIN memory_thought_version_bases b ON r.thought_id=b.thought_id AND r.version=b.version
  WHERE b.basis_event_id IS NOT NULL
  UNION
  SELECT v.thought_id,v.version,json_extract(v.payload,'$.origin.event_id') FROM exact_refs r
  JOIN memory_thought_versions v ON r.thought_id=v.thought_id AND r.version=v.version
  WHERE json_type(v.payload,'$.origin.event_id') IS 'text' AND json_extract(v.payload,'$.origin.event_id')<>''
)`;
const key=(a,v)=>JSON.stringify([a,v]);

function parseOptions(input) {
  if(!object(input)||Object.keys(input).some(k=>!inputKeys.has(k)))throw new TypeError('Invalid memory_history arguments.');
  const {view_id,version_id=null,section='overview',offset=0,limit=6000,snapshot_id=null,
    presentation_cutoff=null,name_watermark=null}=input;
  if(!id(view_id)||!validSections.has(section)||(version_id!==null&&!integer(version_id))||
      (snapshot_id!==null&&!integer(snapshot_id))||(presentation_cutoff!==null&&!string(presentation_cutoff))||
      (name_watermark!==null&&!integer(name_watermark))||!integer(offset)||offset<0||!integer(limit)||limit<1||limit>12000)
    throw new TypeError('Invalid memory_history identity, pin or page.');
  return {view_id,version_id,section,offset,limit,snapshot_id};
}
function applies(proof,context) {
  if(!object(proof))throw new Error('Invalid saved question proof.');
  if(proof.context!=null) {
    return compactSorted(proof.context)===compactSorted(context??null);
  }
  const cutoff=context?.recorded_as_of;
  if(cutoff==null)return true;
  const time=Object.hasOwn(proof,'first_checked_at')?proof.first_checked_at:proof.checked_at;
  return time!=null && normalizeProducerInstant(time)<=normalizeProducerInstant(cutoff);
}
function compactSorted(v,level=0) {
  if(level>32)throw boundedError();
  if(v===null||string(v)||typeof v==='boolean'||integer(v)&&!Object.is(v,-0))return JSON.stringify(v);
  if(Array.isArray(v))return '['+v.map(x=>compactSorted(x,level+1)).join(',')+']';
  if(object(v))return '{'+Object.keys(v).sort(compareUnicode).map(k=>JSON.stringify(k)+':'+compactSorted(v[k],level+1)).join(',')+'}';
  throw new TypeError('Unexpected canonical saved JSON value.');
}
function nestedSorted(v,depth=0) {
  if(depth>32)throw boundedError();
  if(Array.isArray(v))return v.map(x=>nestedSorted(x,depth+1));
  if(object(v))return Object.fromEntries(Object.keys(v).sort(compareUnicode).map(k=>[k,nestedSorted(v[k],depth+1)]));
  return v;
}
function publicThought(thought,proofs,eligible) {
  const questions=new Set(eligible&&thought.question?[thought.question]:[]);
  if(eligible)for(const entry of proofs) {
    const witnesses=JSON.parse(entry.proofs);
    if(!Array.isArray(witnesses)||!string(entry.question))throw new Error('Invalid saved question proof.');
    if(!(witnesses.length? witnesses : [{context:null,checked_at:null}]).some(proof=>applies(proof,thought.metadata.selection_context)))continue;
    questions.add(entry.question);
  }
  return {
    id:thought.id,version:thought.version,text:thought.text,
    origin:nestedSorted(thought.origin),bases:nestedSorted(thought.bases),metadata:nestedSorted(thought.metadata),
    created_at:thought.created_at,questions:[...questions].sort(compareUnicode),
  };
}
function textPage(entries,args) {
  // Top-level Thought fields have Python dataclass order. Nested parts in
  // canonical decoded payloads have Python sorted JSON key order.
  const body=chars('['+entries.map(entry=>'{'+Object.keys(entry).map(k=>
    JSON.stringify(k)+':'+compactSorted(entry[k])).join(',')+'}').join(',')+']');
  const {view_id,version_id,section,snapshot_id,offset,limit}=args;
  if(offset>body.length)throw new RangeError('Offset is outside the requested section.');
  let end=Math.min(offset+limit,body.length);
  const fixed={view_id,section,version_id,snapshot_id};
  while(true) {
    const complete=end===body.length;
    const result={...fixed,encoding:'json',offset,end,total_characters:body.length,item_count:entries.length,
      content:body.slice(offset,end).join(''),response_complete:complete,
      continuation:complete?null:{tool:'memory_history',arguments:{...fixed,offset:end,limit}}};
    const pretty=JSON.stringify(result,null,2);
    const wrap={content:[{type:'text',text:pretty}],structuredContent:result,isError:false};
    if(count(JSON.stringify(wrap))+256<=28_000)return result;
    if(end<=offset+1)throw boundedError();
    end=offset+Math.floor((end-offset)/2);
  }
}

/** Requires the actual canonical applicability-proofs table (see Python
 * thought_questions.ensure_schema), not a fake empty-table fallback. An
 * approved scope may disclose exact history only where every version's
 * documentary ancestry remains wholly rooted in that same raw scope. */
export function createThoughtHistoryReader(db,{authorizedScope,authorize}={}) {
  if(!db||!id(authorizedScope)||['*','all','ALL'].includes(authorizedScope)||typeof authorize!=='function')
    throw new TypeError('One exact trusted scope and history authorizer are required.');
  return async function memory_history(input) {
    const args=parseOptions(input);
    if(await authorize(Object.freeze({view_id:args.view_id,scope:authorizedScope,section:args.section}))!==true)throw missing();
    const id=args.view_id;
    const binds=[args.snapshot_id,id,id,args.version_id,args.version_id];
    let result;
    try {
      result=await db.batch([
        bind(db,`SELECT max(version) AS max_version, EXISTS (
          SELECT 1 FROM memory_thought_versions v JOIN memory_raw_captures r ON r.event_id=json_extract(v.payload,'$.origin.event_id')
          WHERE v.thought_id=? AND json_extract(r.raw_json,'$.scope')=?
        ) AS scope_witness FROM memory_thought_versions WHERE thought_id=?`,id,authorizedScope,id),
        bind(db,`${closure} SELECT v.thought_id,v.version,
          CASE WHEN length(CAST(v.payload AS BLOB))<=262144
            AND sum(length(CAST(v.payload AS BLOB))) OVER ()<=1048576 THEN v.payload END AS payload
          FROM chosen c JOIN memory_thought_versions v ON c.thought_id=v.thought_id AND c.version=v.version
          ORDER BY v.version`,...binds),
        bind(db,`${closure} SELECT e.thought_id,e.version,e.event_id,
          json_extract(r.raw_json,'$.scope') AS scope,json_extract(r.raw_json,'$.source') AS source,
          json_extract(r.raw_json,'$.actor') AS actor,
          CASE WHEN json_extract(r.raw_json,'$.scope')=?
            AND sum(length(CAST(json_extract(r.raw_json,'$.metadata') AS BLOB))) OVER ()<=524288
            THEN json_extract(r.raw_json,'$.metadata') END AS metadata
          FROM raw_edges e LEFT JOIN memory_raw_captures r ON r.event_id=e.event_id
          ORDER BY e.thought_id,e.version,e.event_id LIMIT ${EVIDENCE_LIMIT+1}`,...binds,authorizedScope),
        bind(db,`${closure} SELECT v.thought_id,v.version,
          CASE WHEN sum(length(CAST(json_extract(v.payload,'$.origin') AS BLOB))) OVER ()<=524288
            THEN json_extract(v.payload,'$.origin') END AS origin, b.basis_thought_id,b.basis_thought_version
          FROM exact_refs e LEFT JOIN memory_thought_versions v ON e.thought_id=v.thought_id AND e.version=v.version
          LEFT JOIN memory_thought_version_bases b ON e.thought_id=b.thought_id AND e.version=b.version
            AND b.basis_thought_id IS NOT NULL
          ORDER BY e.thought_id,e.version,b.basis_index LIMIT ${EVIDENCE_LIMIT+1}`,...binds),
        bind(db,`${closure} SELECT q.thought_id,q.version,q.question,
          CASE WHEN sum(length(CAST(q.proofs AS BLOB))) OVER ()<=524288 THEN q.proofs END AS proofs
          FROM chosen c JOIN memory_thought_reuse_checks q ON c.thought_id=q.thought_id AND c.version=q.version
          ORDER BY q.version,q.question LIMIT ${EVIDENCE_LIMIT+1}`,...binds),
      ]);
    } catch { throw new Error('Canonical thought history and question proof schema are unavailable.'); }
    const [[family],history,raw,edges,proofs]=result.map(rows);
    if(family?.max_version==null)throw missing();
    if(history.length>HISTORY_LIMIT||raw.length>EVIDENCE_LIMIT||edges.length>EVIDENCE_LIMIT||proofs.length>EVIDENCE_LIMIT||
      history.some(row=>row.payload===null)||proofs.some(row=>row.proofs===null))throw boundedError();
    const maximum=args.snapshot_id||family.max_version;
    // With empty selection the original still returns []; enforce a live
    // authorized family witness rather than reveal a mismatched ID's existence.
    if(!history.length) {
      if(family.scope_witness!==1)throw missing();
      return textPage([],{...args,snapshot_id:maximum});
    }

    const objects=new Map(), sourceByNode=new Map(), proofsByNode=new Map();
    for(const edge of edges) {
      if(edge.thought_id===null || edge.origin===null)throw missing();
      const k=key(edge.thought_id,edge.version);
      if(!objects.has(k))objects.set(k,{origin:JSON.parse(edge.origin),children:[]});
      if(edge.basis_thought_id!==null)objects.get(k).children.push(key(edge.basis_thought_id,edge.basis_thought_version));
    }
    if(objects.size>CLOSURE_LIMIT)throw boundedError();
    for(const source of raw) {
      const k=key(source.thought_id,source.version);
      if(source.scope!==authorizedScope || source.metadata===null)throw missing();
      const eligible=eventEligibility({source:source.source,actor:source.actor,metadata:JSON.parse(source.metadata)}).allowed;
      if(!sourceByNode.has(k))sourceByNode.set(k,[]);
      sourceByNode.get(k).push(eligible);
    }
    for(const proof of proofs) {
      const k=key(proof.thought_id,proof.version);
      if(!proofsByNode.has(k))proofsByNode.set(k,[]);
      proofsByNode.get(k).push(proof);
    }
    const visited=new Map(), opened=new Set();
    function lineage(k) {
      if(visited.has(k))return visited.get(k);
      if(opened.has(k)||!objects.has(k))throw missing();
      opened.add(k);
      const {origin,children}=objects.get(k);
      if(origin.scope!=null&&origin.scope!==authorizedScope)throw missing();
      const sources=sourceByNode.get(k)??[];
      const dependants=children.map(lineage);
      const grounded=sources.length>0||dependants.some(value=>value.grounded);
      const eligible=eventEligibility(origin).allowed&&sources.every(Boolean)&&dependants.every(value=>value.eligible);
      const state={grounded,eligible};
      visited.set(k,state);opened.delete(k);return state;
    }
    let bytes=0;
    const out=history.map(row=>{
      bytes+=utf8.encode(row.payload).length;
      if(bytes>1024*1024)throw boundedError();
      const k=key(row.thought_id,row.version), policy=lineage(k);
      if(!policy.grounded||!policy.eligible)throw missing();
      const thought=JSON.parse(row.payload);
      return publicThought(thought,proofsByNode.get(k)??[],true);
    });
    return textPage(out,{...args,snapshot_id:maximum});
  };
}
