import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeGraph } from '../src/binary.ts';
import { buildMembershipIndex, projectLod } from '../src/lod.ts';
import { buildSpatialIndex, queryNear, viewBindingMatches, nearPreviewActive } from '../src/focus.ts';
import { buildEnvelopes, buildEnvelopeIndex, queryEnvelopes } from '../src/envelopes.ts';
import { PreviewCache, previewFromCard } from '../src/preview-cache.ts';
const matrix=new Float32Array([1,0,0,0,0,1,0,0,0,0,-1,-1,0,0,-.2,0]);
const binding={revision:'r1',lod_generation:2,camera_generation:3};
test('approach candidates contain only visible canonical stars and literal Unicode preview text never changes semantic selection',()=>{
  const positions=new Float32Array([0,0,-1,.1,0,-1,0,0,-1,0,0,-1,0,0,1]);
  const result=queryNear(buildSpatialIndex(positions),positions,new Float32Array([1,1,0,1,1]),new Float32Array([1,1,0,0,1]),{...binding,view_projection:matrix,camera_position:[0,0,0],camera_target:[0,0,-1],focus_screen:[200,400],viewport:[400,800],activation:{near:true},limit:12});
  assert.deepEqual(result.candidates.map(item=>item.ordinal),[0,1]);
  const literal='😀\nТочный текст «из источника». '.repeat(30);
  const preview=previewFromCard({id:'source-a',ordinal:0,text_revision:'text-r',text:literal,text_total:Array.from(literal).length},'provider','r1');
  assert.equal(preview.text,Array.from(literal).slice(0,480).join(''));
  assert.equal('selection' in preview,false);
  assert.equal(nearPreviewActive(false,.44,false),true);
  assert.equal(nearPreviewActive(true,.53,false),true);
  assert.equal(nearPreviewActive(true,.56,false),false);
});
test('preview cache retains only bounded literal payload and stale source/camera responses cannot relabel a new ordinal',()=>{
  const cache=new PreviewCache(2,4096);
  const huge={id:'a',ordinal:7,text_revision:'ta',text:'😀'.repeat(600),text_total:600,incident_edges:{items:['private metadata'.repeat(100000)]}};
  const first=previewFromCard(huge,'provider','r1');cache.put(first);cache.put({...first,id:'b',ordinal:8});cache.put({...first,id:'c',ordinal:9});
  assert.equal(cache.size,2);assert.equal(cache.get('provider','r1',7),null);assert.ok(cache.bytes<=4096);
  assert.equal('incident_edges' in first,false);
  assert.equal(viewBindingMatches(binding,{...binding,revision:'r2'}),false);
  assert.equal(viewBindingMatches(binding,{...binding,lod_generation:4}),false);
  assert.equal(viewBindingMatches(binding,{...binding,camera_generation:4}),false);
  assert.equal(viewBindingMatches(binding,binding),true);
});
function sourceFixture(){
  const buffer=new ArrayBuffer(64);const view=new DataView(buffer);[0,2,10,50].forEach((x,i)=>view.setFloat32(i*16,x,true));
  const graph=decodeGraph(buffer,new Uint32Array([0,2,1,2]).buffer,4,2,false);
  const topics=[{id:'P',index:0,position:[1,0,0],radius:1,depth:0,member_count:2},{id:'C',index:1,position:[10,0,0],radius:0,depth:1,member_count:1},{id:'B',index:2,position:[0,0,0],radius:0,depth:0,member_count:1},{id:'D',index:3,position:[50,0,0],radius:0,depth:0,member_count:1}];
  const memberships=[{topic_index:0,node_indices:[0,1]},{topic_index:1,node_indices:[2]},{topic_index:2,node_indices:[0]},{topic_index:3,node_indices:[3]}];
  const links=[{parent:0,child:1,relation:'subquestion'},{parent:2,child:1,relation:'subquestion'},{parent:0,child:3,relation:'prerequisite'}];
  return{graph,index:buildMembershipIndex(4,topics,memberships,links)};
}
test('parent envelope contains direct stars and the entire explicit child shell without inheriting its semantic members',()=>{
  const {graph,index}=sourceFixture();const bounds=buildEnvelopes(graph,index,'r1');
  const parent=Array.from(bounds.indices).indexOf(0);const child=Array.from(bounds.indices).indexOf(1);
  const distance=Math.hypot((bounds.centers[parent*3]??0)-(bounds.centers[child*3]??0),(bounds.centers[parent*3+1]??0)-(bounds.centers[child*3+1]??0),(bounds.centers[parent*3+2]??0)-(bounds.centers[child*3+2]??0));
  assert.ok(distance+(bounds.radii[child]??0)<=(bounds.radii[parent]??0)+.0001);
  assert.equal(bounds.direct_visible_counts[parent],2);assert.equal(bounds.direct_visible_counts[child],1);
  assert.ok((bounds.radii[child]??0)>0);assert.ok((bounds.radii[parent]??0)<20);
  graph.visible[2]=0;const historical=buildEnvelopes(graph,index,'r2');const p=Array.from(historical.indices).indexOf(0);
  assert.ok((historical.radii[p]??0)<2);
});
test('shared child has one fixed shell enclosed by both parents and geometric grouping never changes exact source coverage',()=>{
  const {graph,index}=sourceFixture();const request={revision:'r1',generation:2,collapsed_topic_indices:new Uint32Array([0,2]),force_reveal_ordinals:new Uint32Array()};const before=projectLod(graph,index,request).coverage;
  const bounds=buildEnvelopes(graph,index,'r1');assert.equal(Array.from(bounds.indices).filter(value=>value===1).length,1);
  const child=Array.from(bounds.indices).indexOf(1);for(const id of [0,2]){const parent=Array.from(bounds.indices).indexOf(id);assert.ok(Math.abs((bounds.centers[parent*3]??0)-(bounds.centers[child*3]??0))+(bounds.radii[child]??0)<=(bounds.radii[parent]??0)+.0001);}
  assert.deepEqual(projectLod(graph,index,request).coverage,before);
});
test('envelope queries remain bounded to24 source shells and camera/history binding rejects delayed bounds',()=>{
  const count=20000;const bounds={revision:'r1',indices:Uint32Array.from({length:count},(_,i)=>i),centers:new Float32Array(count*3),radii:new Float32Array(count).fill(.01),direct_visible_counts:new Uint32Array(count).fill(1)};
  for(let i=0;i<count;i++){bounds.centers[i*3]=(i%100-50)/100;bounds.centers[i*3+1]=(Math.floor(i/100)%100-50)/100;bounds.centers[i*3+2]=-1;}
  const selected=queryEnvelopes(buildEnvelopeIndex(bounds),bounds,{...binding,view_projection:matrix,camera_position:[0,0,0],viewport:[375,812],eligible_topic_indices:bounds.indices,priority_topic_indices:new Uint32Array([7]),limit:24});
  assert.equal(selected.shell_indices.length,24);assert.ok(selected.shell_indices.includes(7));
  assert.equal(viewBindingMatches(binding,{...binding,revision:'old'}),false);
});
test('a twelve-level source hierarchy around one shared point does not exponentially inflate empty parent shells',()=>{
  const graph=decodeGraph(new ArrayBuffer(16),new ArrayBuffer(0),1,0,false);
  const topics=Array.from({length:12},(_,i)=>({id:`topic-${String(i).padStart(2,'0')}`,index:i,position:[0,0,0],radius:0,depth:i,member_count:i===11?1:0}));
  const links=Array.from({length:11},(_,i)=>({parent:i,child:i+1,relation:'subquestion'}));
  const index=buildMembershipIndex(1,topics,[{topic_index:11,node_indices:[0]}],links);
  const bounds=buildEnvelopes(graph,index,'nested');
  assert.equal(bounds.indices.length,12);
  assert.ok((bounds.radii[0]??Infinity)<=1,`Root radius ${bounds.radii[0]} inflated without any additional coordinates`);
  assert.ok((bounds.radii[0]??0)>0);
});
