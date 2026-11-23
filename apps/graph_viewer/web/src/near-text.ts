import { apiUrl, get } from './api.ts';
import { nodeSchema } from './contracts.ts';
import type { Manifest } from './contracts.ts';
import { button, element } from './dom.ts';
import { viewBindingMatches,PREVIEW_FOCUS_RADIUS } from './focus.ts';
import type { NearCandidate, ViewBinding } from './focus.ts';
import { PreviewCache, previewFromCard } from './preview-cache.ts';
import type { PreviewEntry } from './preview-cache.ts';
import { lifecycle } from './lifecycle.ts';
import type { Projection } from './scene.ts';
type Rect=readonly[number,number,number,number];
interface Frame {binding:ViewBinding;candidate:NearCandidate;show:boolean;blocked:readonly Rect[]}
/** A preview needs one actual canonical glyph at focus, without an overlapping rival. */
export function uniquePreviewCandidate(candidates:readonly NearCandidate[],focus:readonly[number,number]):NearCandidate|null{const matches=candidates.filter(candidate=>Math.hypot(candidate.screen_x-focus[0],candidate.screen_y-focus[1])<=PREVIEW_FOCUS_RADIUS);return matches.length===1?matches[0]!:null;}
export class NearText {
  private fetchingPaused=false;private suppressed=false;private dockRect:Rect|null=null;private cache=new PreviewCache();private context:Manifest|null=null;private frame:Frame|null=null;private expected:ViewBinding|null=null;
  private root:HTMLElement;private body:HTMLElement;private read:HTMLButtonElement;private entry:PreviewEntry|null=null;private pressed:{entry:PreviewEntry;intent:number}|null=null;private active:{controller:AbortController;ordinal:number;intent:number}|null=null;private failed=new Map<string,number>();private disposed=false;private focus:number|null=null;private transferred=0;private intent=0;
  private motion='';private stableAt=0;private ready=false;
  private project:(ordinal:number)=>Projection;private onRead:(entry:PreviewEntry)=>void;private onError:(error:unknown)=>void;
  constructor(root:HTMLElement,project:(ordinal:number)=>Projection,onRead:(entry:PreviewEntry)=>void,onError:(error:unknown)=>void,_connectionInfo:(ordinal:number)=>string|null=()=>null){
    this.project=project;this.onRead=onRead;this.onError=onError;this.root=element('section','','near-preview');this.body=element('p');this.read=button('↗',()=>{const pressed=this.pressed;this.pressed=null;const entry=pressed?.entry??this.entry;if(pressed&&(pressed.intent!==this.intent||entry!==this.entry))return;if(this.eligible()&&entry&&entry.provider_id===this.context?.provider_id&&entry.revision===this.context.revision&&entry.ordinal===this.frame?.candidate.ordinal)this.onRead(entry);});this.read.addEventListener('pointerdown',()=>{this.pressed=this.entry?{entry:this.entry,intent:this.intent}:null;});this.read.addEventListener('pointercancel',()=>{this.pressed=null;});this.read.setAttribute('aria-label','Читать утверждение целиком');this.read.title='Читать целиком';this.root.setAttribute('aria-label','Первые две строки утверждения');this.root.append(this.body,this.read);this.root.hidden=true;root.append(this.root);
  }
  setContext(manifest:Manifest):void{if(this.context?.revision===manifest.revision&&this.context.provider_id===manifest.provider_id){this.context=manifest;return;}this.invalidate();this.cache.clear();this.failed.clear();this.context=manifest;this.ready=false;this.stableAt=performance.now();}
  pauseFetches():void{this.fetchingPaused=true;this.invalidate();}
  resumeFetches():void{this.fetchingPaused=false;this.pump();}
  get focusedEntry():PreviewEntry|null{return this.focus!==null&&this.context?this.cache.get(this.context.provider_id,this.context.revision,this.focus):null;}
  prime(entries:readonly PreviewEntry[]):void{for(const entry of entries)if(entry.provider_id===this.context?.provider_id&&entry.revision===this.context.revision)this.cache.put(entry);}
  setSuppressed(value:boolean):void{if(this.suppressed===value)return;this.suppressed=value;lifecycle('preview-owner',{reader:value});this.invalidate();this.ready=false;this.stableAt=performance.now();}
  /** Camera, genuine gestures, worker acceptance and crossfade must all be idle. */
  setMotion(signature:string,busy:boolean,now:number):boolean{if(signature!==this.motion||busy){if(this.frame||this.active||!this.root.hidden)this.invalidate();this.motion=signature;this.stableAt=now;this.ready=false;return true;}if(!this.ready&&now-this.stableAt>=650){this.ready=true;this.render();this.pump();}return false;}
  get occupiedRects():readonly Rect[]{return this.dockRect?[this.dockRect]:[];}
  expect(binding:ViewBinding):void{if(this.expected&&!viewBindingMatches(this.expected,binding)){this.abort();this.hide();}this.expected=binding;}
  invalidate():void{this.ready=false;this.stableAt=performance.now();this.intent++;this.abort();this.frame=null;this.expected=null;this.focus=null;this.hide();}
  setFrame(binding:ViewBinding,candidates:readonly NearCandidate[],show:boolean,blocked:readonly Rect[],focus:readonly[number,number]):void{
    if(binding.revision!==this.context?.revision||!this.expected||!viewBindingMatches(this.expected,binding))return;const candidate=uniquePreviewCandidate(candidates,focus);if(!show||!candidate){this.intent++;this.abort();this.frame=null;this.focus=null;this.hide();return;}if(candidate.ordinal!==this.focus){this.intent++;this.abort();this.hide();}this.focus=candidate.ordinal;this.frame={binding,candidate,show,blocked};this.render();this.pump();
  }
  private abort():void{this.active?.controller.abort();this.active=null;}
  private eligible():boolean{const frame=this.frame;return !this.disposed&&!this.fetchingPaused&&!this.suppressed&&this.ready&&Boolean(frame?.show&&this.context&&this.expected&&viewBindingMatches(frame.binding,this.expected)&&this.project(frame.candidate.ordinal).visible);}
  private pump():void{
    const frame=this.frame,context=this.context;if(!this.eligible()||!frame||!context)return;const ordinal=frame.candidate.ordinal,key=`${context.revision}:${ordinal}`;if(this.active||this.cache.get(context.provider_id,context.revision,ordinal)||Date.now()-(this.failed.get(key)??0)<5000)return;
    const job={controller:new AbortController(),ordinal,intent:this.intent};this.active=job;
    get(apiUrl(`/graph/${encodeURIComponent(context.revision)}/nodes/${ordinal}`,{offset:0,limit:480}),nodeSchema,job.controller.signal).then(card=>{
      if(job.controller.signal.aborted||this.intent!==job.intent||!this.eligible()||this.frame?.candidate.ordinal!==ordinal||this.context?.revision!==context.revision||this.context.provider_id!==context.provider_id)return;if(card.revision!==context.revision||card.ordinal!==ordinal)throw new Error('Источник вернул текст другой звезды');const preview=previewFromCard(card,context.provider_id,context.revision);this.transferred+=new TextEncoder().encode(JSON.stringify(card)).byteLength;this.cache.put(preview);this.render();
    }).catch(error=>{if(job.controller.signal.aborted||this.intent!==job.intent)return;this.failed.set(key,Date.now());this.onError(error);this.hide();}).finally(()=>{if(this.active===job)this.active=null;});
  }
  private hide():void{this.dockRect=null;this.entry=null;if(!this.root.hidden)lifecycle('preview-hide');this.root.hidden=true;}
  private render():void{const frame=this.frame,context=this.context;if(!this.eligible()||!frame||!context){this.hide();return;}const entry=this.cache.get(context.provider_id,context.revision,frame.candidate.ordinal);if(!entry){this.hide();return;}this.entry=entry;if(this.body.textContent!==entry.text)this.body.textContent=entry.text;this.root.dataset.nodeId=entry.id;this.root.hidden=false;this.position();}
  refreshMetrics():void{}
  position():void{if(!this.eligible()||!this.entry){this.hide();return;}const rect=this.root.getBoundingClientRect(),box:Rect=[rect.left,rect.top,rect.right,rect.bottom];if((this.frame?.blocked??[]).some(other=>box[0]<other[2]+2&&box[2]>other[0]-2&&box[1]<other[3]+2&&box[3]>other[1]-2)){this.hide();return;}this.dockRect=box;this.root.hidden=false;}
  diagnostics():Record<string,unknown>{return{visible:this.root.hidden?0:1,activeFetches:this.active?1:0,cacheEntries:this.cache.size,cacheBytes:this.cache.bytes,observedResponseBytes:this.transferred,focusOrdinal:this.focus,settled:this.ready};}
  dispose():void{this.disposed=true;this.invalidate();this.cache.clear();this.failed.clear();this.context=null;this.root.remove();}
}
