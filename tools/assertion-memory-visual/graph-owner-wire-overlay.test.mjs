import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {versionableBundle} from './bundle.mjs';
import {graphWireMethods} from './graph-owner-wire-overlay.mjs';
import {extendOwnerGraphPositions,ownerGraphDeltaIds,preserveOwnerCamera} from '../../apps/graph_viewer/web/src/owner-graph-position.mjs';

const E='e'.repeat(64),E2='f'.repeat(64),R='a'.repeat(32),R2='b'.repeat(32);
function input(revision,epoch,cursor,ids,edges=[]){
  const endpoints=Uint32Array.from(edges),edgeActive=new Uint8Array(edges.length/2).fill(1);
  const graph={positions:Float32Array.from(ids.flatMap((_,i)=>[21+i,31+i,41+i])),
    flags:new Uint32Array(ids.length),visible:new Float32Array(ids.length).fill(1),
    edges:endpoints,edgeActive,visibleEdges:endpoints,visibleEdgeOrdinals:Uint32Array.from(edgeActive,(_,i)=>i)};
  const manifest={schema:1,provider_id:'source',owner_live_epoch:epoch,revision,complete_graph:true,
    selector:{mode:'current',history:false},counts:{nodes:ids.length,edges:edgeActive.length,visible_nodes:ids.length,visible_edges:edgeActive.length}};
  return {epoch,revision,cursor,manifest,topics:{revision,topics:[]},ids,nodes:graph,edges:new ArrayBuffer(0)};
}

test('actual packed graph-v1 adapter stays on one graph stream, retains world endpoints, and discards a changed epoch',async()=>{
  const pending=[],positions=[],args=[],events=[];
  let workers=0,forced=0,cacheClears=0,signIn=0;
  const parse={parse:v=>v};
  const Runtime=vm.runInNewContext('class Viewer{'+graphWireMethods+'}; Viewer',{
    Symbol,Float32Array,Uint8Array,Uint32Array,DOMException,
    location:{origin:'https://owner.invalid'},CustomEvent:class{constructor(type){this.type=type}},
    dispatchEvent:e=>events.push(e.type),w:parse,j:parse,ue:parse,
    lm:class {constructor(){workers++;} async load(value){return value;}
      async forceLayout(graph){forced++;return graph.positions;}
      dispose(){}},
    ownerGraphCheckpointKey:()=>null,ownerGraphCache:()=>({take:async()=>null,clear:async()=>{cacheClears++;},save:async()=>{}}),
    browserOwnerTopologyStore:()=>null,ownerGraphDeltaIds,extendOwnerGraphPositions,
    openOwnerGraphSession: options=>{pending.push(options);return {initial:Promise.resolve(),close(){}};},
  });
  const context={selector:{mode:'current',history:false},day:'',zone:'UTC'};
  const v=new Runtime();
  Object.assign(v,{capabilities:{owner_live_graph_v1:true,provider_id:'source'},authenticated:true,
    loading:{hidden:true},stopReleaseOwnerLive(){},ensureReleaseScene(){},setConnection(){},
    clearReleaseOwnerLiveGraph(){this.graph=null;},showLogin(){signIn++;},applyContextInputs(){},updateCounts(){},updateReaderConnections(){},
    presentGraph(graph,manifest,topics,worker,resetCamera,isCurrent,signal,preserve,placement){
      args.push([resetCamera,preserve]);assert.equal(isCurrent(),true);
      return this.graphWirePlacement(graph,worker,placement).then(x=>{positions.push(x);return null;});
    },
  });
  v.readOwnerGraphAuth({graph_cache_scope:E2,graph_cache_epoch:E});
  await v.openGraphWire(context);
  assert.equal(pending.length,1);
  assert.equal(pending[0].verifiedEpoch,E);
  const signal=new AbortController().signal, stream=pending[0];
  await stream.onSnapshot(input(R,E,'8',['node-a','node-b'],[0,1]),signal);
  const after=await stream.onSnapshot(input(R2,E,'9',['node-b','new','node-a'],[2,0,1,0]),signal);
  assert.deepEqual(positions[1].slice(0,3),positions[0].slice(3,6));
  assert.deepEqual(positions[1].slice(6,9),positions[0].slice(0,3));
  assert.equal(after.ids[1],'new');
  assert.deepEqual(args,[[true,false],[false,true]]);
  assert.equal(forced,1,'warm same-epoch change must not run force layout');
  assert.equal(workers,2);
  await stream.onReset({epoch:E2},signal);
  assert.equal(v.graph,null);
  assert.equal(v.releaseGraphPlacementState,null);
  v.graph={sensitiveCachedGeometry:true};
  stream.onState('unauthorized');
  assert.equal(v.graph,null,'a mounted cached picture disappears immediately on 401/403');
  assert.equal(signIn,1);
  assert.equal(cacheClears,1);
  await assert.rejects(stream.onSnapshot(input(R,E,'10',['different']),signal),e=>e.name==='AbortError');
  assert.deepEqual(events,['owner-live-snapshot','owner-live-snapshot']);
});

test('actual packed scene captures current pose after async validation and restores all camera fields',async()=>{
  const files=await versionableBundle(new URL('.',import.meta.url).pathname);
  const js=files['assets/index-Byv8wmN9.js'].toString();
  const scene=js.slice(js.indexOf('async setGraph(e,t,r,i,a=!1'),js.indexOf('acceptGestureRoute('));
  assert.match(scene,/isCurrent.+ownerGraphCamera=s\.preserveCamera\?preserveOwnerCamera\(this\.camera,this\.controls\)/s);
  assert.match(scene,/a&&this\.reset\(\),ownerGraphCamera\?\.\(\),this\.cutAt=/);
  const vec=x=>({x,clone(){return vec(this.x)},copy(v){this.x=v.x}});
  const cam={position:vec(4),quaternion:vec(7),zoom:4,near:2,far:900,updateProjectionMatrix(){},updateMatrixWorld(){}};
  const controls={target:vec(17),minDistance:10,maxDistance:100};
  const restore=preserveOwnerCamera(cam,controls);
  cam.position.x=99;cam.quaternion.x=90;cam.zoom=20;controls.target.x=0;cam.far=20;controls.minDistance=0;
  restore();
  assert.deepEqual([cam.position.x,cam.quaternion.x,cam.zoom,cam.near,cam.far,controls.target.x,controls.minDistance,controls.maxDistance],
    [4,7,4,2,900,17,10,100]);
});


test('packed new Visual uses the unchanged legacy bootstrap on an old Base contract',async()=>{
  const files=await versionableBundle(new URL('.',import.meta.url).pathname);
  const js=files['assets/index-Byv8wmN9.js'].toString();
  const start=js.indexOf('bootstrapReleaseOwnerGraph(){'),end=js.indexOf('\nstopReleaseOwnerLive(){',start);
  assert.ok(start>0&&end>start,'packed startup method must be independently observable');
  const calls=[];
  const Viewer=vm.runInNewContext('class Viewer{'+js.slice(start,end)+'}; Viewer',{
    releaseFirstOwnerSnapshot:callback=>{
      calls.push('legacy-bootstrap');
      return {fallback:()=>{calls.push('legacy-snapshot');return callback(null)},ready:Promise.resolve()};
    },
    dispatchEvent:()=>{},CustomEvent:class{constructor(type){this.type=type}},
  });
  const v=new Viewer();
  Object.assign(v,{authenticated:true,capabilities:{capabilities:{live:true},owner_live_bootstrap:1},
    currentContext:()=>({selector:{mode:'current'}}),
    openGraphWire:()=>assert.fail('old Base must receive no graph-v1 request'),
    stopReleaseOwnerLive:()=>{},ensureReleaseScene:()=>{},setConnection:()=>{},
    loadGraph:()=>calls.push('legacy-read')});
  await v.bootstrapReleaseOwnerGraph();
  assert.deepEqual(calls,['legacy-bootstrap','legacy-snapshot','legacy-read']);
});
