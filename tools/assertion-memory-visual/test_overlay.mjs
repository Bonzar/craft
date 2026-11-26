import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {versionableBundle} from './bundle.mjs';
test('release uses the unmodified Connections worker and a pinned tag',async()=>{
  const dir=new URL('.',import.meta.url).pathname;
  const blank=await versionableBundle(dir);
  const tag='assertion-memory-visual-v3.2.1';
  const pinned=await versionableBundle(dir,tag);
  const worker=Object.keys(pinned).find(k=>k.includes('graph.worker'));
  const javascript=Object.keys(pinned).find(k=>k.endsWith('.js')&&k!==worker);
  assert.deepEqual(pinned[worker],await readFile(new URL('ui/'+worker,import.meta.url)));
  assert.deepEqual(pinned[javascript],blank[javascript]);
  assert.match(pinned[javascript].toString(),/graphMode=`connections`/);
  assert.match(pinned['index.html'].toString(),/id="topics-toggle" hidden/);
  assert.match(pinned['index.html'].toString(),/Визуал: assertion-memory-visual-v3.2.1/);
  assert.doesNotMatch(blank['index.html'].toString(),/Визуал:/);
  assert.equal(Object.keys(pinned).length,4);
  assert.match(pinned['index.html'].toString(),/<dialog id="visual-queue" aria-labelledby="visual-queue-heading">/);
  assert.match(pinned[javascript].toString(),/queue-card-top/);
  const stylesheet=Object.keys(pinned).find(n=>n.endsWith('.css'));
  assert.match(pinned[stylesheet].toString(),/#visual-queue\{box-sizing:border-box;position:fixed;inset:0/);
  await assert.rejects(versionableBundle(dir,'<script>'),/Invalid Visual release tag/);
});

import {runInNewContext} from 'node:vm';
import {gatewayGraphProgram} from './graph-live-overlay.mjs';
test('Gateway polling uses the exact selected manifest and reloads only on revision change',{timeout:2000},async()=>{
  const eventTarget=new EventTarget();
  const indicator={dataset:{}};
  const props={hidden:false,addEventListener:(...a)=>eventTarget.addEventListener(...a),
    removeEventListener:(...a)=>eventTarget.removeEventListener(...a),getElementById:()=>indicator};
  const fixture={mode:'current',at:null,history:true,undated:'separate',session_id:'fixture',run_id:null,output_snapshot_id:null};
  let load=0, seen=[], expectedRev='old', firstSuccess, refreshed;
  const first=new Promise(resolve=>firstSuccess=resolve), second=new Promise(resolve=>refreshed=resolve);
  const app={authenticated:true,selector:fixture,manifest:{revision:'old',provider_id:'fixture'},
    setConnection:(label,ready)=>{seen.push([label,ready]);if(ready)firstSuccess();},
    loadGraph:async()=>{load++;refreshed();}};
  const context={AbortController,clearTimeout,document:props,
    setTimeout:(fn,delay)=>setTimeout(fn,delay>=3000?10:delay),
    md:async path=>({status:200,json:async()=>({revision:expectedRev,provider_id:'fixture',selector:fixture})}),
    pd:(path,query)=>{assert.equal(path,'/graph');assert.equal(query.session_id,'fixture');assert.equal(query.history,1);return path;},
    w:{parse:x=>x},Me:()=>''};
  const api=runInNewContext(gatewayGraphProgram()+'\n({startGatewayGraphPoll,stopGatewayGraphLive})',context);
  try{
    api.startGatewayGraphPoll(app);
    await first;
    assert.equal(load,0);
    assert.equal(indicator.dataset.transport,'poll');
    expectedRev='new';
    await second;
    assert.ok(load>=1);
    assert.ok(seen.some(([label,ready])=>ready&&label.includes('каждые 3 с')));
  }finally{api.stopGatewayGraphLive(app);}
});
