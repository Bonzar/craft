import {test} from 'node:test';import assert from 'node:assert/strict';
import {expectedLod} from './lod-oracle.js';
const topic=(id,index,members)=>({id,index,source_ref:{view_id:id},members:Uint32Array.from(members)});
const base=()=>({visible:Uint8Array.from([1,1,1,1,0,1]),edges:Uint32Array.from([0,3,0,3,3,0,1,2,0,5,4,0,3,5,5,0]),
 topics:[topic('a',0,[0,1,2,4]),topic('b',1,[1,2,3,4]),topic('c',2,[1,2])],
 links:[{parent:0,child:2,relation:'subquestion',provenance:{view_id:'a',route_revision:1}},{parent:1,child:2,relation:'subquestion',provenance:{view_id:'b',route_revision:1}}],collapsed:[0,1],forced:[]});

test('shared source members have one disjoint owner while overlapping membership counts stay truthful',()=>{
 const result=expectedLod(base());assert.deepEqual([...result.representativeByNode],[6,6,6,7,-1,5]);
 assert.deepEqual(result.coverage,{visibleNodes:5,canonicalNodes:1,representedNodes:4,visibleEdges:7,originalEdges:0,bundledEdges:6,internalEdges:1});
 assert.deepEqual(result.topics.map(t=>[t.owned,t.sourceMembers,t.visibleMembers]),[[3,4,3],[1,4,3],[0,2,2]]);
});
test('forced canonical star is removed from proxy ownership once and hidden forced node remains hidden',()=>{
 const input=base();input.forced=[2,4];const result=expectedLod(input);
 assert.deepEqual([...result.representativeByNode],[6,6,2,7,-1,5]);assert.equal(result.coverage.canonicalNodes,2);assert.equal(result.topics[0].owned,2);
});
test('most specific actual collapsed child owns overlaps across multiple source parents',()=>{
 const input=base();input.collapsed=[0,2];const result=expectedLod(input);
 assert.deepEqual([...result.representativeByNode],[6,8,8,3,-1,5]);
 assert.equal(result.coverage.originalEdges,1);assert.equal(result.coverage.bundledEdges,5);assert.equal(result.coverage.internalEdges,1);
 assert.deepEqual([...result.edgeClass],[1,1,1,2,1,255,0,1]);
});
test('full expansion keeps every canonical visible node and all original visible edge ordinals',()=>{
 const input=base();input.collapsed=[];const result=expectedLod(input);
 assert.deepEqual([...result.representativeByNode],[0,1,2,3,-1,5]);assert.equal(result.coverage.canonicalNodes,5);
 assert.equal(result.coverage.originalEdges,7);assert.equal(result.coverage.bundledEdges,0);assert.equal(result.coverage.internalEdges,0);
});
test('parent DAG reachability never invents missing parent membership',()=>{
 const input=base();input.topics[0].members=Uint32Array.from([0]);input.collapsed=[0];const result=expectedLod(input);
 assert.deepEqual([...result.representativeByNode],[6,1,2,3,-1,5]);
});
test('prerequisite or unverified links cannot create semantic depth for tie breaking',()=>{
 const input=base();input.links[0].relation='prerequisite';input.links[1].provenance.view_id='unverified';input.collapsed=[0,2];
 const result=expectedLod(input);assert.deepEqual([...result.representativeByNode],[6,6,6,3,-1,5]);
});
test('dangling source edge or source membership is rejected instead of counted as covered',()=>{
 const input=base();input.edges=Uint32Array.from([0,99]);assert.throws(()=>expectedLod(input),RangeError);
 const other=base();other.topics[0].members=Uint32Array.from([99]);assert.throws(()=>expectedLod(other),RangeError);
});
test('inactive source edge slots never enter projected edge coverage',()=>{
 const input=base();input.edgeActive=Uint8Array.from([0,1,1,1,1,1,1,1]);const result=expectedLod(input);
 assert.equal(result.edgeClass[0],255);assert.equal(result.coverage.visibleEdges,6);assert.equal(result.coverage.bundledEdges,5);
});
