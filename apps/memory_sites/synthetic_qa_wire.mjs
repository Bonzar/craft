// Read-only, one-node preview, bound to the exact verified synthetic v2 commit.
import {readSyntheticSemanticGraphEvidence} from './synthetic_semantic_projection.mjs';
import {getSyntheticPermanentSmoke} from './synthetic_permanent_smoke.mjs';
import {encodeQABuffers,qaFormats} from './qa_binary.mjs';

const policy='synthetic-qa-single-thought/v1';
const encoder=new TextEncoder();
const acceptedText='The imaginary greenhouse has a blue door.';
const acceptedScope='synthetic-assertion-permanent-v2';
const idPattern=/^thought-[a-f0-9]{32}$/;
const hexPattern=/^[a-f0-9]{64}$/;
const hash=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))),
  byte=>byte.toString(16).padStart(2,'0')).join('');

export class SyntheticQaWireError extends Error {
  constructor(status,code) {
    super(status===409?'Snapshot revision is unavailable; reload the graph.':'Verified synthetic graph is unavailable.');
    this.status=status;
    this.code=code;
  }
}
const unavailable=()=>{throw new SyntheticQaWireError(503,'synthetic_graph_unavailable');};
// Compatibility return for the existing frozen helper; the actual site must still
// serve its identity bytes through rawTransport to avoid Cloudflare double gzip.
async function artifact(identity) {
  const stream=new Blob([identity]).stream().pipeThrough(new CompressionStream('gzip'));
  return {identity,gzip:new Uint8Array(await new Response(stream).arrayBuffer())};
}

export async function readSyntheticQaWire(db) {
  let evidence, proof;
  try {
    proof=await getSyntheticPermanentSmoke(db);
    if(!proof.ok||proof.phase!=='verified'||!proof.accepted?.permanent||proof.rejected?.permanent!==false)
      return unavailable();
    evidence=await readSyntheticSemanticGraphEvidence(db);
  } catch { return unavailable(); }
  const {document,versions}=evidence;
  const accepted=proof.accepted;
  if(!document||document.schema!=='graph-viewer-source/v1'||
    document.provider_id!=='synthetic-permanent-thoughts'||
    !/^sha256:[a-f0-9]{64}$/.test(document.source_revision)||
    document.nodes.length!==1||versions.length!==1||
    [document.edges,document.episodes,document.overviews,document.topic_links].some(v=>v.length!==0)||
    accepted.scope!==acceptedScope||accepted.text!==acceptedText||accepted.version!==1||
    !idPattern.test(accepted.thought_id)||!hexPattern.test(accepted.content_hash)) return unavailable();
  const [node]=document.nodes;
  const [row]=versions;
  const id='thought-version:'+accepted.thought_id+':1';
  if(node.id!==id||row.id!==id||row.thought_ref.thought_id!==accepted.thought_id||row.thought_ref.version!==1||
    row.content_hash!==accepted.content_hash||node.text!==acceptedText||node.kind!==null||node.scope!==null||
    node.valid_at!==null||node.valid_precision!=='unknown'||node.recorded_at!==row.recorded_at||
    !Number.isFinite(Date.parse(node.recorded_at))) return unavailable();

  const provider_id=document.provider_id;
  const source_revision='sha256:'+await hash(JSON.stringify([policy,document.source_revision,row.content_hash]));
  const revision=(await hash(JSON.stringify([policy,provider_id,source_revision]))).slice(0,30);
  const captured_at=node.recorded_at; // pinned fixture time, never request wall time
  const base='/api/v1/graph/'+revision;
  const nodeMeta={url:base+'/nodes.bin',...qaFormats.nodes,byte_length:16};
  const edgeMeta={url:base+'/edges.bin',...qaFormats.edges,byte_length:0};
  const manifest={
    schema:1,revision,source_revision,provider_id,captured_at,
    selector:{mode:'current',at:null,history:false,undated:'separate'},
    counts:{undated_known_count:1,excluded_unknown_valid_count:0,future_valid_known_count:0,
      nodes:1,edges:0,visible_nodes:1,visible_edges:0,eligible_before_status_filter:1,topics:0},
    nodes:nodeMeta,edges:edgeMeta,topics_url:base+'/topics',
    bounds:{min:[29.999,29.999,29.999],max:[30.001,30.001,30.001]},
    layout:{version:'source-component-bins-v3',semantic:false,stable:true,extent:650},
    complete_graph:true,readiness:[],live_cursor:'0',status_at:captured_at,knowledge_cutoff:captured_at,
  };
  const topics={
    revision,topics:[],memberships:[],links:[],completeness:'complete',unknowns:[],
    presentation_snapshot:{presentation_cutoff:captured_at,name_watermark:0},
  };
  const capabilities={
    provider_id,
    capabilities:{full_graph:true,topics:true,topic_hierarchy:false,source_context:false,whole_saved_source:false,
      keyword_search:false,semantic_search:false,history:false,live:false,session_filter:false},
    readiness:[{code:'synthetic_fixture',message:'One verified synthetic thought from the preview database.'}],
  };
  const card={
    id,ordinal:0,revision,text_revision:await hash(node.text),text:node.text,text_offset:0,
    text_total:[...node.text].length,next_text_offset:null,entity_type:'thought',
    thought_ref:{...row.thought_ref},is_current:true,label:node.text,full_title:node.text,
    actor:null,source:null,episode:null,scope:null,
    time:{valid_at:null,valid_precision:'unknown',recorded_at:node.recorded_at,
      source_recorded_at:null,source_available_at:null,interpreted_at:null},
    status:{disposition:'assertion',unresolved_conflict:false,replacements:[],basis_edge_ids:[],
      effective_at:null,effectiveness:'unknown'},
    sources:[],topic_ids:[],
    incident_edges:{revision,node_id:id,visible_only:true,total:0,items:[],next_cursor:null,visible_total:0,url:''},
    unknowns:['saved_source_reference_unknown'],
  };
  const buffers=encodeQABuffers([{ordinal:0,position:[30,30,30],flags:0}],[]);
  const [nodes,edges]=await Promise.all([artifact(buffers.nodes),artifact(buffers.edges)]);
  return {manifest,capabilities,cards:[{kind:'nodes',card}],topics,artifacts:{'nodes.bin':nodes,'edges.bin':edges}};
}

export async function loadSyntheticQaRevision(db,revision) {
  if(typeof revision!=='string'||!/^[a-f0-9]{30}$/.test(revision))
    throw new SyntheticQaWireError(409,'revision_expired');
  const current=await readSyntheticQaWire(db);
  if(current.manifest.revision!==revision) throw new SyntheticQaWireError(409,'revision_expired');
  return current;
}
