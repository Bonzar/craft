import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Worker } from 'node:worker_threads';
import { once } from 'node:events';
import { decodeGraph, patchGraph } from '../src/binary.ts';
const blank={upsert_nodes:[],upsert_edges:[],remove_node_ordinals:[],remove_edge_ordinals:[],status_changes:[]};
function worker():Worker{
  const moduleUrl=new URL('../src/graph.worker.ts',import.meta.url).href;
  return new Worker(new URL(`data:text/javascript,${encodeURIComponent(`import {parentPort} from 'node:worker_threads';globalThis.self={addEventListener:(_kind,callback)=>parentPort.on('message',data=>callback({data})),postMessage:value=>parentPort.postMessage(value)};await import(${JSON.stringify(moduleUrl)});` )}`));
}
async function request(worker:Worker,body:Record<string,unknown>):Promise<Record<string,unknown>>{const reply=once(worker,'message');worker.postMessage({id:1,...body});return (await reply)[0];}
test('a fresh delta worker preserves deleted edge slots while accepted worker can still answer its old revision',async()=>{
  const accepted=worker(),staged=worker();
  try{
    const nodes=new ArrayBuffer(48),edges=new Uint32Array([0,1,1,2]).buffer;
    const topics={revision:'old',topics:[],memberships:[],links:[]};
    await request(accepted,{kind:'load',nodes,edges,nodeCount:3,edgeCount:2,history:false,topics,revision:'old'});
    const oldLod=await request(accepted,{kind:'lod',request:{revision:'old',generation:1,collapsed_topic_indices:new Uint32Array(),force_reveal_ordinals:new Uint32Array()}});
    assert.ok(oldLod.lod);
    const candidate=oldLod.candidate as {token:number;base_token:number|null};
    assert.equal(candidate.base_token,null);
    assert.equal((await request(accepted,{kind:'screen-connectivity',request:{revision:'old',lod_generation:1}})).error,'Expired connectivity query');
    const ack=await request(accepted,{kind:'accept-lod',token:candidate.token});
    assert.equal(ack.accepted_token,candidate.token);assert.equal(ack.revision,'old');assert.equal(ack.generation,1);
    const graph=patchGraph(decodeGraph(nodes,edges,3,2,false),{...blank,remove_edge_ordinals:[0]},false);
    const reply=await request(staged,{kind:'load-patch',graph,patch:{...blank,remove_node_ordinals:[2]},history:false,topics,revision:'new'});
    assert.equal(reply.error,undefined);
    const next=reply.graph as typeof graph;
    assert.deepEqual(Array.from(next.edgeActive),[0,1]);
    assert.equal(next.visibleEdges.length,0);
    assert.deepEqual(Array.from(graph.visible),[1,1,1]);
    const oldScreen=await request(accepted,{kind:'screen-connectivity',request:{revision:'old',lod_generation:1,camera_generation:2,view_projection:new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]),viewport:[375,812]}});
    assert.equal(oldScreen.error,undefined);assert.ok(oldScreen.screen);
  }finally{await Promise.all([accepted.terminate(),staged.terminate()]);}
});
