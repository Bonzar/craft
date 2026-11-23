import { test } from 'node:test';
import assert from 'node:assert/strict';
import { LodController } from '../src/lod-controller.ts';
import { decodeGraph } from '../src/binary.ts';
import { buildMembershipIndex, projectCameraMapLod, projectNavigationLod, projectLod } from '../src/lod.ts';
import type { LodRequest } from '../src/lod.ts';
import { manifestSchema, topicsSchema } from '../src/contracts.ts';
function fixture(visible:number,slots=visible,revision='r1'){
  const nodes=new ArrayBuffer(slots*16);const view=new DataView(nodes);for(let ordinal=visible;ordinal<slots;ordinal++)view.setUint32(ordinal*16+12,1,true);
  const graph=decodeGraph(nodes,new ArrayBuffer(0),slots,0,false);
  const topic=(id:string,index:number)=>({id,index,label:id,full_title:id,label_method:'source',source_ref:{view_id:id},position:[0,0,0],radius:10,depth:0,member_count:slots,freshness:'saved'});
  const topics=topicsSchema.parse({revision,topics:[topic('A',0),topic('B',1)],memberships:[{topic_index:0,node_indices:Array.from({length:slots},(_,index)=>index)},{topic_index:1,node_indices:Array.from({length:Math.ceil(slots/2)},(_,index)=>index*2)}],links:[]});
  const membership=buildMembershipIndex(slots,topics.topics,topics.memberships,topics.links);
  const manifest=manifestSchema.parse({schema:1,revision,source_revision:revision,provider_id:'source-A',captured_at:'2026-11-12T00:00:00Z',selector:{mode:'current',at:null,history:false,undated:'separate'},counts:{nodes:slots,edges:0,visible_nodes:visible,visible_edges:0,topics:2},nodes:{url:'nodes',format:'binary',stride:16,byte_length:slots*16},edges:{url:'edges',format:'binary',stride:8,byte_length:0},topics_url:'topics',bounds:{min:[0,0,0],max:[10,10,10]},layout:{version:'test',semantic:false,stable:true},complete_graph:true});
  const worker={lod:async(request:LodRequest)=>({lod:request.map_lod?projectCameraMapLod(graph,membership,request):request.navigation?projectNavigationLod(graph,membership,request):projectLod(graph,membership,request),token:request.generation,rebuilds:1}),acceptLod:async()=>{},discardLod:async()=>{}};
  return{graph,manifest,topics,worker};
}
function controller():LodController{return new LodController(()=>{},error=>{throw error;});}
for(const visible of [0,70,200])test(`first entry shows all ${visible} small-graph canonical stars exactly once despite overlapping source memberships`,async()=>{
  const source=fixture(visible,Math.max(visible,300));const controls=controller();
  const lod=await controls.bind(source.manifest,source.topics,source.worker,source.graph.visible);
  assert.equal(lod.coverage.canonical_nodes,visible);assert.equal(lod.proxies.length,0);
  assert.equal(new Set(Array.from(lod.representative_by_node).filter(ordinal=>ordinal>=0)).size,visible);
});
for(const visible of [201,100000])test(`first entry retains topic collapse above the small-graph budget (${visible} visible stars)`,async()=>{
  const source=fixture(visible);const controls=controller();const lod=await controls.bind(source.manifest,source.topics,source.worker,source.graph.visible);
  assert.equal(lod.coverage.canonical_nodes,0);assert.ok(lod.proxies.length>0);assert.equal(lod.coverage.represented_nodes,visible);
});
test('same-source staged forks preserve the accepted cut across the budget boundary; Home reapplies the current default',async()=>{
  const small=fixture(70),large=fixture(201,201,'r2');const initial=controller();await initial.bind(small.manifest,small.topics,small.worker,small.graph.visible);
  const grown=initial.fork();const retained=await grown.bind(large.manifest,large.topics,large.worker,large.graph.visible,true);
  assert.equal(retained.proxies.length,0);assert.equal(retained.coverage.canonical_nodes,201);
  grown.reset();const home=await grown.reveal([]);assert.ok(home.proxies.length>0);
  grown.expand('A');await grown.reveal([]);
  const shrunk=grown.fork();const manual=await shrunk.bind(small.manifest,small.topics,small.worker,small.graph.visible,true);
  assert.deepEqual(Array.from(manual.collapsed_topic_indices),[1]);
  shrunk.reset();const smallHome=await shrunk.reveal([]);assert.equal(smallHome.proxies.length,0);assert.equal(smallHome.coverage.canonical_nodes,70);
});

test('small physical camera map starts with Source topics, unlike classic small-graph view',async()=>{
  const source=fixture(70);
  const cut=await controller().bind(source.manifest,source.topics,source.worker,source.graph.visible,false,
    {split_px:96,merge_px:72,assertion_radius:1,min_glyph_px:12,merge_glyph_px:9});
  assert.ok(cut.map_lod);assert.equal(cut.coverage.canonical_nodes,0);
  assert.equal(cut.coverage.represented_nodes,70);assert.ok(cut.proxies.length>0);
});
