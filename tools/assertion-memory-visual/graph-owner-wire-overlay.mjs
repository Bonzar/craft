
import {readFile} from 'node:fs/promises';
const root='../../apps/graph_viewer/web/src/';
const source=await Promise.all(['owner-graph-wire.mjs','owner-graph-cache.mjs','owner-graph-session.mjs','owner-graph-position.mjs']
  .map(name=>readFile(new URL(root+name,import.meta.url),'utf8')));
const lineImports=/^import .* from ['"].*['"];\r?\n/gm;
const exports=/^export /gm;
function moduleText(index,names,prepare=''){
  return '(()=>{'+prepare+source[index].replace(lineImports,'').replace(exports,'')+'\nreturn {'+names.join(',')+'};})()';
}
export const graphWirePrelude='const {openOwnerGraphSession,ownerGraphCache,browserOwnerTopologyStore,ownerGraphCheckpointKey,extendOwnerGraphPositions,ownerGraphDeltaIds,preserveOwnerCamera}=(()=>{'+
  'const wire='+moduleText(0,['ownerGraphReconciler','readOwnerGraphWire'])+';'+
  'const cache='+moduleText(1,['ownerGraphCache','browserOwnerTopologyStore','ownerGraphCheckpointKey'])+';'+
  'const session='+moduleText(2,['openOwnerGraphSession'],'const {ownerGraphReconciler,readOwnerGraphWire}=wire;')+';'+
  'const position='+moduleText(3,['extendOwnerGraphPositions','ownerGraphDeltaIds','preserveOwnerCamera'])+';return {...session,...cache,...position};})();\n';

export const graphWireMethods=`
readOwnerGraphAuth(state){
  this.releaseGraphOwner=typeof state.graph_cache_scope==='string'&&/^[a-f0-9]{64}$/.test(state.graph_cache_scope)?state.graph_cache_scope:null;
  this.releaseGraphStage=state.graph_cache_stage==='test'||state.graph_cache_stage==='prod'?state.graph_cache_stage:null;
  this.releaseGraphEpoch=typeof state.graph_cache_epoch==='string'&&/^[a-f0-9]{64}$/.test(state.graph_cache_epoch)?state.graph_cache_epoch:null;
}
closeGraphWire(){
  this.releaseGraphWire?.close();this.releaseGraphWire=null;this.releaseGraphWireKey=null;
}
discardGraphWireCache(){
  this.closeGraphWire();this.releaseGraphPlacementState=null;this.releaseGraphOwner=null;this.releaseGraphStage=null;this.releaseGraphEpoch=null;
  void (this.releaseGraphCache??=ownerGraphCache(browserOwnerTopologyStore())).clear();
}
openGraphWire(context){
  const config=this.capabilities;
  if(config?.owner_live_graph_v1!==true||!this.authenticated)return null;
  this.releaseOwnerStartupPending=false;
  this.graphRequest?.abort();this.stream?.close();this.stream=null;this.stopReleaseOwnerLive();this.closeGraphWire();
  const selector=context.selector,provider=config.provider_id,origin=location.origin;
  const scope=ownerGraphCheckpointKey({origin,stage:this.releaseGraphStage,owner:this.releaseGraphOwner,provider,selector});
  const key=Symbol(),viewKey=scope??JSON.stringify([origin,provider,selector]);
  this.releaseGraphWireKey=key;
  const cache=this.releaseGraphCache??=ownerGraphCache(browserOwnerTopologyStore());
  const current=signal=>this.authenticated&&this.releaseGraphWireKey===key&&!signal.aborted;
  const selectors=['mode','at','history','undated','session_id','run_id','output_snapshot_id'];
  const matches=manifest=>manifest.provider_id===provider&&selectors.every(k=>(manifest.selector[k]??null)===(selector[k]??null));
  const empty=()=>({upsert_nodes:[],upsert_edges:[],remove_node_ordinals:[],remove_edge_ordinals:[],status_changes:[]});
  const apply=async (frame,signal,kind)=>{
    if(!current(signal))throw new DOMException('Closed','AbortError');
    const previous=this.releaseGraphPlacementState;
    let manifest,topics,graph,ids;const next=new lm;
    try{
      if(kind==='delta'){
        const delta=ue.parse(frame), old=this.manifest;
        if(!old||!this.graph||!this.topics||previous?.epoch!==frame.epoch||previous?.scope!==viewKey||
            delta.base_revision!==old.revision)throw Error('Graph delta scope differs');
        const membership=delta.topic_changes.length?ge(delta,old,this.topics):null;
        if(delta.topic_changes.length&&!membership)throw Error('Invalid graph membership update');
        topics=membership?.topics??{...this.topics,revision:delta.revision};
        const supplied=membership?.manifest??he(delta,old);
        if(delta.manifest&&!supplied)throw Error('Invalid graph update manifest');
        graph=await next.loadPatchedGraph(this.graph,delta,selector.history,topics,delta.revision);
        ids=ownerGraphDeltaIds(previous.ids,delta);
        manifest={...(supplied??old),revision:delta.revision,source_revision:delta.source_revision,captured_at:delta.captured_at,
          live_cursor:frame.cursor,owner_live_epoch:frame.epoch,
          counts:supplied?.counts??{...old.counts,nodes:graph.flags.length,edges:graph.edgeActive.length,
            visible_nodes:graph.visible.reduce((a,x)=>a+x,0),visible_edges:graph.visibleEdges.length/2}};
      }else{
        manifest=w.parse(kind==='cached'?frame.view?.manifest:frame.manifest);
        topics=j.parse(kind==='cached'?frame.view?.topics:frame.topics);
        ids=frame.ids;
        if(kind==='cached')graph=await next.loadPatchedGraph(frame.graph,empty(),selector.history,topics,manifest.revision);
        else graph=await next.load(frame.nodes,frame.edges,manifest.counts.nodes,manifest.counts.edges,selector.history,topics,manifest.revision);
      }
      if(!current(signal)||!matches(manifest)||manifest.owner_live_epoch!==frame.epoch||manifest.revision!==frame.revision||topics.revision!==frame.revision||
          ids.length!==graph.flags.length||manifest.counts.nodes!==graph.flags.length||manifest.counts.edges!==graph.edgeActive.length||
          manifest.counts.visible_nodes!==graph.visible.reduce((a,x)=>a+x,0)||manifest.counts.visible_edges!==graph.visibleEdges.length/2)
        throw Error('Graph data differs from authenticated view');
      this.ensureReleaseScene();
      const placement={scope:viewKey,epoch:frame.epoch,ids,previous,
        cachedPositions:kind==='cached'?frame.visualPositions:null,result:null};
      const preserve=previous?.scope===viewKey&&previous?.epoch===frame.epoch&&!!this.graph;
      const rebound=await this.presentGraph(graph,manifest,topics,next,!this.graph,()=>current(signal),signal,preserve,placement);
      if(!current(signal)||!(placement.result instanceof Float32Array))throw new DOMException('Closed','AbortError');
      this.worker?.dispose();this.worker=next;this.graph=graph;this.manifest=manifest;this.topics=topics;this.cursor=frame.cursor;
      this.releaseGraphPlacementState={scope:viewKey,epoch:frame.epoch,ids,positions:placement.result};
      this.selector={...manifest.selector};this.acceptedContext={selector:{...manifest.selector},day:context.day,zone:context.zone};
      this.sessionFilter?.bind(manifest);this.applyContextInputs(this.acceptedContext);this.updateCounts();
      this.selectedOrdinal=rebound??null;this.updateReaderConnections();this.loading.hidden=true;
      if(kind!=='cached')globalThis.dispatchEvent?.(new CustomEvent('owner-live-snapshot',{detail:{epoch:frame.epoch,queue:false}}));
      return {epoch:frame.epoch,revision:frame.revision,cursor:frame.cursor,ids,graph,visualPositions:placement.result,view:{manifest,topics}};
    }catch(error){if(this.worker!==next)next.dispose();throw error;}
  };
  const session=openOwnerGraphSession({selector,scope,verifiedEpoch:this.releaseGraphEpoch,cache:{
      take:async key=>{const value=await cache.take(key);return value?.view?value:null;},
      save:(key,value)=>cache.save(key,value),clear:()=>cache.clear(),
    },
    onCached:(value,signal)=>apply(value,signal,'cached'),onSnapshot:(value,signal)=>apply(value,signal,'baseline'),
    onDelta:(value,signal)=>apply(value,signal,'delta'),
    onReset:({epoch},signal)=>{
      if(!current(signal))return;
      if(this.releaseGraphPlacementState?.epoch!==epoch){this.releaseGraphPlacementState=null;this.clearReleaseOwnerLiveGraph();this.loading.hidden=true;}
    },
    onState:(state)=>{
      if(this.releaseGraphWireKey!==key)return;
      if(state==='unauthorized'){this.discardGraphWireCache();this.clearReleaseOwnerLiveGraph();this.showLogin();return;}
      const text={live:'Live · обновляется',connecting:'Подключаемся к live-потоку',
         'snapshot-connecting':'Снимок · подключаемся к Live','snapshot-offline':'Нет live-связи · снимок',offline:'Нет live-связи'};
      this.setConnection(text[state]??'Нет live-связи',state==='live');
    }});
  this.releaseGraphWire=session;return session.initial;
}
async graphWirePlacement(graph,worker,options){
  if(!options)return worker.forceLayout(graph);
  if(options.cachedPositions){options.result=options.cachedPositions.slice();return options.result;}
  const {scope,epoch,ids,previous}=options;
  const same=previous?.scope===scope&&previous?.epoch===epoch;
  const proposal=same?graph.positions:await worker.forceLayout(graph);
  options.result=extendOwnerGraphPositions(previous,{scope,epoch,ids,positions:proposal,edges:graph.edges,edgeActive:graph.edgeActive});
  return options.result;
}
`;

export function patchGraphWire(script,once){
  const replace=(from,to)=>{script=once(script,from,to);};
  replace('owner_live_bootstrap:d(c,null),capabilities:', 'owner_live_bootstrap:d(c,null),owner_live_graph_v1:d(s,!1),capabilities:');
  replace('csrf_token:d(i,``)}),ue=', 'csrf_token:d(i,``),graph_cache_scope:d(c,null),graph_cache_stage:d(c,null),graph_cache_epoch:d(c,null)}),ue=');
  replace('this.csrf=e.csrf_token,await this.start()', 'this.readOwnerGraphAuth(e),this.csrf=e.csrf_token,await this.start()');
  replace('this.csrf=t.csrf_token,e.value=``,await this.start()', 'this.readOwnerGraphAuth(t),this.csrf=t.csrf_token,e.value=``,await this.start()');
  replace('this.loading.hidden=!0,this.stopReleaseOwnerLive(),this.stream?.close()', 'this.loading.hidden=!0,this.discardGraphWireCache(),this.stopReleaseOwnerLive(),this.stream?.close()');
  replace('this.cardRequest?.abort(),this.searchRequest?.abort(),this.stopReleaseOwnerLive(),this.stream?.close()','this.cardRequest?.abort(),this.searchRequest?.abort(),this.discardGraphWireCache(),this.stopReleaseOwnerLive(),this.stream?.close()');
  replace('startLive(){', graphWireMethods+'startLive(){if(this.capabilities?.owner_live_graph_v1===true)return;');
  replace('bootstrapReleaseOwnerGraph(){\n  const boot=', 'bootstrapReleaseOwnerGraph(){\n  if(this.capabilities?.owner_live_graph_v1===true)return this.openGraphWire(this.currentContext());\n  const boot=');
  replace('loadGraph(e,preserveOwnerLive=false){if(this.releaseOwnerStartupPending)return Promise.resolve();','loadGraph(e,preserveOwnerLive=false){if(this.releaseOwnerStartupPending)return Promise.resolve();if(this.capabilities?.owner_live_graph_v1===true)return this.openGraphWire(e??this.currentContext())??Promise.resolve();');
  replace('async presentGraph(e,n,r,i,a,o,s,c=!1){', 'async presentGraph(e,n,r,i,a,o,s,c=!1,placement=null){');
  replace('t(e,r,await i.forceLayout(e)):', 't(e,r,await this.graphWirePlacement(e,i,placement)):');
  replace('individualMode:this.graphMode===`connections`,calibration:l.calibration,selectedNodeId:t,',
    'individualMode:this.graphMode===`connections`,preserveCamera:!!placement&&c,calibration:l.calibration,selectedNodeId:t,');
  // Capture at the synchronous renderer commit, never before an awaited
  // preparation. This preserves user gestures made while the new graph stages.
  replace('this.controller.dispose(),this.controller=d,l||',
    'let ownerGraphCamera=s.preserveCamera?preserveOwnerCamera(this.camera,this.controls):null;this.controller.dispose(),this.controller=d,l||');
  replace('a&&this.reset(),this.cutAt=-1/0,this.updateCut(performance.now()),h',
    'a&&this.reset(),ownerGraphCamera?.(),this.cutAt=-1/0,this.updateCut(performance.now()),h');
  return graphWirePrelude+script;
}
