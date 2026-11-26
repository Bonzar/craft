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
  assert.match(pinned[javascript].toString(),/database_cleared.*location\.reload/);
  const stylesheet=Object.keys(pinned).find(n=>n.endsWith('.css'));
  assert.match(pinned[stylesheet].toString(),/#visual-queue\{box-sizing:border-box;position:fixed;inset:0/);
  await assert.rejects(versionableBundle(dir,'<script>'),/Invalid Visual release tag/);
});

import "./graph-boot-overlay.test.mjs";

import vm from 'node:vm';
import {ownerLiveMethods,ownerLivePrelude,patchOwnerLive} from './visual-overlay.mjs';

test('assembled owner live requires matching protected capability; refreshes same view without reload',async()=>{
  const pending=[],statuses=[],calls=[];
  const c=vm.createContext({URL,releaseOpenOwnerLive(config){calls.push(config);return{close(){statuses.push('closed')}}}});
  const Viewer=vm.runInContext(`class Viewer{${ownerLiveMethods}};Viewer`,c);
  const v=new Viewer();
  Object.assign(v,{manifest:{owner_live_epoch:'a'.repeat(64)},capabilities:{provider_id:'test-provider'},authenticated:true,csrf:'opaque',selector:{mode:'current'},currentContext:()=>({day:'same',selector:v.selector}),
    setConnection(label,ready){statuses.push([label,ready])},
    loadGraph(context,preserve){pending.push([context,preserve]);},
  });
  assert.equal(v.startReleaseOwnerLive(),false);
  for(const cfg of [{version:1,lane:'prod',origin:'https://192.0.2.1'},{version:1,lane:'test',origin:'https://example.test'},{version:1,lane:'test',origin:'https://192.0.2.1/path'}]){
    v.capabilities.owner_live_transport=cfg;
    assert.equal(v.startReleaseOwnerLive(),false);
  }
  assert.equal(calls.length,0);
  v.capabilities.owner_live_transport={version:1,lane:'test',origin:'https://192.0.2.1'};
  assert.equal(v.startReleaseOwnerLive(),true);
  assert.equal(v.startReleaseOwnerLive(),true);
  assert.equal(calls.length,1);
  const cfg=calls[0];
  cfg.onState('connecting');cfg.onState('live');
  assert.deepEqual(statuses.map(e=>e[1]),[false,true]);
  await cfg.onRefresh('changed');
  assert.deepEqual(pending,[[undefined,true]]);
  v.selector={mode:'known_then'};
  await cfg.onRefresh('changed');
  assert.equal(pending.length,1);
  v.graph=v.manifest=v.topics={revision:'old'};
  v.scene={dispose(){statuses.push('scene disposed')}};
  v.worker={dispose(){statuses.push('worker disposed')}};
  v.loading={hidden:true};
  v.clearContextSelection=()=>statuses.push('selection cleared');
  v.ensureReleaseScene=()=>statuses.push('scene initialized');
  cfg.onInvalidate();
  await cfg.onRefresh('reset');
  assert.equal(v.graph,null);
  assert.equal(v.manifest,null);
  assert.equal(v.topics,null);
  assert.equal(v.loading.hidden,false);
  assert.deepEqual(statuses.slice(-4),['selection cleared','worker disposed','scene disposed','scene initialized']);
  assert.deepEqual(pending,[[undefined,true],[undefined,true]]);
  v.stopReleaseOwnerLive();
  await cfg.onRefresh('changed');
  assert.equal(pending.length,2);
});

test('packed controller shares direct stream implementation and fails closed on seed drift',async()=>{
  assert.doesNotThrow(()=>new vm.Script(ownerLivePrelude()));
  const dir=new URL('.',import.meta.url).pathname;
  const result=await versionableBundle(dir);
  const main=Object.keys(result).find(name=>/^assets\/index-.*\.js$/.test(name));
  const source=result[main].toString();
  assert.doesNotThrow(()=>new vm.Script(source.replace('import.meta.url','"https://localhost/visual/assets/"')));
  assert.match(source,/owner_live_transport:d\(c,null\)/);
  assert.match(source,/credentials:'omit',mode:'cors',redirect:'error'/);
  assert.match(source,/this\.startReleaseOwnerLive\(\)\)return;this\.stopReleaseOwnerLive\(\);let t=new EventSource/);
  assert.throws(()=>patchOwnerLive(source,source),/seed changed/);
});

test('actual packed transport keeps open view on foreground and reconnect, then invalidates on new epoch',async()=>{
  const timers=[],streams=[],loads=[],disposed=[];
  const document=new EventTarget();document.visibilityState='visible';
  const window=new EventTarget();
  const a='a'.repeat(64),b='b'.repeat(64);
  let epoch=a,revision=1;
  const c=vm.createContext({URL,AbortController,TextDecoder,performance,document,window,
    setTimeout(fn,delay){const task={fn,delay};timers.push(task);return task},
    clearTimeout(task){if(task)task.dead=true},
    fetch:async path=>path==='/api/v1/events-ticket'?
      Response.json({ticket:'A'.repeat(43),epoch,stream_url:'https://192.0.2.1/__mvt/api/v1/events-direct'}):
      new Response(new ReadableStream({start(ctrl){
        streams.push({send(...types){ctrl.enqueue(new TextEncoder().encode(types.map(x=>'event: '+x+'\ndata: {}\n\n').join('')))},end(){ctrl.close()}})
      }}),{headers:{'Content-Type':'text/event-stream'}}),
  });
  const Viewer=vm.runInContext(ownerLivePrelude()+`class Viewer{${ownerLiveMethods}};Viewer`,c);
  const v=new Viewer();
  const scene={camera:{zoom:8},dispose(){disposed.push('scene')}};
  const worker={dispose(){disposed.push('worker')}};
  const selector={mode:'current',session_id:'fixture-only'};
  Object.assign(v,{scene,worker,selector,authenticated:true,csrf:'fixture',selectedNodeId:'fixture-node',
    manifest:{owner_live_epoch:a,revision:1},graph:{revision:1},topics:{revision:1},loading:{hidden:true},
    capabilities:{provider_id:'fixture',owner_live_transport:{version:1,lane:'test',origin:'https://192.0.2.1'}},
    currentContext:()=>({selector}),setConnection(){},
    clearContextSelection(){disposed.push('selection');this.selectedNodeId=null},
    ensureReleaseScene(){if(!this.scene)this.scene={camera:{zoom:1}}},
    async loadGraph(_,preserve){loads.push([preserve,revision]);this.manifest={owner_live_epoch:epoch,revision};this.graph={revision};this.loading.hidden=true},
  });
  const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()};
  const timer=delay=>{const t=timers.find(t=>t.delay===delay&&!t.dead&&!t.used);assert.ok(t);t.used=true;t.fn()};
  assert.equal(v.startReleaseOwnerLive(),true);await flush();
  streams[0].send('hello','reset');await flush();
  document.visibilityState='hidden';document.dispatchEvent(new Event('visibilitychange'));
  revision=2;document.visibilityState='visible';document.dispatchEvent(new Event('visibilitychange'));
  timer(0);await flush();
  streams[0].end();await flush();timer(1000);await flush();
  revision=3;streams[1].send('hello','reset');await flush();
  revision=4;streams[1].send('changed');await flush();
  assert.deepEqual(loads,[[true,1],[true,2],[true,3],[true,4]]);
  assert.deepEqual(disposed,[]);
  assert.equal(v.scene,scene);assert.equal(v.worker,worker);
  assert.equal(v.scene.camera.zoom,8);assert.equal(v.selectedNodeId,'fixture-node');assert.equal(v.selector,selector);
  streams[1].end();await flush();epoch=b;revision=5;timer(2000);await flush();
  streams[2].send('hello','reset');await flush();
  assert.deepEqual(disposed,['selection','worker','scene']);
  assert.equal(v.manifest.revision,5);assert.equal(v.selectedNodeId,null);
  assert.equal(v.selector,selector);v.stopReleaseOwnerLive();
});

test('packed browser assets keep the public same-origin contract without carrying Base service code or credentials',async()=>{
  const dir=new URL('.',import.meta.url).pathname;
  const assets=await versionableBundle(dir);
  const paths=Object.keys(assets);
  assert.equal(paths.length,4);
  assert.ok(paths.includes('index.html'));
  assert.equal(paths.filter(x=>/^assets\/index-[\w-]+\.js$/.test(x)).length,1);
  assert.equal(paths.filter(x=>/^assets\/index-[\w-]+\.css$/.test(x)).length,1);
  assert.equal(paths.filter(x=>/^assets\/graph\.worker-[\w-]+\.js$/.test(x)).length,1);
  const browser=assets[paths.find(x=>/^assets\/index-[\w-]+\.js$/.test(x))].toString();
  for(const path of [
    '/api/v1/session','/api/v1/capabilities?queue_status=1',
    '/api/v1/processing/sessions','/api/v1/processing/pause/prepare',
    '/api/v1/processing/pause','/api/v1/processing/items/remove',
    '/api/v1/processing/items/source-preview','/api/v1/processing/database/clear-v2',
    '/api/v1/events?channel=dashboard','/api/v1/events-ticket',
  ])assert.ok(browser.includes(path),'missing public request: '+path);
  for(const path of ['graph','search','capabilities','bookmarks','events']) {
    assert.match(browser,new RegExp('["\x60]/'+path+'["\x60]'),'missing graph request: '+path);
  }
  assert.doesNotMatch(browser,/\/api\/v1\/(?:queue-(?:status|pause|selection|remove)|internal)(?:[/"'`?]|$)/);
  assert.doesNotMatch(browser,/localhost:88\d\d|127\.0\.0\.1:88\d\d/);
  // The existing, distinct direct TEST ingress consumes only the scoped
  // browser ticket returned by the authenticated same-origin POST.
  assert.match(browser,/\/__mvt\/api\/v1\/events-direct/);
  assert.match(browser,/credentials:'omit',mode:'cors',redirect:'error'/);
});
