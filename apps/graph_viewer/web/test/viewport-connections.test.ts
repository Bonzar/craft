import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decodeGraph} from '../src/binary.ts';
import {buildMembershipIndex,projectLod} from '../src/lod.ts';
import {buildViewportConnections,acceptsScreenConnectivity} from '../src/viewport-connections.ts';
import type {ViewportLod} from '../src/viewport-connections.ts';

function weightedFixture(){
  const edges=[0,1,0,1,0,3,0,3,0,3,3,0,...Array.from({length:5},()=>[3,2]).flat(),...Array.from({length:7},()=>[3,4]).flat(),0,0];
  const graph=decodeGraph(new ArrayBuffer(5*16),new Uint32Array(edges).buffer,5,edges.length/2,false);
  const topics=[{id:'P',index:0,position:[0,0,0],radius:1,depth:0,member_count:2}];
  return projectLod(graph,buildMembershipIndex(5,topics,[{topic_index:0,node_indices:[3,4]}],[]),{revision:'weighted',generation:1,collapsed_topic_indices:new Uint32Array([0]),force_reveal_ordinals:new Uint32Array()});
}
function snapshot(index:ReturnType<typeof buildViewportConnections>,ids:number[],camera:number){return index.update(new Uint32Array(ids),camera);}
function total(values:Uint32Array){return values.reduce((sum,value)=>sum+value,0);}

test('weighted parallel/reverse source edges count exact onscreen external endpoints and proxy internal edges once',()=>{
  const lod=weightedFixture();const index=buildViewportConnections(lod,5);const result=snapshot(index,[0,1,5],1);
  assert.deepEqual(Array.from(result.external_visible),[6,2,0,0,0,4]);
  assert.equal(result.unique_external_edges,6);assert.equal(total(result.external_visible),12);
  assert.equal(result.proxy_internal[5],7);assert.equal(result.external_visible[5]!+result.proxy_internal[5]!,11);
  assert.equal(result.canonical_self[0],1);assert.equal(result.stats.onscreen_stars,2);assert.equal(result.stats.onscreen_proxies,1);
  assert.deepEqual(Array.from(index.active),[1,1,1,0,0,1]);
});

test('simultaneously entering/leaving endpoints update each projected edge once and unchanged masks do no edge work',()=>{
  const lod=weightedFixture();const index=buildViewportConnections(lod,5);snapshot(index,[0,1,5],1);
  const entered=snapshot(index,[0,1,2,5],2);
  assert.deepEqual(Array.from(entered.external_visible),[6,2,5,0,0,9]);assert.equal(entered.unique_external_edges,11);
  const left=snapshot(index,[2,5],3);
  assert.deepEqual(Array.from(left.external_visible),[0,0,5,0,0,5]);assert.equal(left.unique_external_edges,5);
  assert.equal(left.canonical_self[0],0);assert.equal(left.diagnostics.changed_representatives,2);
  assert.equal(left.diagnostics.visited_projected_edges,4);assert.equal(left.diagnostics.full_rebuilds,0);
  const fresh=snapshot(buildViewportConnections(lod,5),[2,5],3);
  assert.deepEqual(left.external_visible,fresh.external_visible);assert.equal(left.unique_external_edges,fresh.unique_external_edges);
  for(let frame=4;frame<304;frame++){
    const unchanged=snapshot(index,[5,2,5],frame);
    assert.equal(unchanged.diagnostics.visited_projected_edges,0);assert.equal(unchanged.diagnostics.full_rebuilds,0);
    assert.deepEqual(unchanged.external_visible,left.external_visible);
  }
});

test('shared source membership internal edge moves to one external edge on reveal and stale binding cannot restyle new ordinals',()=>{
  const graph=decodeGraph(new ArrayBuffer(3*16),new Uint32Array([0,1]).buffer,3,1,false);
  const topics=['A','B'].map((id,index)=>({id,index,position:[0,0,0],radius:1,depth:0,member_count:2}));
  const memberships=topics.map(topic=>({topic_index:topic.index,node_indices:[0,1]}));
  const membership=buildMembershipIndex(3,topics,memberships,[]);
  const collapsed=projectLod(graph,membership,{revision:'r',generation:1,collapsed_topic_indices:new Uint32Array([0,1]),force_reveal_ordinals:new Uint32Array()});
  const before=snapshot(buildViewportConnections(collapsed,3),[3],7);
  assert.equal(before.proxy_internal[3],1);assert.equal(before.unique_external_edges,0);
  const revealed=projectLod(graph,membership,{revision:'r',generation:2,collapsed_topic_indices:new Uint32Array([0,1]),force_reveal_ordinals:new Uint32Array([0])});
  const after=snapshot(buildViewportConnections(revealed,3),[0,3],9);
  assert.equal(after.proxy_internal[3],0);assert.equal(after.external_visible[0],1);assert.equal(after.external_visible[3],1);assert.equal(after.unique_external_edges,1);
  assert.equal(revealed.coverage.visible_edges,collapsed.coverage.visible_edges);
  assert.equal(acceptsScreenConnectivity('r',2,8,before),false);assert.equal(acceptsScreenConnectivity('r',2,8,after),true);
  assert.equal(acceptsScreenConnectivity('r',2,10,after),false);assert.equal(acceptsScreenConnectivity('other',2,8,after),false);
});

function scaleFixture(internal:readonly number[]):ViewportLod{
  const nodes=9;const proxyCount=internal.length;
  const proxies=internal.map((count,index)=>({topic_id:'P'+index,topic_index:index,position:[0,0,0] as [number,number,number],represented_count:2,source_member_count:2,visible_source_member_count:2,internal_edge_count:count,generation:1}));
  return {revision:'scales',generation:1,node_render_visible:new Float32Array([1,1,1,1,1,0,0,0,0]),proxies,
    render_positions:new Float32Array((nodes+proxyCount)*3),render_edges:new Uint32Array([1,nodes,2,nodes,3,nodes,4,nodes]),
    edge_pick_kind:new Uint8Array([1,1,1,1]),edge_pick_target:new Uint32Array([0,1,2,3]),bundle_offsets:new Uint32Array([0,1,3,8,1000008])};
}

test('canonical and proxy robust log scales remain independent, monotone, finite and compatible with a visible 12px floor',()=>{
  const ordinary=snapshot(buildViewportConnections(scaleFixture([100]),9),[0,1,2,3,4,9],1);
  const huge=snapshot(buildViewportConnections(scaleFixture([100,10000000]),9),[0,1,2,3,4,9,10],1);
  assert.deepEqual(Array.from(ordinary.external_visible).slice(0,5),[0,1,2,5,1000000]);
  assert.deepEqual(ordinary.star_intensity.slice(0,9),huge.star_intensity.slice(0,9));
  assert.equal(ordinary.stats.star_scale_cap,14);
  const levels=Array.from(ordinary.star_intensity).slice(0,5);
  assert.equal(levels[0],0);assert.ok(levels[1]!<levels[2]!&&levels[2]!<levels[3]!&&levels[3]!<levels[4]!);
  for(const level of levels){assert.ok(Number.isFinite(level)&&level>=0&&level<=1);assert.ok(12+8*level>=12);}
  assert.equal(huge.proxy_internal[10],10000000);assert.equal(huge.proxy_intensity[10],1);
  assert.equal(huge.stats.onscreen_stars,5);assert.equal(huge.stats.onscreen_proxies,2);
});
