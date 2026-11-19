import { apiUrl, get } from './api.ts';
import { nodeSchema } from './contracts.ts';
import type { Manifest } from './contracts.ts';
import { button, element } from './dom.ts';
import { viewBindingMatches } from './focus.ts';
import type { NearCandidate, ViewBinding } from './focus.ts';
import { PreviewCache, previewFromCard } from './preview-cache.ts';
import type { PreviewEntry } from './preview-cache.ts';
import { lifecycle } from './lifecycle.ts';
import type { Projection } from './scene.ts';
type Rect=readonly[number,number,number,number];
interface Label {root:HTMLElement;anchor:HTMLElement;leader:HTMLElement;body:HTMLElement;counts:HTMLElement;read:HTMLButtonElement;ordinal:number;entry:PreviewEntry|null;pressed:PreviewEntry|null;width:number;height:number;dirty:boolean;active:boolean}
interface Frame {binding:ViewBinding;candidates:NearCandidate[];show:boolean;blocked:readonly Rect[]}
export class NearText {
  private fetchingPaused=false;private suppressed=false;private dockRect:Rect|null=null;private visibleBefore=0;private cache=new PreviewCache();private context:Manifest|null=null;private frame:Frame|null=null;private expected:ViewBinding|null=null;
  private labels:Label[]=[];private active=new Map<string,{controller:AbortController;ordinal:number}>();private failed=new Map<string,number>();private disposed=false;private focus:number|null=null;private transferred=0;
  private connectionInfo:(ordinal:number)=>string|null;private project:(ordinal:number)=>Projection;private onRead:(entry:PreviewEntry)=>void;private onError:(error:unknown)=>void;
  constructor(root:HTMLElement,project:(ordinal:number)=>Projection,onRead:(entry:PreviewEntry)=>void,onError:(error:unknown)=>void,connectionInfo:(ordinal:number)=>string|null=()=>null){
    this.connectionInfo=connectionInfo;this.project=project;this.onRead=onRead;this.onError=onError;
    for(let i=0;i<1;i++){const box=element('section','','near-preview');const anchor=element('div','','near-anchor');const leader=element('div','','near-leader');const body=element('p');const counts=element('small','','near-connections');const read=button('Читать целиком',()=>{const entry=item.pressed??item.entry;item.pressed=null;if(entry&&entry.provider_id===this.context?.provider_id&&entry.revision===this.context?.revision)this.onRead(entry);});const item:Label={root:box,anchor,leader,body,counts,read,ordinal:0,entry:null,pressed:null,width:260,height:120,dirty:true,active:false};read.addEventListener('pointerdown',()=>{item.pressed=item.entry;});box.setAttribute('aria-label','Предпросмотр утверждения');box.append(counts,body,read);box.hidden=true;anchor.hidden=true;leader.hidden=true;root.append(leader,anchor,box);this.labels.push(item);}
  }
  setContext(manifest:Manifest):void{if(this.context?.revision===manifest.revision&&this.context.provider_id===manifest.provider_id){this.context=manifest;return;}this.abort();this.cache.clear();this.failed.clear();this.frame=null;this.expected=null;this.focus=null;this.context=manifest;this.hide();}
  pauseFetches():void{this.fetchingPaused=true;this.abort();this.active.clear();}
  resumeFetches():void{this.fetchingPaused=false;this.pump();}
  get focusedEntry():PreviewEntry|null{const ordinal=this.focus;return ordinal!==null&&this.context?this.cache.get(this.context.provider_id,this.context.revision,ordinal):null;}
  prime(entries:readonly PreviewEntry[]):void{for(const entry of entries)if(entry.provider_id===this.context?.provider_id&&entry.revision===this.context.revision)this.cache.put(entry);}
  setSuppressed(value:boolean):void{if(this.suppressed===value)return;this.suppressed=value;lifecycle('preview-owner',{reader:value});if(value)this.hide();else this.render();}
  get occupiedRects():readonly Rect[]{return this.dockRect?[this.dockRect]:[];}
  expect(binding:ViewBinding):void{this.expected=binding;}
  invalidate():void{lifecycle('preview-invalidate');this.frame=null;this.expected=null;this.hide();}
  setFrame(binding:ViewBinding,candidates:readonly NearCandidate[],show:boolean,blocked:readonly Rect[]):void{
    if(binding.revision!==this.context?.revision||!this.expected||!viewBindingMatches(this.expected,binding))return;
    const ordered=[...candidates];const previous=ordered.find(candidate=>candidate.ordinal===this.focus);const best=ordered[0];if(previous&&best&&previous.score<=best.score+12){ordered.splice(ordered.indexOf(previous),1);ordered.unshift(previous);}this.focus=ordered[0]?.ordinal??null;
    this.frame={binding,candidates:ordered.slice(0,3),show,blocked};const wanted=new Set(this.frame.candidates.map(candidate=>String(candidate.ordinal)));
    for(const job of this.active.values())if(!wanted.has(String(job.ordinal)))job.controller.abort();this.render();this.pump();
  }
  private abort():void{for(const job of this.active.values())job.controller.abort();}
  private pump():void{
    const frame=this.frame;const context=this.context;if(this.disposed||this.fetchingPaused||!frame||!context)return;
    for(const candidate of frame.candidates){if(this.active.size>=2)break;const key=`${context.revision}:${candidate.ordinal}`;if(this.cache.get(context.provider_id,context.revision,candidate.ordinal)||this.active.has(key)||Date.now()-(this.failed.get(key)??0)<5000)continue;
      const controller=new AbortController();const job={controller,ordinal:candidate.ordinal};this.active.set(key,job);
      get(apiUrl(`/graph/${encodeURIComponent(context.revision)}/nodes/${candidate.ordinal}`,{offset:0,limit:480}),nodeSchema,controller.signal).then(card=>{
        if(controller.signal.aborted||this.context?.revision!==context.revision||this.context.provider_id!==context.provider_id)return;
        if(card.revision!==context.revision||card.ordinal!==candidate.ordinal)throw new Error('Источник вернул текст другой звезды');
        const preview=previewFromCard(card,context.provider_id,context.revision);this.transferred+=new TextEncoder().encode(JSON.stringify(card)).byteLength;this.cache.put(preview);this.render();
      }).catch(error=>{if(controller.signal.aborted)return;this.failed.set(key,Date.now());this.onError(error);this.render();}).finally(()=>{if(this.active.get(key)===job)this.active.delete(key);this.pump();});
    }
  }
  private hide():void{this.dockRect=null;if(this.visibleBefore){this.visibleBefore=0;lifecycle('preview-hide');}for(const label of this.labels){label.root.hidden=true;label.anchor.hidden=true;label.leader.hidden=true;label.active=false;}}
  private render():void{
    const frame=this.frame;const context=this.context;if(this.suppressed||!frame||!context||!frame.show){this.hide();return;}if(!this.expected||!viewBindingMatches(frame.binding,this.expected))return;
    const limit=1;
    for(let i=0;i<this.labels.length;i++){const label=this.labels[i];const candidate=frame.candidates[i];if(!label)continue;if(!candidate||i>=limit){label.active=false;label.root.hidden=true;label.anchor.hidden=true;label.leader.hidden=true;continue;}
      const entry=this.cache.get(context.provider_id,context.revision,candidate.ordinal);const points=entry?Array.from(entry.text).slice(0,window.innerWidth<700?240:480):[];const text=entry?points.join('')+(entry.text_total>points.length?'…':''):this.failed.has(`${context.revision}:${candidate.ordinal}`)?'Текст пока недоступен':'Загружаем текст…';
      if(label.body.textContent!==text){label.body.textContent=text;label.dirty=true;}label.counts.textContent=this.connectionInfo(candidate.ordinal)??'';label.ordinal=candidate.ordinal;label.entry=entry;label.active=true;label.root.classList.toggle('focused',i===0);label.root.dataset.nodeId=entry?.id??'';label.read.hidden=!entry;
    }
    this.position();
  }
  refreshMetrics():void{for(const label of this.labels){if(label.active){label.counts.textContent=this.connectionInfo(label.ordinal)??'';label.dirty=true;}}}
  position():void{
    if(this.suppressed){this.hide();return;}this.dockRect=null;const width=window.innerWidth,height=window.innerHeight;const top=width<700?202:115;
    for(const label of this.labels){if(!label.active)continue;const point=this.project(label.ordinal);if(!point.visible){label.root.hidden=true;label.anchor.hidden=true;label.leader.hidden=true;continue;}
      label.root.hidden=false;label.root.style.maxHeight=width<700?'30dvh':'min(480px,60dvh)';label.root.style.width=width<700?`${Math.max(1,width-28)}px`:'340px';if(label.dirty){label.width=label.root.offsetWidth||340;label.height=label.root.offsetHeight||200;label.dirty=false;}
      const maxHeight=width<700?height*.30:Math.min(480,height*.60);const h=Math.min(label.root.offsetHeight||label.height,maxHeight);const w=Math.min(label.root.offsetWidth||label.width,width-28);const x=width<700?14:width-w-20;const y=width<700?Math.max(top,height-h-120):Math.max(top,Math.min(height-h-100,135));const rect:Rect=[x,y,x+w,y+h];
      if((this.frame?.blocked??[]).some(other=>rect[0]<other[2]+2&&rect[2]>other[0]-2&&rect[1]<other[3]+2&&rect[3]>other[1]-2)){label.root.hidden=true;label.anchor.hidden=true;label.leader.hidden=true;continue;}
      this.dockRect=rect;label.root.style.transform=`translate(${x}px,${y}px)`;label.anchor.hidden=false;label.anchor.style.transform=`translate(${point.x}px,${point.y}px) translate(-50%,-50%)`;
      const endX=Math.max(rect[0],Math.min(rect[2],point.x));const endY=Math.max(rect[1],Math.min(rect[3],point.y));const distance=Math.hypot(endX-point.x,endY-point.y);label.leader.hidden=distance<2;label.leader.style.width=`${distance}px`;label.leader.style.transform=`translate(${point.x}px,${point.y}px) rotate(${Math.atan2(endY-point.y,endX-point.x)}rad)`;
      if(!this.visibleBefore){this.visibleBefore=1;lifecycle('preview-show');}
    }
  }
  diagnostics():Record<string,unknown>{return{visible:this.labels.filter(label=>!label.root.hidden).length,activeFetches:this.active.size,cacheEntries:this.cache.size,cacheBytes:this.cache.bytes,observedResponseBytes:this.transferred,focusOrdinal:this.focus};}
  dispose():void{this.disposed=true;this.abort();this.cache.clear();this.failed.clear();this.context=null;this.frame=null;for(const label of this.labels){label.root.remove();label.anchor.remove();label.leader.remove();}this.labels=[];}
}
