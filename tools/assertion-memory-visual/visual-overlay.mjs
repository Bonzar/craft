import {readFile} from 'node:fs/promises';
import {hash} from './store.mjs';
const [queueHtml, queueScript, queueCss]=await Promise.all(['queue-dashboard.html','queue-dashboard.js','queue-dashboard.css'].map(name=>readFile(new URL(name,import.meta.url),'utf8')));
// The pinned bundle still owns the Connections renderer and worker.
const TAG = /^assertion-memory-visual-v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]{1,30})?$/;
function once(input, from, to) {
  if (input.split(from).length !== 2) throw Error('Pinned viewer integration changed: ' + from.slice(0,60));
  return input.replace(from, () => to);
}
export function patchVisual({html,script,css,releaseTag=null}) {
  if(releaseTag!==null&&(typeof releaseTag!=='string'||!TAG.test(releaseTag)||/\s/.test(releaseTag)))throw Error('Invalid Visual release tag');
  script=once(script,'graphMode=e(new URLSearchParams(location.search));','graphMode=`connections`;');
  script=once(script,'let e=Z(this.graphMode===`connections`?`⬡ Обзоры`:`⬡ Связи`,()=>this.run(()=>this.toggleGraphMode()));e.id=`layout-toggle`,','let e=Z(`Связи`,()=>{});e.hidden=true;e.disabled=true;e.id=`layout-toggle`,');
  // The exact pinned Connections client must reload even a historical page
  // after a committed clear. Do not replace its renderer or layout worker.
  script=once(script,'let t=_d(e,fe);wd(`sse-reset`','let t=_d(e,fe);if(t.reason===`database_cleared`){this.stream?.close();location.reload();return;}wd(`sse-reset`');
  html=once(html,'id="topics-toggle" aria-label=','id="topics-toggle" hidden aria-label=');
  if(releaseTag!==null) {
    html=once(html,'</body>','<div class="visual-release-version">Визуал: '+releaseTag+'</div>\n</body>');
  }
  html=once(html,'</body>',queueHtml+'\n</body>');
  script+='\n'+queueScript;
  css+='\n'+queueCss;
  css+='\n#layout-toggle[hidden],#topics-toggle[hidden]{display:none!important}\n'+
    '.visual-release-version{position:fixed;bottom:12px;left:12px;z-index:3;background:#101728;color:#e6eafa;border:1px solid #526086;border-radius:7px;padding:5px 8px;font:12px system-ui;pointer-events:none}';
  return {html,script,css};
}

// The release archive still uses a separate pinned renderer. Adapt only its
// controller seams; do not replace its renderer, Worker, or Safari boot fixes.
const LIVE_SEED='9a0cef2d67103909cd18a81d37aadeb62659622e2f21f56225b43d5fff0b791d';
const liveDirect=await readFile(new URL('../../apps/graph_viewer/web/src/direct-stream.mjs',import.meta.url),'utf8');
const liveController=await readFile(new URL('../../apps/graph_viewer/web/src/owner-live-controller.mjs',import.meta.url),'utf8');
export function ownerLivePrelude(){
  const direct=once(once(liveDirect,'export function directStreamTarget(','function directStreamTarget('),'export async function readDirectStream(','async function readDirectStream(');
  const controller=once(once(liveController,"import {readDirectStream} from './direct-stream.mjs';",''),'export function openOwnerLive(','function openOwnerLive(');
  return 'const releaseOpenOwnerLive=(()=>{\n'+direct+'\n'+controller+'\nreturn openOwnerLive;\n})();\n';
}

const releaseScene='if(!this.scene)try{this.scene=new cm(this.canvas,$(`topic-labels`,HTMLElement),e=>this.run(()=>this.picked(e)),e=>this.openTopic(e),(e,t)=>this.feedback(e,t),e=>this.handleError(e),e=>this.updateRepresentation(e),e=>this.run(()=>this.openNode(e.id)),e=>this.updateReaderConnections(e))}catch{throw Error(`Для вселенной нужен браузер с WebGL 2. Проверьте аппаратное ускорение.`)}';
export const ownerLiveMethods=`
ensureReleaseScene(){${releaseScene}}
stopReleaseOwnerLive(){this.releaseOwnerLive?.close();this.releaseOwnerLive=null;this.releaseOwnerLiveKey=null;}
startReleaseOwnerLive(){
  const cfg=this.capabilities?.owner_live_transport;
  if(!this.authenticated||!this.csrf||cfg?.version!==1||cfg?.lane!=='test'||typeof cfg.origin!=='string')return false;
  let target;try{target=new URL(cfg.origin);}catch{return false;}
  if(target.origin!==cfg.origin||target.protocol!=='https:'||target.port||!/^\\d+\\.\\d+\\.\\d+\\.\\d+$/.test(target.hostname))return false;
  const key=JSON.stringify([cfg.origin,this.capabilities.provider_id,this.currentContext()]);
  if(this.releaseOwnerLive&&this.releaseOwnerLiveKey===key)return true;
  this.stopReleaseOwnerLive();this.releaseOwnerLiveKey=key;
  const current=()=>this.releaseOwnerLiveKey===key&&this.authenticated&&!this.changingContext;
  this.releaseOwnerLive=releaseOpenOwnerLive({
    allowedOrigin:cfg.origin,csrf:this.csrf,
    onState:state=>{
      if(!current())return;
      this.setConnection(state==='live'?(this.selector.mode==='known_then'?'Срез закреплён · связь есть':'Live · обновляется'):state==='connecting'?'Подключаемся к live-потоку':'Нет live-связи',state==='live');
    },
    onRefresh:async reason=>{
      if(this.graphLoading)await this.graphLoading;
      if(!current())return;
      if(reason==='reset'){
        this.cardRequest?.abort();this.searchRequest?.abort();this.clearContextSelection();
        this.worker?.dispose();this.worker=null;this.scene?.dispose();this.scene=null;
        this.graph=null;this.manifest=null;this.topics=null;this.cursor='';
        this.loading.hidden=false;
        this.ensureReleaseScene();
      }else if(this.selector.mode==='known_then')return;
      await this.loadGraph(undefined,true);
    },
  });return true;
}
`;
export function patchOwnerLive(script,rawSeed){
  if(hash(Buffer.from(rawSeed))!==LIVE_SEED)throw Error('Owner live overlay seed changed; re-audit the renderer');
  const replace=(from,to)=>{script=once(script,from,to);};
  // The pinned schema strips unknown fields by default. Keep this new field
  // opaque here and validate narrowly only when selecting the transport.
  replace('ce=p({provider_id:i,capabilities:', 'ce=p({provider_id:i,owner_live_transport:d(c,null),capabilities:');
  replace('this.changingContext=!0,this.stream?.close()', 'this.changingContext=!0,this.stopReleaseOwnerLive(),this.stream?.close()');
  replace('this.loading.hidden=!0,this.stream?.close()', 'this.loading.hidden=!0,this.stopReleaseOwnerLive(),this.stream?.close()');
  replace('this.cardRequest?.abort(),this.searchRequest?.abort(),this.stream?.close()', 'this.cardRequest?.abort(),this.searchRequest?.abort(),this.stopReleaseOwnerLive(),this.stream?.close()');
  replace('async start(){if(this.authenticated=', 'async start(){this.authenticated=');
  replace(',!this.scene)try{'+releaseScene.split('try{')[1]+'this.capabilities=await', ';this.ensureReleaseScene();this.capabilities=await');
  replace('loadGraph(e){if(wd(', 'loadGraph(e,preserveOwnerLive=false){if(wd(');
  replace('this.fetchGraph(e??this.currentContext())','this.fetchGraph(e??this.currentContext(),preserveOwnerLive)');
  replace('async fetchGraph(e){','async fetchGraph(e,preserveOwnerLive=false){');
  replace('this.graphRequest=t,this.stream?.close(),this.stream=null,this.setConnection(this.changingContext?', 'this.graphRequest=t,preserveOwnerLive||this.stopReleaseOwnerLive(),this.stream?.close(),this.stream=null,preserveOwnerLive||this.setConnection(this.changingContext?');
  replace('startLive(){', ownerLiveMethods+'startLive(){');
  replace('this.capabilities.provider_id!==this.manifest.provider_id){this.setConnection', 'this.capabilities.provider_id!==this.manifest.provider_id){this.stopReleaseOwnerLive();this.setConnection');
  replace('if(!e.subscribe){this.setConnection','if(!e.subscribe){this.stopReleaseOwnerLive();this.setConnection');
  replace('let t=new EventSource(pd(`/events`', 'if(this.startReleaseOwnerLive())return;this.stopReleaseOwnerLive();let t=new EventSource(pd(`/events`');
  return ownerLivePrelude()+script;
}
