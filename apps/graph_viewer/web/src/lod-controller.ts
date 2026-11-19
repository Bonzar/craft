import { nextCollapsedTopics } from './lod.ts';
import { sourceHierarchy } from './logic.ts';
import type { LodRequest, LodResult } from './lod.ts';
import type { Manifest, Topic, Topics } from './contracts.ts';
import type { GraphWorker } from './worker-client.ts';
// A presentation budget only: source topics and memberships remain unchanged.
const SMALL_GRAPH_CANONICAL_BUDGET=200;
interface Binding {manifest:Manifest;topics:Topics;worker:GraphWorker}
interface Waiter {resolve:(value:LodResult)=>void;reject:(reason:unknown)=>void}
export class LodController {
  private binding:Binding|null=null;private serial=0;private states=new Map<string,boolean>();private manual=new Set<string>();
  private children=new Map<string,string[]>();private roots:string[]=[];private byId=new Map<string,Topic>();
  private selection=new Set<number>();private search=new Set<number>();private cut:number[]=[];
  private accepted:LodResult|null=null;private acceptedKey='';private desired:{request:LodRequest;key:string;binding:Binding}|null=null;
  private running=false;private waiters:Waiter[]=[];private allExpanded=false;private initialized=false;private active=false;private rebuilds=0;
  private onAccepted:(result:LodResult,rebuilds:number)=>void;private onError:(reason:unknown)=>void;
  constructor(onAccepted:(result:LodResult,rebuilds:number)=>void,onError:(reason:unknown)=>void){this.onAccepted=onAccepted;this.onError=onError;}
  fork():LodController{const next=new LodController(this.onAccepted,this.onError);next.states=new Map(this.states);next.manual=new Set(this.manual);next.allExpanded=this.allExpanded;next.initialized=this.initialized;next.serial=this.serial;return next;}
  setForces(selection:readonly number[],search:readonly number[]):void{this.selection=new Set(selection);this.search=new Set(search);}
  get result():LodResult|null{return this.accepted;}
  get expandedTopicIndices():Uint32Array{if(this.allExpanded)return new Uint32Array(Array.from(this.byId.values(),topic=>topic.index));const result:number[]=[];const queue=[...this.roots];const visited=new Set<string>();for(let cursor=0;cursor<queue.length;cursor++){const id=queue[cursor];if(!id||visited.has(id))continue;visited.add(id);if(this.states.get(id)??true)continue;const topic=this.byId.get(id);if(topic)result.push(topic.index);queue.push(...(this.children.get(id)??[]));}return new Uint32Array(result);}
  get forcedOrdinals():Uint32Array{return new Uint32Array(new Set([...this.selection,...this.search]));}
  get rebuildCount():number{return this.rebuilds;}
  async bind(manifest:Manifest,topics:Topics,worker:GraphWorker,preserveForces=false):Promise<LodResult>{
    if(!this.initialized){this.allExpanded=manifest.counts.visible_nodes<=SMALL_GRAPH_CANONICAL_BUDGET;this.initialized=true;}
    this.active=false;this.binding={manifest,topics,worker};this.byId=new Map(topics.topics.map(topic=>[topic.id,topic]));this.children.clear();
    const parented=new Set<string>();for(const link of sourceHierarchy(topics.topics,topics.links)){this.children.set(link.parent,[...(this.children.get(link.parent)??[]),link.child]);parented.add(link.child);}
    this.roots=topics.topics.filter(topic=>!parented.has(topic.id)).map(topic=>topic.id);
    if(!this.roots.length&&topics.topics.length){this.roots=topics.topics.map(topic=>topic.id);this.children.clear();}
    for(const topic of topics.topics)if(!this.states.has(topic.id))this.states.set(topic.id,true);
    if(!preserveForces){this.selection.clear();this.search.clear();this.manual.clear();this.resetCut();}
    this.acceptedKey='';this.cut=this.collectCut();return this.request();
  }
  activate():void{this.active=true;}
  private resetCut():void{for(const topic of this.byId.values())this.states.set(topic.id,true);}
  async expandAll():Promise<LodResult>{this.allExpanded=true;this.manual.clear();for(const id of this.byId.keys())this.states.set(id,false);this.cut=[];return this.request();}
  reset():void{this.allExpanded=(this.binding?.manifest.counts.visible_nodes??Infinity)<=SMALL_GRAPH_CANONICAL_BUDGET;this.manual.clear();this.resetCut();this.cut=this.collectCut();this.schedule();}
  expand(topicId:string):void{if(!this.byId.has(topicId))return;this.states.set(topicId,false);this.manual.add(topicId);this.cut=this.collectCut();this.schedule();}
  async reveal(ordinals:readonly number[]):Promise<LodResult>{this.selection=new Set(ordinals);return this.request();}
  clearSelection():void{this.selection.clear();this.schedule();}
  setSearch(ordinals:readonly number[]):void{this.search=new Set(ordinals);this.schedule();}
  update(projectedDiameter:(topic:Topic)=>number,overview:boolean,threshold:number):void{
    if(!this.active||!this.binding||this.allExpanded)return;if(overview){this.cut=this.collectCut();return;}
    const queue=[...this.roots];const visited=new Set<string>();
    for(let cursor=0;cursor<queue.length;cursor++){const id=queue[cursor];if(!id||visited.has(id))continue;visited.add(id);const topic=this.byId.get(id);if(!topic)continue;const diameter=projectedDiameter(topic);let collapsed=nextCollapsedTopics(this.states.get(id)??true,diameter,threshold);
      if(this.manual.has(id)){collapsed=false;if(diameter>threshold*1.35)this.manual.delete(id);}
      this.states.set(id,collapsed);if(!collapsed)queue.push(...(this.children.get(id)??[]));
    }
    const next=this.collectCut();if(next.join(',')!==this.cut.join(',')){this.cut=next;this.schedule();}
  }
  private collectCut():number[]{if(this.allExpanded)return[];const result:number[]=[];const queue=[...this.roots];const visited=new Set<string>();for(let cursor=0;cursor<queue.length;cursor++){const id=queue[cursor];if(!id||visited.has(id))continue;visited.add(id);const topic=this.byId.get(id);if(!topic)continue;if(this.states.get(id)??true)result.push(topic.index);else queue.push(...(this.children.get(id)??[]));}return result.toSorted((a,b)=>a-b);}
  private schedule():void{if(!this.binding)return;this.request().catch(error=>{if(!(error instanceof DOMException&&error.name==='AbortError'))this.onError(error);});}
  private request():Promise<LodResult>{
    const binding=this.binding;if(!binding)return Promise.reject(new Error('Вселенная ещё не загружена'));
    const forced=Array.from(new Set([...this.selection,...this.search])).toSorted((a,b)=>a-b);
    const key=`${binding.manifest.revision}|${this.cut.join(',')}|${forced.join(',')}`;
    if(this.accepted&&this.acceptedKey===key)return Promise.resolve(this.accepted);
    if(this.desired?.key!==key||this.desired.binding!==binding){this.desired={key,binding,request:{revision:binding.manifest.revision,generation:++this.serial,collapsed_topic_indices:new Uint32Array(this.cut),force_reveal_ordinals:new Uint32Array(forced)}};}
    const result=new Promise<LodResult>((resolve,reject)=>this.waiters.push({resolve,reject}));this.pump();return result;
  }
  private pump():void{
    if(this.running||!this.desired)return;this.running=true;const desired=this.desired;
    desired.binding.worker.lod(desired.request).then(({lod,rebuilds})=>{
      if(this.desired!==desired||this.binding!==desired.binding)return;
      this.accepted=lod;this.rebuilds=rebuilds;this.acceptedKey=desired.key;if(this.active)this.onAccepted(lod,rebuilds);
      const waiters=this.waiters;this.waiters=[];for(const waiter of waiters)waiter.resolve(lod);
    }).catch(error=>{if(this.desired!==desired)return;const waiters=this.waiters;this.waiters=[];for(const waiter of waiters)waiter.reject(error);}).finally(()=>{this.running=false;if(this.desired!==desired)this.pump();});
  }
  dispose():void{this.binding=null;this.desired=null;this.serial++;const waiters=this.waiters;this.waiters=[];for(const waiter of waiters)waiter.reject(new DOMException('Aborted','AbortError'));}
}
