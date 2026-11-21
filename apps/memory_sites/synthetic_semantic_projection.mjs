// Internal read-only projection for a dedicated synthetic preview. This is not
// a canonical graph export or authorization mechanism for any real dataset.
import {SNAPSHOT_SCHEMA,validateSnapshot} from './store.mjs';

export const SYNTHETIC_SEMANTIC_SCOPE='synthetic-assertion-permanent-v2';
const PURPOSE='private-synthetic-assertion-smoke/v1';
const PROVIDER='synthetic-permanent-thoughts';
const maxHeads=64, maxBases=512, maxLinks=512;
const text=new TextEncoder();
const sort=(a,b)=>a<b?-1:a>b?1:0;
const key=(id,version)=>JSON.stringify([id,version]);
const safeUri=s=>encodeURIComponent(s).replace(/[!'()*]/g,k=>'%'+k.charCodeAt(0).toString(16).toUpperCase());
const nodeId=(id,version)=>'thought-version:'+safeUri(id)+':'+version;

// Canonical hashes here are over narrow graph objects and pinned thought
// basis objects (strings + nonnegative integers/null). Python orders Unicode
// keys by code point; current allowed field keys are ASCII.
function compact(value) {
  if(value===null||typeof value==='string'&&value.isWellFormed()||Number.isSafeInteger(value)) return JSON.stringify(value);
  if(Array.isArray(value)) return '['+value.map(compact).join(',')+']';
  if(value && Object.getPrototypeOf(value)===Object.prototype) {
    return '{'+Object.keys(value).sort(sort).map(k=>JSON.stringify(k)+':'+compact(value[k])).join(',')+'}';
  }
  throw new TypeError('Unexpected synthetic graph evidence representation.');
}
async function hash(value) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',text.encode(compact(value)))),
    byte=>byte.toString(16).padStart(2,'0')).join('');
}
const valid=results=>{
  if(results?.success===false || !Array.isArray(results?.results)) throw new Error('Synthetic graph snapshot unavailable.');
  return results.results;
};
const marker="EXISTS (SELECT 1 FROM memory_synthetic_smoke_environment WHERE singleton=1 AND purpose='"+PURPOSE+"')";
const fixtureCheck=alias=>`json_extract(${alias}.raw_json,'$.scope')=?
  AND json_extract(${alias}.raw_json,'$.source')='fixture://permanent/v2/accepted'
  AND ${alias}.session_id='synthetic-assertion-permanent-v2-accepted'
  AND ${alias}.message_id='fixture-1'
  AND json_extract(${alias}.raw_json,'$.prompt')='The imaginary greenhouse has a blue door.'
  AND json_extract(${alias}.raw_json,'$.metadata.role') IN ('user','assistant')
  AND coalesce(json_extract(${alias}.raw_json,'$.metadata.channel'),'') NOT IN ('analysis','reasoning')
  AND coalesce(json_extract(${alias}.raw_json,'$.metadata.content_type'),'')<>'reasoning'`;

// Selection is deliberately narrower than the original: require exact
// attached synthetic raw event origin and omit historical versions. SQL does
// not transfer raw prompts, raw metadata, questions or full thought payloads.
const candidates=`WITH fixture_heads AS (
  SELECT v.thought_id,v.version,v.payload,v.content_hash FROM memory_thoughts h
  JOIN memory_thought_versions v ON v.thought_id=h.id AND v.version=h.current_version
  JOIN memory_raw_captures r ON r.event_id=json_extract(v.payload,'$.origin.event_id')
  WHERE ${marker} AND ${fixtureCheck('r')}
    AND (json_type(v.payload,'$.origin.scope') IS NULL OR json_extract(v.payload,'$.origin.scope')=?)
    AND json_type(v.payload,'$.metadata.selection_context') IS NULL
  ORDER BY v.thought_id,v.version LIMIT ${maxHeads+1}
)`;

export async function readSyntheticSemanticGraphEvidence(db) {
  const scope=SYNTHETIC_SEMANTIC_SCOPE;
  const statements=[
    db.prepare(`SELECT 1 ok WHERE ${marker}`),
    db.prepare(`${candidates} SELECT thought_id,version,
      CASE WHEN length(CAST(json_extract(payload,'$.text') AS BLOB))<=16384
           THEN json_extract(payload,'$.text') ELSE NULL END AS text,
      json_extract(payload,'$.created_at') AS created_at,
      json_extract(payload,'$.origin.scope') AS semantic_scope,content_hash
      FROM fixture_heads ORDER BY thought_id,version`).bind(scope,scope),
    db.prepare(`${candidates} SELECT b.thought_id,b.version,b.basis_index,b.basis_thought_id,b.basis_thought_version,
      CASE WHEN b.basis_event_id IS NULL THEN NULL ELSE EXISTS (
        SELECT 1 FROM memory_raw_captures r WHERE r.event_id=b.basis_event_id AND ${fixtureCheck('r')}
      ) END AS fixture_raw,
      CASE WHEN b.basis_thought_id IS NOT NULL
          AND length(CAST(json_extract(f.payload,'$.bases['||b.basis_index||']') AS BLOB))<=4096
        THEN json_extract(f.payload,'$.bases['||b.basis_index||']') ELSE NULL END AS basis
      FROM fixture_heads f JOIN memory_thought_version_bases b ON b.thought_id=f.thought_id AND b.version=f.version
      ORDER BY b.thought_id,b.version,b.basis_index LIMIT ${maxBases+1}`).bind(scope,scope,scope),
    db.prepare(`${candidates} SELECT l.id,l.source_id,l.source_version,l.target_id,l.target_version,
      json_extract(l.payload,'$.created_at') AS created_at
      FROM memory_thought_links l
      JOIN fixture_heads s ON s.thought_id=l.source_id AND s.version=l.source_version
      JOIN fixture_heads t ON t.thought_id=l.target_id AND t.version=l.target_version
      ORDER BY l.id LIMIT ${maxLinks+1}`).bind(scope,scope),
  ];
  let result;
  try { result=await db.batch(statements); }
  catch { throw new Error('Dedicated synthetic semantic preview database is unavailable.'); }
  if(valid(result[0])[0]?.ok!==1) throw new Error('Dedicated synthetic semantic preview database is unavailable.');
  const [heads,bases,links]=result.slice(1).map(valid);
  if(heads.length>maxHeads||bases.length>maxBases||links.length>maxLinks||
      heads.some(row=>row.text===null)||bases.some(row=>row.basis_thought_id!==null&&row.basis===null)) throw new Error('Synthetic semantic preview exceeds its bounded read limits.');
  const byKey=new Map(heads.map(row=>[key(row.thought_id,row.version),row]));
  const tainted=new Set();
  const parents=new Map();
  for(const basis of bases) {
    const from=key(basis.thought_id,basis.version);
    if(basis.basis_thought_id!==null) {
      const to=key(basis.basis_thought_id,basis.basis_thought_version);
      if(!byKey.has(to)) tainted.add(from);
      else if(parents.has(to)) parents.get(to).push(from);
      else parents.set(to,[from]);
    } else if(basis.fixture_raw!==1) tainted.add(from);
  }
  // Fixed point: no visible dependent may ground itself in an omitted basis.
  for(const bad of tainted) for(const parent of parents.get(bad)??[]) tainted.add(parent);
  const included=(id,v)=>byKey.has(key(id,v))&&!tainted.has(key(id,v));
  const nodes=heads.filter(row=>included(row.thought_id,row.version)).map(row=>({
    id:nodeId(row.thought_id,row.version),text:row.text,kind:null,scope:row.semantic_scope,recorded_at:row.created_at,
    valid_at:null,valid_precision:'unknown',
  })).sort((a,b)=>sort(a.id,b.id));
  if(!nodes.length) return {document:null,versions:[]};

  const derivations=await Promise.all(bases.filter(row=>row.basis_thought_id!==null&&
    included(row.thought_id,row.version)&&included(row.basis_thought_id,row.basis_thought_version)).map(async row=>({
    id:'thought-basis:'+await hash([[row.thought_id,row.version],row.basis_index,JSON.parse(row.basis)]),
    from_id:nodeId(row.thought_id,row.version),to_id:nodeId(row.basis_thought_id,row.basis_thought_version),
    kind:'thought_basis',recorded_at:byKey.get(key(row.thought_id,row.version)).created_at,
  })));
  const relations=links.filter(row=>row.created_at!==null&&
    included(row.source_id,row.source_version)&&included(row.target_id,row.target_version)).map(row=>({
    id:'thought-link:'+row.id,from_id:nodeId(row.source_id,row.source_version),to_id:nodeId(row.target_id,row.target_version),
    kind:'thought_relation',recorded_at:row.created_at,
  }));
  const edges=[...derivations,...relations].sort((a,b)=>sort(a.id,b.id));
  const document={schema:SNAPSHOT_SCHEMA,provider_id:PROVIDER,nodes,edges,episodes:[],overviews:[],topic_links:[]};
  const versions=heads.filter(row=>included(row.thought_id,row.version)).map(row=>({
    id:nodeId(row.thought_id,row.version),thought_ref:{thought_id:row.thought_id,version:row.version},
    content_hash:row.content_hash,recorded_at:row.created_at,
  })).sort((a,b)=>sort(a.id,b.id));
  return {document:validateSnapshot({...document,source_revision:'sha256:'+await hash(document)}),versions};
}

export async function readSyntheticSemanticProjection(db) {
  return (await readSyntheticSemanticGraphEvidence(db)).document;
}
