// Optional aggregate-only instrumentation; no source text, cookies or bodies.
export async function installPhaseObserver(page){
 await page.addInitScript(()=>{
  const now=()=>performance.timeOrigin+performance.now();const timeline={events:[],workers:[],status:null};window.__qaLivePhases=timeline;
  const Source=window.EventSource;
  window.EventSource=class extends Source{constructor(...args){super(...args);for(const type of['hello','delta','reset','unavailable'])this.addEventListener(type,event=>{try{const data=JSON.parse(event.data);timeline.events.push({type,at_ms:now(),cursor:data.cursor,revision:data.revision,base_revision:data.base_revision,node_upserts:data.upsert_nodes?.length,edge_upserts:data.upsert_edges?.length});}catch{timeline.events.push({type,at_ms:now(),parse_error:true});}});}};
  const WorkerClass=window.Worker;let serial=0;
  window.Worker=class extends WorkerClass{
   constructor(...args){super(...args);this.__qaId=++serial;this.__qaRequests=new Map();this.addEventListener('message',event=>{const request=this.__qaRequests.get(event.data?.id);if(!request)return;timeline.workers.push({phase:'reply',worker:this.__qaId,id:event.data.id,kind:request.kind,revision:request.revision,at_ms:now(),elapsed_ms:now()-request.at_ms,error:Boolean(event.data.error)});this.__qaRequests.delete(event.data.id);});}
   postMessage(...args){const message=args[0];const value={phase:'send',worker:this.__qaId,id:message?.id,kind:message?.kind,revision:message?.revision??message?.request?.revision,at_ms:now()};this.__qaRequests.set(message?.id,value);timeline.workers.push(value);return Reflect.apply(WorkerClass.prototype.postMessage,this,args);}
  };
  const begin=()=>new MutationObserver(()=>{if(timeline.status)return;const button=Array.from(document.querySelectorAll('button')).find(node=>node.textContent==='Перейти к замене'&&node.getClientRects().length);if(button){timeline.status={dom_ms:now()};requestAnimationFrame(()=>{timeline.status.raf1_ms=now();requestAnimationFrame(()=>{timeline.status.raf2_ms=now();});});}}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','class']});
  if(document.body)begin();else document.addEventListener('DOMContentLoaded',begin,{once:true});
 });
}
