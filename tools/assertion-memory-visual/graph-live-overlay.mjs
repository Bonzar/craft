import {LiveFallback,POLL_INTERVAL_MS,SSE_START_MS,SSE_IDLE_MS}
  from '../../apps/graph_viewer/web/src/live-fallback.mjs';

// The pinned viewer seed is compiled independently. Use exact audited anchors
// so a new/unknown seed fails packaging rather than silently losing fallback.
export function patchGraphLive(source,once){
  let script=source;
  const start='startLive(){if(this.changingContext||(this.stream?.close(),this.stream=null,!this.manifest||!this.capabilities))return;';
  script=once(script,start,start.replace('this.stream?.close(),this.stream=null','stopGatewayGraphLive(this)'));
  const stream='let t=new EventSource(pd(`/events`,{after:this.cursor,revision:this.manifest.revision}));this.stream=t;let n=e=>{this.stream===t&&!this.changingContext&&this.event(e)};';
  script=once(script,stream,'if(this.gatewayPollOnly||typeof EventSource===`undefined`){startGatewayGraphPoll(this);return}'+
    stream.replace('this.stream=t;','this.stream=t;let gateway=watchGatewayGraphStream(this,t);').replace('&&this.event(e)','&&(gateway(),this.event(e))'));
  const error='t.onerror=()=>{this.stream===t&&this.setConnection(`Нет live-связи · данные от ${Me(this.manifest?.captured_at??null,`instant`)}`,!1)}';
  script=once(script,error,'t.onerror=()=>fallbackGatewayGraph(this,t)');
  for(const anchor of [
    'this.changingContext=!0,this.stream?.close(),this.stream=null,this.clearContextSelection()',
    'this.graphRequest=t,this.stream?.close(),this.stream=null,this.setConnection(',
    'this.loading.hidden=!0,this.stream?.close(),$(`logout`,HTMLButtonElement)',
    'this.searchRequest?.abort(),this.stream?.close(),this.worker?.dispose()'
  ])script=once(script,anchor,anchor.replace('this.stream?.close()','stopGatewayGraphLive(this)').replace(',this.stream=null',''));

  // Declarations are hoisted even if the seed constructs Viewer immediately.
  return script+'\n'+gatewayGraphProgram();
}
export function gatewayGraphProgram(){
  return [
    stopGatewayGraphLive,makeGatewayGraphPolling,watchGatewayGraphStream,
    fallbackGatewayGraph,startGatewayGraphPoll
  ].map(f=>f.toString()).join('\n').replace('__LIVE_POLL_CLASS__',()=>LiveFallback.toString().replace('interval=POLL_INTERVAL_MS',`interval=${POLL_INTERVAL_MS}`)).replaceAll('__SSE_START_MS__',String(SSE_START_MS)).replaceAll('__SSE_IDLE_MS__',String(SSE_IDLE_MS)).replaceAll('__POLL_SECONDS__',String(POLL_INTERVAL_MS/1000)).replaceAll('__POLL_INTERVAL_MS__',String(POLL_INTERVAL_MS));
}
function stopGatewayGraphLive(viewer){
  viewer.stream?.close();viewer.stream=null;clearTimeout(viewer.gatewayLiveDeadline);
  viewer.gatewayLivePoll?.stop();
}
function makeGatewayGraphPolling(){
  return new (__LIVE_POLL_CLASS__)();
}
function watchGatewayGraphStream(viewer,stream){
  const arm=ms=>{
    clearTimeout(viewer.gatewayLiveDeadline);
    viewer.gatewayLiveDeadline=setTimeout(()=>fallbackGatewayGraph(viewer,stream),ms);
  };
  arm(__SSE_START_MS__);
  return ()=>arm(__SSE_IDLE_MS__);
}
function fallbackGatewayGraph(viewer,stream){
  if(viewer.stream!==stream)return;
  stopGatewayGraphLive(viewer);viewer.gatewayPollOnly=true;startGatewayGraphPoll(viewer);
}
function startGatewayGraphPoll(viewer){
  if(viewer.selector.mode==='known_then'){viewer.setConnection('Срез закреплён',true);return;}
  viewer.setConnection('Проверяем обновления графа…',false);
  (viewer.gatewayLivePoll??=makeGatewayGraphPolling()).start(async signal=>{
    const current=viewer.manifest, selector=viewer.selector;
    const valid=()=>!signal.aborted&&viewer.authenticated&&!viewer.changingContext
      &&!document.hidden&&viewer.manifest===current&&viewer.selector===selector;
    if(!valid()||!current)return;
    const response=await md(pd('/graph',{mode:selector.mode,at:selector.at,history:+!!selector.history,
      undated:selector.undated,session_id:selector.session_id,run_id:selector.run_id,output_snapshot_id:selector.output_snapshot_id}),signal);
    if(!valid())return;
    if(response.status===202){viewer.setConnection('Граф готовит новый срез · идёт проверка',false);return;}
    const next=w.parse(await response.json());
    if(!valid())return;
    if(next.provider_id!==current.provider_id||(next.selector.session_id??null)!==(selector.session_id??null)
      ||(next.selector.run_id??null)!==(selector.run_id??null)
      ||(selector.output_snapshot_id!=null&&next.session_filter?.overview_history?.snapshot_id!==selector.output_snapshot_id))
      throw Error('Источник или выбранный срез изменился');
    viewer.setConnection('Проверка обновлений каждые '+__POLL_SECONDS__+' с',true);
    document.getElementById('live-dot').dataset.transport='poll';
    if(next.revision!==current.revision)await viewer.loadGraph();
  },error=>{
    if(error?.status===401||error?.status===403){viewer.showLogin();return;}
    viewer.setConnection('Обновления недоступны · данные от '+Me(viewer.manifest?.captured_at??null,'instant'),false);
  },__POLL_INTERVAL_MS__);
}
