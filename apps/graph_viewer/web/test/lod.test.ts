import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeGraph } from '../src/binary.ts';
import { buildMembershipIndex, projectLod, nextCollapsedTopics, acceptsLod } from '../src/lod.ts';
const topics=[{id:'A',index:0,position:[0,0,0],radius:10,depth:0,member_count:3},{id:'B',index:1,position:[10,0,0],radius:10,depth:0,member_count:3},{id:'C',index:2,position:[5,0,0],radius:3,depth:1,member_count:2}];
const memberships=[{topic_index:0,node_indices:[0,1,2]},{topic_index:1,node_indices:[1,2,3]},{topic_index:2,node_indices:[1,2]}];
const links=[{parent:0,child:2,relation:'subquestion'},{parent:1,child:2,relation:'subquestion'}];
function fixture(edges:number[]=[]){return decodeGraph(new ArrayBuffer(64),new Uint32Array(edges).buffer,4,edges.length/2,false);}
function projection(collapsed:number[],forced:number[]=[],edges:number[]=[]){const graph=fixture(edges);const membership=buildMembershipIndex(4,topics,memberships,links);return projectLod(graph,membership,{revision:'r',generation:1,collapsed_topic_indices:new Uint32Array(collapsed),force_reveal_ordinals:new Uint32Array(forced)});}
test('overlapping many-parent source topics form an exact disjoint presentation partition at every cut',()=>{
  const top=projection([0,1]);
  assert.equal(top.coverage.canonical_nodes,0);
  assert.equal(top.coverage.represented_nodes,4);
  assert.deepEqual(top.proxies.map(proxy=>[proxy.topic_id,proxy.represented_count,proxy.source_member_count,proxy.visible_source_member_count]),[['A',3,3,3],['B',1,3,3]]);
  assert.equal(new Set(Array.from(top.representative_by_node)).size,2);
  const child=projection([2]);
  assert.equal(child.coverage.canonical_nodes,2);
  assert.equal(child.proxies[0]?.represented_count,2);
  assert.equal(projection([]).coverage.canonical_nodes,4);
});
test('forced search or selected canonical node appears exactly once and leaves its former proxy count',()=>{
  const result=projection([0,1],[2]);
  assert.equal(result.node_render_visible[2],1);
  assert.equal(result.representative_by_node[2],2);
  assert.equal(result.coverage.canonical_nodes,1);
  assert.equal(result.coverage.represented_nodes,3);
  assert.equal(result.proxies.reduce((sum,proxy)=>sum+proxy.represented_count,0),3);
});
test('parallel reversed bundled edges and internal edges retain every original ordinal exactly once',()=>{
  const graphEdges=[0,3,0,3,3,0,0,1,2,3];
  const result=projection([0],[2,3],graphEdges);
  assert.deepEqual(result.coverage,{visible_nodes:4,canonical_nodes:2,represented_nodes:2,visible_edges:5,original_edges:1,bundled_edges:3,internal_edges:1});
  const originals=Array.from(result.edge_pick_target).filter((_,i)=>result.edge_pick_kind[i]===0);
  const all=[...originals,...result.bundle_edge_ordinals,...result.proxy_internal_edge_ordinals].sort((a,b)=>a-b);
  assert.deepEqual(all,[0,1,2,3,4]);
  assert.equal(result.bundle_offsets.length-1,2);
  assert.deepEqual(Array.from(result.bundle_offsets),[0,2,3]);
});
test('hidden historical slots are never counted in proxies or bundles and missing parent memberships are never inferred',()=>{
  const graph=fixture([0,1,0,2]);graph.flags[1]=1;graph.visible[1]=0;graph.visibleEdges=new Uint32Array([0,2]);graph.visibleEdgeOrdinals=new Uint32Array([1]);
  const membership=buildMembershipIndex(4,[{...topics[0],member_count:1},topics[2]],[{topic_index:0,node_indices:[0]},{topic_index:2,node_indices:[1,2]}],[links[0]]);
  const result=projectLod(graph,membership,{revision:'r',generation:2,collapsed_topic_indices:new Uint32Array([0]),force_reveal_ordinals:new Uint32Array()});
  assert.equal(result.coverage.visible_nodes,3);
  assert.equal(result.proxies[0]?.represented_count,1);
  assert.equal(result.node_render_visible[2],1);
  assert.equal(result.representative_by_node[1],-1);
});
test('LOD hysteresis keeps its cut during camera oscillation and stale generations cannot replace a current revision',()=>{
  assert.equal(nextCollapsedTopics(true,100,96),true);
  assert.equal(nextCollapsedTopics(true,140,96),false);
  assert.equal(nextCollapsedTopics(false,100,96),false);
  assert.equal(nextCollapsedTopics(false,90,96),true);
  assert.equal(acceptsLod('r',3,{revision:'r',generation:3}),true);
  assert.equal(acceptsLod('r',3,{revision:'r',generation:2}),false);
  assert.equal(acceptsLod('new',3,{revision:'old',generation:3}),false);
});
test('equal-depth shared ownership is stable by source topic ID independent of membership row order',()=>{
  const graph=fixture();
  const one=buildMembershipIndex(4,topics,memberships,links);
  const two=buildMembershipIndex(4,topics,[memberships[1],memberships[0],memberships[2]],links);
  const request={revision:'r',generation:1,collapsed_topic_indices:new Uint32Array([0,1]),force_reveal_ordinals:new Uint32Array()};
  assert.deepEqual(Array.from(projectLod(graph,one,request).representative_by_node),Array.from(projectLod(graph,two,request).representative_by_node));
});
test('invalid source membership ordinals are rejected before unsigned conversion can wrap them into real stars',()=>{
  for(const ordinal of [4294967296,-1,1.5,NaN,Infinity]){
    assert.throws(()=>buildMembershipIndex(4,topics,[{topic_index:0,node_indices:[ordinal]}],links),/membership/i);
  }
});
test('repeated source membership rows for one topic are united rather than double-counting shared members',()=>{
  const index=buildMembershipIndex(4,[topics[0]],[{topic_index:0,node_indices:[0,1]},{topic_index:0,node_indices:[1,2]}],[]);
  const result=projectLod(fixture(),index,{revision:'r',generation:1,collapsed_topic_indices:new Uint32Array([0]),force_reveal_ordinals:new Uint32Array()});
  assert.equal(result.proxies[0]?.visible_source_member_count,3);
  assert.equal(result.proxies[0]?.represented_count,3);
  assert.equal(result.coverage.visible_nodes,4);
});
