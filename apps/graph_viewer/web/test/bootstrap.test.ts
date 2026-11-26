import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {bootBuffers, bootStep} from '../src/bootstrap.ts';
import {manifestSchema} from '../src/contracts.ts';
import {decodeGraph} from '../src/binary.ts';
import {GraphWorker} from '../src/worker-client.ts';

// Captured from the authenticated Graph API using the tracked independent-source.json fixture;
// no real graph records or credentials. Retains the exact returned encodings and content types.
const fixture=JSON.parse(readFileSync(new URL('./fixtures/boot-network.json',import.meta.url),'utf8'));
const manifest=manifestSchema.parse(fixture.manifest);
const paths={nodes:manifest.nodes.url,edges:manifest.edges.url,topics:manifest.topics_url} as const;
const shell=Object.assign(new EventTarget(),{location:{origin:'https://graph.test'}});

test('reads real manifest and full node/edge octets with validated graph layout and matching topics',async t=>{
  t.mock.method(globalThis,'fetch',async(path:string,init:RequestInit)=>{
    assert.equal(init.credentials,'same-origin');assert.equal(init.cache,'no-store');
    const entry=Object.entries(paths).find(([,url])=>path===url);
    assert.ok(entry);
    const data=fixture.resources[entry[0]];
    if (entry[0]!=='topics')assert.equal(new Headers(init.headers).get('accept'),'application/octet-stream');
    return new Response(Buffer.from(data.body_b64,'base64'),{headers:{'content-type':data.mime}});
  });
  Object.defineProperty(globalThis,'window',{value:shell,configurable:true});
  try{
    const [nodes,edges,topics]=await bootBuffers(manifest,new AbortController().signal);
    const graph=decodeGraph(nodes,edges,manifest.counts.nodes,manifest.counts.edges,manifest.selector.history);
    assert.equal(topics.revision,manifest.revision);
    assert.equal(graph.visible.reduce((total,value)=>total+value,0),manifest.counts.visible_nodes);
    assert.equal(graph.visibleEdges.length/2,manifest.counts.visible_edges);
  }finally{Reflect.deleteProperty(globalThis,'window');}
});

test('rejects HTML with status 200 and truncated graph binary instead of awaiting a worker',async t=>{
  Object.defineProperty(globalThis,'window',{value:shell,configurable:true});
  let malformed='text/html';
  t.mock.method(globalThis,'fetch',async(path:string)=>path===paths.nodes
    ? new Response(new Uint8Array(1),{headers:{'content-type':malformed}})
    : path===paths.edges
    ? new Response(Buffer.from(fixture.resources.edges.body_b64,'base64'),{headers:{'content-type':fixture.resources.edges.mime}})
    : new Response(Buffer.from(fixture.resources.topics.body_b64,'base64'),{headers:{'content-type':fixture.resources.topics.mime}}));
  try{
    await assert.rejects(bootBuffers(manifest,new AbortController().signal),/неподходящем формате/);
    malformed='application/octet-stream';
    await assert.rejects(bootBuffers(manifest,new AbortController().signal),/неполные данные/);
  }finally{Reflect.deleteProperty(globalThis,'window');}
});

test('timeout rejects even WebKit body/fetch hangs ignoring AbortSignal; retry is independent',async()=>{
  let child:AbortSignal|undefined;
  await assert.rejects(bootStep('payload','Чтение карты просрочено',signal=>{
    child=signal;return new Promise(()=>{});
  },undefined,8),/Чтение карты просрочено/);
  assert.equal(child?.aborted,true);
  assert.equal(await bootStep('payload','error',async()=>42,undefined,8),42);
});

test('new graph cancels the preceding unresponsive operation without a timeout error',async()=>{
  const caller=new AbortController();
  const prev=bootStep('worker','timeout',()=>new Promise(()=>{}),caller.signal,200);
  caller.abort();
  await assert.rejects(prev,{name:'AbortError'});
});

for(const failure of ['error','messageerror']){
  test('worker boot failure '+failure+' rejects rather than hanging',async()=>{
    class Port extends EventTarget {
      dead=false;
      postMessage(){queueMicrotask(()=>this.dispatchEvent(new Event(failure)));}
      terminate(){this.dead=true;}
    }
    const raw=new Port();
    Object.defineProperty(globalThis,'window',{value:shell,configurable:true});
    try{
      const worker=new GraphWorker(raw as unknown as Worker);
      await assert.rejects(worker.load(new ArrayBuffer(0),new ArrayBuffer(0),0,0,false,
        {revision:'r',topics:[],memberships:[],links:[],unknowns:[],completeness:null,
         layout_topics:null,layout_memberships:null,presentation_snapshot:null},'r'),/модуль обработки карты/);
      assert.equal(raw.dead,true);
    }finally{Reflect.deleteProperty(globalThis,'window');}
  });
}
