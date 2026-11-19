import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeGraph, patchGraph } from '../src/binary.ts';
const blank={upsert_nodes:[],upsert_edges:[],remove_node_ordinals:[],remove_edge_ordinals:[],status_changes:[]};
test('malformed delta slot cannot silently append empty stars and keeps last complete graph unchanged',()=>{
  const graph=decodeGraph(new ArrayBuffer(16),new ArrayBuffer(0),1,0,false);
  const patch={...blank,upsert_nodes:[{id:'new-a',ordinal:2,position:[1,2,3] as [number,number,number],flags:0},{id:'new-b',ordinal:2,position:[2,3,4] as [number,number,number],flags:0}]};
  assert.throws(()=>patchGraph(graph,patch,false),/slot/i);
  assert.equal(graph.flags.length,1);
});
test('malformed negative live edge ordinal is rejected instead of corrupting the accepted delta',()=>{
  const graph=decodeGraph(new ArrayBuffer(16),new ArrayBuffer(0),1,0,false);
  assert.throws(()=>patchGraph(graph,{...blank,upsert_edges:[{id:'bad',ordinal:-1,from:0,to:0}]},false),/ordinal/i);
});
test('removing a node hides all incident lines even with history enabled',()=>{
  const graph=decodeGraph(new ArrayBuffer(32),new Uint32Array([0,1]).buffer,2,1,true);
  const updated=patchGraph(graph,{...blank,remove_node_ordinals:[1]},true);
  assert.equal(updated.visible[1],0);
  assert.equal(updated.visibleEdges.length,0);
  assert.equal(graph.visible[1],1);
});
