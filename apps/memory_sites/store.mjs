// One owner-private, approved read snapshot per D1 database. No engine state,
// raw source text, credentials, or arbitrary SQL are accepted by this port.
export const SNAPSHOT_SCHEMA = 'graph-viewer-source/v1';
export const SNAPSHOT_BYTE_LIMIT = 768 * 1024;
const COUNT_LIMITS = { nodes: 3000, edges: 6000, overviews: 1200, topic_links: 2400 };
const KINDS = new Set([null, 'assertion', 'decision', 'observation', 'implementation',
  'inference', 'proposal', 'requirement', 'plan', 'question', 'fact']);
const PRECISIONS = new Set(['unknown', 'instant', 'day', 'month', 'year']);
const RELATIONS = new Set(['subquestion', 'prerequisite', 'related']);
const PRIVATE_NODE_FIELDS = ['actor', 'source', 'episode_id'];
const PRIVATE_EDGE_FIELDS = ['source_quote', 'target_quote', 'condition', 'basis'];

export class SnapshotError extends Error {
  constructor(location, error) {
    super(location + ': ' + error);
    this.name = 'SnapshotError';
    this.status = 400;
  }
}
const invalid = (path, reason) => { throw new SnapshotError(path, reason); };
function object(value, path, allowed) {
  if (!value || Array.isArray(value) || typeof value !== 'object' ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value)))
    invalid(path, 'expected an object');
  if (Object.keys(value).some((key) => !allowed.includes(key))) invalid(path, 'unsupported field');
  return value;
}
function string(value, path, {nullable=false, allowEmpty=false, limit=512} = {}) {
  if (value == null && nullable) return null;
  if (typeof value !== 'string' || (!allowEmpty && !value.trim()) ||
      value.length > limit || value.includes('\0') || !value.isWellFormed())
    invalid(path, 'invalid string');
  return value;
}
function array(value, path, max) {
  if (!Array.isArray(value) || value.length > max) invalid(path, 'invalid or oversized array');
  return value;
}
function instant(value, path, {nullable=false, dateAllowed=false} = {}) {
  if (value == null && nullable) return null;
  string(value, path, {limit: 64});
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?(Z|[+-](\d{2}):(\d{2})))?$/.exec(value);
  if (!m || (!dateAllowed && !m[4])) invalid(path, 'expected an ISO-8601 instant with timezone');
  const [, y, mo, d, h, mi, s, , oh, om] = m;
  const n = +y, month = +mo;
  const days = [31, n % 4 === 0 && (n % 100 !== 0 || n % 400 === 0) ? 29 : 28,
    31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (n < 1 || month < 1 || month > 12 || +d < 1 || +d > days[month - 1] ||
      +h > 23 || +mi > 59 || +s > 59 || +oh > 23 || +om > 59)
    invalid(path, 'invalid calendar date or time');
  return value;
}
function byId(arrayValue, path, limit, transform, idField='id') {
  const known = new Set();
  const out = array(arrayValue, path, limit).map((raw, index) => {
    const p = path + '[' + index + ']';
    const row = transform(raw, p);
    const id = row[idField];
    if (known.has(id)) invalid(p, 'duplicate stable ID');
    known.add(id);
    return row;
  });
  return [out, known];
}
function reference(value, path, known) {
  const id = string(value, path);
  if (!known.has(id)) invalid(path, 'unknown stable ID');
  return id;
}
function maybe(out, raw, field, read) {
  if (Object.hasOwn(raw, field)) out[field] = read(raw[field]);
}
function requireRedacted(raw, fields, path) {
  for (const k of fields) if (raw[k] != null && raw[k] !== '') invalid(path + '.' + k, 'private metadata must be omitted');
}
function requireEmpty(raw, field, path) {
  if (Object.hasOwn(raw, field)) array(raw[field], path + '.' + field, 0);
}
export function validateSnapshot(input) {
  let bytes;
  try { bytes = new TextEncoder().encode(JSON.stringify(input)).byteLength; }
  catch { invalid('snapshot', 'expected JSON data'); }
  if (bytes > SNAPSHOT_BYTE_LIMIT) invalid('snapshot', 'input exceeds 768 KiB');
  const inputObj = object(input, 'snapshot',
    ['schema', 'provider_id', 'source_revision', 'nodes', 'edges', 'episodes', 'overviews', 'topic_links']);
  if (inputObj.schema !== SNAPSHOT_SCHEMA) invalid('schema', 'unsupported snapshot schema');
  const provider_id = string(inputObj.provider_id, 'provider_id');
  const source_revision = string(inputObj.source_revision, 'source_revision');
  // Raw conversations never cross this first-stage port, even when linked.
  array(inputObj.episodes, 'episodes', 0);
  const [nodes, nodeIds] = byId(inputObj.nodes, 'nodes', COUNT_LIMITS.nodes, (raw, p) => {
    object(raw, p, ['id','text','kind','recorded_at','valid_at','valid_precision','scope',
      'source_recorded_at','source_available_at','interpreted_at','context_truncated','published_status',
      'context_episode_ids','evidence_refs', ...PRIVATE_NODE_FIELDS]);
    requireRedacted(raw, PRIVATE_NODE_FIELDS, p);
    requireEmpty(raw, 'context_episode_ids', p);
    requireEmpty(raw, 'evidence_refs', p);
    const kind = raw.kind ?? null;
    if (!KINDS.has(kind)) invalid(p + '.kind', 'unknown node kind');
    const valid_at = instant(raw.valid_at, p + '.valid_at', {nullable:true, dateAllowed:true});
    const valid_precision = raw.valid_precision ?? 'unknown';
    if (!PRECISIONS.has(valid_precision) || (valid_at === null && valid_precision !== 'unknown'))
      invalid(p + '.valid_precision', 'invalid precision');
    const node = {id:string(raw.id,p+'.id'),text:string(raw.text,p+'.text',{limit:65536}),kind,
      scope:string(raw.scope,p+'.scope',{nullable:true}),recorded_at:instant(raw.recorded_at,p+'.recorded_at'),
      valid_at,valid_precision};
    for (const k of ['source_recorded_at','source_available_at','interpreted_at'])
      maybe(node, raw, k, (v) => instant(v, p + '.' + k, {nullable:true}));
    maybe(node, raw, 'context_truncated', (v) => {
      if (v !== null && typeof v !== 'boolean') invalid(p+'.context_truncated','expected boolean or null');
      return v;
    });
    maybe(node, raw, 'published_status', (v) => {
      object(v,p+'.published_status',['published','disposition']);
      if (v.published !== true || v.disposition !== 'cancelled') invalid(p+'.published_status','only explicit cancellation is supported');
      return {published:true,disposition:'cancelled'};
    });
    return node;
  });
  if (!nodes.length) invalid('nodes', 'approved snapshot must include a node');
  const nodeScopes = new Map(nodes.map((n)=>[n.id,n.scope]));
  const [edges] = byId(inputObj.edges, 'edges', COUNT_LIMITS.edges, (raw, p) => {
    object(raw,p,['id','from_id','to_id','kind','recorded_at','effective_at', ...PRIVATE_EDGE_FIELDS]);
    requireRedacted(raw, PRIVATE_EDGE_FIELDS, p);
    const edge = {id:string(raw.id,p+'.id'),from_id:reference(raw.from_id,p+'.from_id',nodeIds),
      to_id:reference(raw.to_id,p+'.to_id',nodeIds),kind:string(raw.kind,p+'.kind'),
      recorded_at:instant(raw.recorded_at,p+'.recorded_at')};
    if (edge.kind === 'supersedes' && (!nodeScopes.get(edge.from_id) || !nodeScopes.get(edge.to_id)))
      invalid(p+'.kind','supersedes requires explicit node scopes');
    maybe(edge,raw,'effective_at',(v)=>instant(v,p+'.effective_at',{nullable:true,dateAllowed:true}));
    return edge;
  });
  const [overviews, overviewIds] = byId(inputObj.overviews, 'overviews', COUNT_LIMITS.overviews, (raw,p)=>{
    object(raw,p,['id','title','question','scopes','versions','current_version_id']);
    const [versions, versionIds] = byId(raw.versions,p+'.versions',100,(v,vp)=>{
      object(v,vp,['version_id','published_at','summary','member_ids','valid_cutoff','recorded_cutoff']);
      const member_ids = array(v.member_ids,vp+'.member_ids',COUNT_LIMITS.nodes).map((id,i)=>reference(id,vp+'.member_ids['+i+']',nodeIds));
      if (new Set(member_ids).size !== member_ids.length) invalid(vp+'.member_ids','duplicate stable ID');
      const version={version_id:string(v.version_id,vp+'.version_id'),published_at:instant(v.published_at,vp+'.published_at'),member_ids};
      maybe(version,v,'summary',(s)=>string(s,vp+'.summary',{allowEmpty:true,nullable:true,limit:65536}));
      for (const k of ['valid_cutoff','recorded_cutoff']) maybe(version,v,k,(t)=>instant(t,vp+'.'+k,{nullable:true}));
      return version;
    },'version_id');
    const overview = {id:string(raw.id,p+'.id'),title:string(raw.title,p+'.title',{limit:4096}),
      scopes:array(raw.scopes??[],p+'.scopes',100).map((s,i)=>string(s,p+'.scopes['+i+']')),
      versions,current_version_id:reference(raw.current_version_id,p+'.current_version_id',versionIds)};
    maybe(overview,raw,'question',(s)=>string(s,p+'.question',{nullable:true,limit:4096}));
    return overview;
  });
  const [topic_links] = byId(inputObj.topic_links??[], 'topic_links', COUNT_LIMITS.topic_links, (raw,p)=>{
    object(raw,p,['id','parent_id','child_id','relation','recorded_at','provenance']);
    if (!RELATIONS.has(raw.relation)) invalid(p+'.relation','unsupported topic relation');
    object(raw.provenance,p+'.provenance',['kind']);
    if (raw.provenance.kind !== 'approved_snapshot') invalid(p+'.provenance','non-sanitized provenance');
    return {id:string(raw.id,p+'.id'),parent_id:reference(raw.parent_id,p+'.parent_id',overviewIds),
      child_id:reference(raw.child_id,p+'.child_id',overviewIds),relation:raw.relation,
      recorded_at:instant(raw.recorded_at,p+'.recorded_at'),provenance:{kind:'approved_snapshot'}};
  });
  const next = new Map(overviews.map(({id})=>[id,[]]));
  const indegree = new Map(overviews.map(({id})=>[id,0]));
  for (const row of topic_links) if (row.relation === 'subquestion') {
    next.get(row.parent_id).push(row.child_id);
    indegree.set(row.child_id,indegree.get(row.child_id)+1);
  }
  const queue = [...overviewIds].filter((id)=>indegree.get(id)===0);
  for (let i=0; i<queue.length; i++) for (const child of next.get(queue[i])) {
    indegree.set(child,indegree.get(child)-1);
    if (indegree.get(child)===0) queue.push(child);
  }
  if (queue.length !== overviews.length) invalid('topic_links','subquestion cycle');
  return {schema:SNAPSHOT_SCHEMA,provider_id,source_revision,nodes,edges,episodes:[],overviews,topic_links};
}

const prepare = (db, sql, ...values) => {
  const statement = db.prepare(sql);
  return values.length ? statement.bind(...values) : statement;
};
const rows = (result) => {
  if (result?.success === false || !Array.isArray(result?.results)) throw new Error('Graph storage read failed.');
  return result.results;
};
const contents = (result) => rows(result).map((r)=>JSON.parse(r.payload));
function page(options, defaultLimit, limitMax) {
  const value = options?.limit === undefined ? defaultLimit : options.limit;
  const offset = options?.offset === undefined ? 0 : options.offset;
  if (!Number.isSafeInteger(value) || value < 1 || value > limitMax) invalid('limit','out of range');
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 10000) invalid('offset','out of range');
  return {limit:value,offset};
}

export async function importSnapshot(db, snapshot) {
  // Normalize and validate every record before the first DB call. D1 executes
  // one batch transaction and rolls back the replacement on any SQL failure.
  const {provider_id, source_revision, nodes, edges, overviews, topic_links} = validateSnapshot(snapshot);
  const stmt = (sql, ...values) => prepare(db, sql, ...values);
  const n = JSON.stringify(nodes), e = JSON.stringify(edges), o = JSON.stringify(overviews), t = JSON.stringify(topic_links);
  if ([n,e,o,t].some((value)=>new TextEncoder().encode(value).byteLength > SNAPSHOT_BYTE_LIMIT))
    invalid('snapshot', 'normalized table exceeds 768 KiB');
  const result = await db.batch([
    stmt('DELETE FROM memory_graph_topic_links'),
    stmt('DELETE FROM memory_graph_edges'),
    stmt('DELETE FROM memory_graph_nodes'),
    stmt('DELETE FROM memory_graph_overviews'),
    stmt("INSERT INTO memory_graph_nodes(id,payload) SELECT json_extract(value,'$.id'), value FROM json_each(?)",n),
    stmt("INSERT INTO memory_graph_edges(id,from_id,to_id,payload) SELECT json_extract(value,'$.id'), json_extract(value,'$.from_id'), json_extract(value,'$.to_id'), value FROM json_each(?)",e),
    stmt("INSERT INTO memory_graph_overviews(id,payload) SELECT json_extract(value,'$.id'),value FROM json_each(?)",o),
    stmt("INSERT INTO memory_graph_topic_links(id,parent_id,child_id,payload) SELECT json_extract(value,'$.id'), json_extract(value,'$.parent_id'), json_extract(value,'$.child_id'),value FROM json_each(?)",t),
    stmt('INSERT INTO memory_graph_meta(singleton,provider_id,source_revision) VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET provider_id=excluded.provider_id,source_revision=excluded.source_revision',provider_id,source_revision),
  ]);
  if (!Array.isArray(result) || result.some((entry)=>entry?.success === false))
    throw new Error('Graph storage transaction failed.');
  return {provider_id,source_revision,nodes:nodes.length,edges:edges.length,overviews:overviews.length,topic_links:topic_links.length};
}

export async function getGraph(db, options={}) {
  const {limit,offset}=page(options,150,500);
  const edgeLimit=options.edgeLimit??1200;
  if (!Number.isSafeInteger(edgeLimit) || edgeLimit<1 || edgeLimit>2500) invalid('edgeLimit','out of range');
  const [metaResult,nodeResult,edgeResult]=await db.batch([
    prepare(db,'SELECT provider_id,source_revision FROM memory_graph_meta WHERE singleton=1'),
    prepare(db,'SELECT payload FROM memory_graph_nodes ORDER BY id LIMIT ? OFFSET ?',limit+1,offset),
    prepare(db,`WITH selection AS (SELECT id FROM memory_graph_nodes ORDER BY id LIMIT ? OFFSET ?)
      SELECT e.payload FROM memory_graph_edges e
      JOIN selection f ON f.id=e.from_id JOIN selection t ON t.id=e.to_id ORDER BY e.id LIMIT ?`,limit,offset,edgeLimit+1),
  ]);
  const meta=rows(metaResult)[0]??{provider_id:null,source_revision:null};
  const nodes=contents(nodeResult),edges=contents(edgeResult);
  return {schema:SNAPSHOT_SCHEMA,...meta,revision:meta.source_revision,nodes:nodes.slice(0,limit),
    edges:edges.slice(0,edgeLimit),truncated:nodes.length>limit||edges.length>edgeLimit,
    next_offset:nodes.length>limit?offset+limit:null};
}
export async function getNode(db,id) {
  string(id,'id');
  const [nodeResult,edgeResult]=await db.batch([
    prepare(db,'SELECT payload FROM memory_graph_nodes WHERE id=?',id),
    prepare(db,'SELECT payload FROM memory_graph_edges WHERE from_id=? OR to_id=? ORDER BY id LIMIT 201',id,id),
  ]);
  const node=contents(nodeResult)[0];
  if (!node) return null;
  const edges=contents(edgeResult);
  return {node,edges:edges.slice(0,200),truncated:edges.length>200};
}
export async function listOverviews(db,options={}) {
  const {limit,offset}=page(options,25,100);
  const [overviewResult,linksResult]=await db.batch([
    prepare(db,'SELECT payload FROM memory_graph_overviews ORDER BY id LIMIT ? OFFSET ?',limit+1,offset),
    prepare(db,`WITH selection AS (SELECT id FROM memory_graph_overviews ORDER BY id LIMIT ? OFFSET ?)
      SELECT e.payload FROM memory_graph_topic_links e
      JOIN selection p ON p.id=e.parent_id JOIN selection c ON c.id=e.child_id ORDER BY e.id LIMIT 501`,limit,offset),
  ]);
  const overviews=contents(overviewResult),topic_links=contents(linksResult);
  return {overviews:overviews.slice(0,limit),topic_links:topic_links.slice(0,500),
    truncated:overviews.length>limit||topic_links.length>500,
    next_offset:overviews.length>limit?offset+limit:null};
}
