import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decodeGraph} from '../src/binary.ts';
import {topicsSchema} from '../src/contracts.ts';
import {deriveVisualSceneData} from '../src/visual-scene-data.ts';
import {mobileOverviewRadiusDepthCap,projectedWorldDiameter,worldGlyphMaterial} from '../src/world-glyphs.ts';

test('alternate layout goes through the same spatial source adapter without changing topology or memberships',()=>{
  const graph=decodeGraph(new ArrayBuffer(4*16),new Uint32Array([0,1,1,2,2,3]).buffer,4,3,false);
  const topic=(id:string,index:number)=>({id,index,label:id,full_title:id,label_method:'source',source_ref:{view_id:id},position:[0,0,0],radius:10,depth:0,member_count:2,freshness:'saved',navigation_membership:'selected_registry'});
  const topics=topicsSchema.parse({revision:'fixture',topics:[topic('left',0),topic('right',1)],memberships:[{topic_index:0,node_indices:[0,1]},{topic_index:1,node_indices:[2,3]}],links:[]});
  const overview=deriveVisualSceneData(graph,topics);
  const alternate=deriveVisualSceneData(graph,topics,{mode:'edges'});
  assert.notDeepEqual(alternate.graph.positions,overview.graph.positions);
  assert.deepEqual(alternate.graph.edges,graph.edges);
  assert.deepEqual(alternate.graph.visible,graph.visible);
  assert.deepEqual(alternate.topics.memberships,topics.memberships);
  assert.notEqual(alternate.graph.edges,graph.edges);
  assert.ok(graph.positions.every(value=>value===0));
  assert.deepEqual(deriveVisualSceneData(graph,topics).graph.positions,overview.graph.positions);
});
test('mobile overview cap yields a 26 CSS pixel solid body and leaves other modes unclamped',()=>{
  const cap=mobileOverviewRadiusDepthCap(true,375,812,45);
  assert.ok(Math.abs(projectedWorldDiameter(cap*25,25,45,812)-26)<1e-9);
  assert.equal(mobileOverviewRadiusDepthCap(false,375,812,45),0);
  assert.equal(mobileOverviewRadiusDepthCap(true,1024,768,45),0);
  assert.match(worldGlyphMaterial().vertexShader,/volume\.x<\.5&&solidRadiusDepthCap/);
});
