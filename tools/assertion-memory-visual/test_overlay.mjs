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
  Object.assign(v,{capabilities:{provider_id:'test-provider'},authenticated:true,csrf:'opaque',selector:{mode:'current'},currentContext:()=>({day:'same',selector:v.selector}),
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
