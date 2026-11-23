import {acceptsLodView} from './lod.ts';
import {cellRoute} from './stable-navigation.ts';
import {sourceHierarchy} from './logic.ts';
import type {LodRequest,LodResult,LodView,LodNavigation,LodSpatial,LodMap} from './lod.ts';
import type {Manifest,Topic,Topics} from './contracts.ts';
import type {GraphWorker} from './worker-client.ts';
const SMALL_GRAPH_CANONICAL_BUDGET=200;
interface Binding {manifest:Manifest;topics:Topics;worker:GraphWorker}
interface Waiter {resolve:(value:LodResult)=>void;reject:(reason:unknown)=>void}
export class LodController {
  private binding:Binding|null=null;private serial=0;private previewSerial=0;private unwrapRootEpoch:number|null=null;private path:string[]=[];private cells:string[]=[];private epoch=0;
  private children=new Map<string,string[]>();private roots:string[]=[];private byId=new Map<string,Topic>();
  private selection=new Set<number>();private search=new Set<number>();private cut:number[]=[];private view:LodView|undefined;private spatial:LodSpatial|null=null;private maps:LodMap|null=null;
  private accepted:LodResult|null=null;private acceptedKey='';private desired:{request:LodRequest;key:string;binding:Binding;unwrapRoot:boolean}|null=null;
  private initialized=false;private running=false;private waiters:Waiter[]=[];private allExpanded=false;private active=false;private unsynchronized=false;private rebuilds=0;
  private onAccepted:(result:LodResult,rebuilds:number)=>void;private onError:(reason:unknown)=>void;private viewCurrent:(view:LodView)=>boolean;
  constructor(onAccepted:(result:LodResult,rebuilds:number)=>void,onError:(reason:unknown)=>void,viewCurrent:(view:LodView)=>boolean=()=>true){this.onAccepted=onAccepted;this.onError=onError;this.viewCurrent=viewCurrent;}
  fork():LodController{const next=new LodController(this.onAccepted,this.onError,this.viewCurrent);next.path=[...this.path];next.cells=[...this.cells];next.epoch=this.epoch;next.allExpanded=this.allExpanded;next.initialized=this.initialized;next.serial=this.serial;return next;}
  private get fullCanonical():boolean{return this.allExpanded;}
  setForces(selection:readonly number[],search:readonly number[]):void{this.selection=new Set(selection);this.search=new Set(search);}
  get result():LodResult|null{return this.accepted;}
  get canBack():boolean{return this.allExpanded||this.path.length>0||this.cells.length>0;}
  get busy():boolean{return this.running;}
  get navigation():LodNavigation{return{epoch:this.epoch,topic_path:this.path.flatMap(id=>{const topic=this.byId.get(id);return topic?[topic.index]:[];}),expanded_cell_ids:[...this.cells]};}
  get expandedTopicIndices():Uint32Array{return new Uint32Array(this.navigation.topic_path);}
  get forcedOrdinals():Uint32Array{return new Uint32Array(new Set([...this.selection,...this.search]));}
  get rebuildCount():number{return this.rebuilds;}
  async bind(manifest:Manifest,topics:Topics,worker:GraphWorker,visible:ArrayLike<number>,preserveForces=false,mapOptions?:LodMap):Promise<LodResult>{
    if(visible.length!==manifest.counts.nodes)throw new Error('Source visibility revision mismatch');
    // New Source generation starts with a complete view-independent partition.
    // Automatic detail is requested only after this graph is atomically accepted.
    this.unsynchronized=false;this.view=undefined;this.spatial=null;this.maps=mapOptions?{...mapOptions}:null;this.active=false;this.binding={manifest,topics,worker};this.byId=new Map(topics.topics.map(topic=>[topic.id,topic]));this.children.clear();
    const parented=new Set<string>();for(const link of sourceHierarchy(topics.topics,topics.links)){this.children.set(link.parent,[...(this.children.get(link.parent)??[]),link.child]);parented.add(link.child);}
    this.roots=topics.topics.filter(topic=>!parented.has(topic.id)).map(topic=>topic.id);
    if(!this.roots.length&&topics.topics.length){this.roots=topics.topics.map(topic=>topic.id);this.children.clear();}
    this.path=this.validatePath(this.path);this.cells=[];this.epoch++;
    if(!preserveForces){this.selection.clear();this.search.clear();this.path=[];this.allExpanded=false;}
    if(!this.initialized||!preserveForces){this.allExpanded=this.defaultFullCanonical();this.initialized=true;}
    this.acceptedKey='';this.cut=this.collectCut();return this.request();
  }
  private validatePath(path:readonly string[]):string[]{const valid:string[]=[];for(const id of path){const parent=valid.at(-1);if(!this.byId.has(id)||parent&&!this.children.get(parent)?.includes(id))break;valid.push(id);}return valid;}
  activate():void{this.active=true;}
  private remember():void{this.epoch++;if(!this.spatial&&!this.maps)this.view=undefined;}
  async expandAll():Promise<LodResult>{this.remember();this.allExpanded=true;this.cut=[];return this.request();}
  async restoreMap():Promise<LodResult>{if(this.allExpanded){this.remember();this.allExpanded=false;this.cut=this.collectCut();}return this.request();}
  private defaultFullCanonical():boolean{return !this.maps&&(this.binding?.manifest.counts.visible_nodes??Infinity)<=SMALL_GRAPH_CANONICAL_BUDGET;}
  reset():void{this.epoch++;this.view=undefined;this.allExpanded=this.defaultFullCanonical();this.path=[];this.cells=[];this.cut=this.collectCut();this.schedule();}
  private topicPath(topicId:string):string[]{
    const existing=this.path.indexOf(topicId);if(existing>=0)return this.path.slice(0,existing+1);
    const parent=this.path.findLastIndex(id=>this.children.get(id)?.includes(topicId));if(parent>=0)return[...this.path.slice(0,parent+1),topicId];
    const parents=new Map<string,string>();for(const [parent,children]of [...this.children].toSorted(([a],[b])=>a.localeCompare(b)))for(const child of children)if(!parents.has(child))parents.set(child,parent);
    const chain:string[]=[];let cursor:string|undefined=topicId;while(cursor&&!chain.includes(cursor)){chain.unshift(cursor);cursor=parents.get(cursor);}return chain;
  }
  enterTopic(topicId:string,unwrapRoot=false):void{
    if(!this.byId.has(topicId)||this.path.at(-1)===topicId&&!this.allExpanded)return;
    const chain=this.topicPath(topicId);this.remember();this.path=chain;this.cells=[];this.allExpanded=false;this.unwrapRootEpoch=unwrapRoot?this.epoch:null;this.cut=this.collectCut();this.schedule();
  }
  /** Only a technical residual root is skipped; never another Source level. */
  private residualRoot(lod:LodResult):string|null{
    if(lod.frontier_topic_indices?.length)return null;const scope=lod.navigation?.topic_path.at(-1);if(scope===undefined||lod.navigation?.expanded_cell_ids.length)return null;
    const roots=lod.proxies.filter(p=>p.kind==='cell'&&p.scope_topic_index===scope&&p.parent_cell_id===null&&!p.coincident);
    const root=roots.length===1?roots[0]:undefined;return root?.kind==='cell'?root.cell_id:null;
  }
  /** Restore an already previewed ordinary cut, including a lateral Source route. */
  restorePreviewNavigation(navigation:LodNavigation,revision:string):boolean{
    if(!this.binding||this.binding.manifest.revision!==revision||this.unsynchronized)return false;
    const byIndex=new Map([...this.byId.values()].map(topic=>[topic.index,topic.id]));
    const path=navigation.topic_path.map(index=>byIndex.get(index));if(path.some(id=>id===undefined)||new Set(path).size!==path.length)return false;
    const ids=path.filter((id):id is string=>id!==undefined);if(this.validatePath(ids).length!==ids.length||navigation.expanded_cell_ids.some(id=>typeof id!=='string'||!id)||new Set(navigation.expanded_cell_ids).size!==navigation.expanded_cell_ids.length)return false;
    this.remember();this.path=[...ids];this.cells=[...navigation.expanded_cell_ids];this.allExpanded=false;this.unwrapRootEpoch=null;this.cut=this.collectCut();this.schedule();return true;
  }
  /** Rebuild a completed scale boundary using its ordinary route only. */
  previewNavigation(navigation:LodNavigation,revision:string):Promise<LodResult|null>{
    if(!this.binding||this.binding.manifest.revision!==revision||this.unsynchronized)return Promise.resolve(null);
    const byIndex=new Map([...this.byId.values()].map(topic=>[topic.index,topic.id]));
    const path=navigation.topic_path.map(index=>byIndex.get(index));if(path.some(id=>id===undefined)||new Set(path).size!==path.length)return Promise.resolve(null);
    const ids=path.filter((id):id is string=>id!==undefined);if(this.validatePath(ids).length!==ids.length||navigation.expanded_cell_ids.some(id=>typeof id!=='string'||!id)||new Set(navigation.expanded_cell_ids).size!==navigation.expanded_cell_ids.length)return Promise.resolve(null);
    return this.preview(ids,[...navigation.expanded_cell_ids],false);
  }
  previewTopic(topicId:string,unwrapRoot=false):Promise<LodResult|null>{return this.byId.has(topicId)?this.preview(this.topicPath(topicId),[],unwrapRoot):Promise.resolve(null);}
  previewCell(cellId:string):Promise<LodResult|null>{
    const cell=this.accepted?.proxies.find(p=>p.kind==='cell'&&p.cell_id===cellId);if(!cell||cell.kind!=='cell'||this.cells.includes(cellId))return Promise.resolve(null);
    const chain=cellRoute(this.cells,cellId,cell.parent_cell_id);return chain.length?this.preview(this.path,chain,false):Promise.resolve(null);
  }
  previewParent():Promise<LodResult|null>{return !this.canBack||this.allExpanded?Promise.resolve(null):this.cells.length?this.preview(this.path,this.cells.slice(0,-1),false):this.preview(this.path.slice(0,-1),[],false);}
  private async preview(path:readonly string[],cells:readonly string[],unwrapRoot:boolean):Promise<LodResult|null>{
    const binding=this.binding,base=this.accepted,epoch=this.epoch,serial=++this.previewSerial,forces=[...this.forcedOrdinals].toSorted((a,b)=>a-b).join(',');
    if(!binding||!base||this.unsynchronized||this.allExpanded)return null;
    const current=()=>this.binding===binding&&this.accepted===base&&this.epoch===epoch&&this.previewSerial===serial&&[...this.forcedOrdinals].toSorted((a,b)=>a-b).join(',')===forces;
    const topic_path=path.flatMap(id=>{const topic=this.byId.get(id);return topic?[topic.index]:[];}),active=path.at(-1),cut=(active?this.children.get(active)??[]:this.roots).flatMap(id=>{const topic=this.byId.get(id);return topic?[topic.index]:[];});
    const request:LodRequest={revision:binding.manifest.revision,generation:this.serial+1,navigation:{epoch:epoch+1,topic_path,expanded_cell_ids:[...cells]},collapsed_topic_indices:new Uint32Array(cut),expanded_topic_indices:new Uint32Array(topic_path),partial_topic_indices:new Uint32Array(topic_path),force_reveal_ordinals:this.forcedOrdinals};
    let lod=await binding.worker.previewLod(request);if(!current()||!lod)return null;
    if(!acceptsLodView(request,lod))throw new Error('Invalid preview LOD binding');
    const root=unwrapRoot?this.residualRoot(lod):null;
    if(root){const next={...request,navigation:{...request.navigation!,expanded_cell_ids:[root]}};lod=await binding.worker.previewLod(next);if(!current()||!lod)return null;if(!acceptsLodView(next,lod))throw new Error('Invalid preview LOD binding');}
    return lod;
  }
  expand(topicId:string):void{this.enterTopic(topicId);}
  enterCell(cellId:string):void{const cell=this.accepted?.proxies.find(proxy=>proxy.kind==='cell'&&proxy.cell_id===cellId);if(!cell||cell.kind!=='cell'||this.cells.includes(cellId))return;const chain=cellRoute(this.cells,cellId,cell.parent_cell_id);if(!chain.length)return;this.remember();this.cells=chain;this.allExpanded=false;this.schedule();}
  ancestor(topicDepth:number,cellDepth=0):boolean{if(topicDepth<0||topicDepth>this.path.length||cellDepth<0||cellDepth>this.cells.length)return false;const sameTopic=topicDepth===this.path.length;this.remember();this.path=this.path.slice(0,topicDepth);this.cells=sameTopic?this.cells.slice(0,cellDepth):[];this.allExpanded=false;this.cut=this.collectCut();this.schedule();return true;}
  back():boolean{if(!this.canBack)return false;if(this.allExpanded){this.allExpanded=false;this.path=[];this.cells=[];}else if(this.cells.length)this.cells=this.cells.slice(0,-1);else this.path=this.path.slice(0,-1);this.remember();this.cut=this.collectCut();this.schedule();return true;}
  async reveal(ordinals:readonly number[]):Promise<LodResult>{this.selection=new Set(ordinals);return this.request();}
  clearSelection():void{if(!this.selection.size)return;this.selection.clear();this.schedule();}
  setSearch(ordinals:readonly number[]):void{this.search=new Set(ordinals);this.schedule();}
  /** Opt-in physical frontier; selection/search remain highlight inputs only. */
  updateSpatial(view:LodView,options:LodSpatial={split_px:96,merge_px:72}):void{
    this.maps=null;this.spatial={...options};this.view={...view,view_projection:view.view_projection.slice(),viewport:[...view.viewport],blocked_rects:view.blocked_rects?.map(rect=>[...rect])};
    if(!this.fullCanonical)this.schedule();
  }
  /** Current Source maps remain the only public aggregate identities. */
  updateMaps(view:LodView,options:LodMap):void{
    this.spatial=null;this.maps={...options};this.view={...view,view_projection:view.view_projection.slice(),viewport:[...view.viewport],blocked_rects:view.blocked_rects?.map(rect=>[...rect])};
    if(!this.fullCanonical)this.schedule();
  }
  // Camera affects labels and screen metrics only. A route cut has no view dependency.
  update(_projectedDiameter:(topic:Topic)=>number,_overview:boolean,_threshold:number,_view?:LodView):void{}
  private collectCut():number[]{
    if(this.fullCanonical)return[];
    const expanded=new Set(this.path),pending=[...this.roots],seen=new Set<string>(),cut:number[]=[];
    for(let i=0;i<pending.length;i++){const id=pending[i]!;if(seen.has(id))continue;seen.add(id);if(expanded.has(id)){for(const child of this.children.get(id)??[])pending.push(child);}else{const topic=this.byId.get(id);if(topic)cut.push(topic.index);}}
    return cut.toSorted((a,b)=>a-b);
  }
  private schedule():void{if(!this.binding)return;this.request().catch(error=>{if(!(error instanceof DOMException&&error.name==='AbortError'))this.onError(error);});}
  private request():Promise<LodResult>{
    if(this.unsynchronized)return Promise.reject(new Error('Worker unsynchronized; fresh graph bind required'));
    const binding=this.binding;if(!binding)return Promise.reject(new Error('Вселенная ещё не загружена'));
    const maps=!this.fullCanonical?this.maps:undefined,spatial=!maps&&!this.fullCanonical&&this.view?this.spatial:undefined,cameraMode=!!(maps||spatial);
    const forced=cameraMode?[]:Array.from(new Set([...this.selection,...this.search])).toSorted((a,b)=>a-b),partial=this.fullCanonical||cameraMode?new Uint32Array():this.expandedTopicIndices,view=cameraMode?this.view:undefined,navigation=this.fullCanonical||cameraMode?undefined:this.navigation,cut=cameraMode?[]:this.cut;
    const key=`${binding.manifest.revision}|${JSON.stringify(navigation)}|${cut.join(',')}|${partial.join(',')}|${forced.join(',')}|${JSON.stringify(maps??null)}|${JSON.stringify(spatial??null)}|${view?JSON.stringify([view.camera_generation,Array.from(view.view_projection),view.viewport,view.glyph_diameter,view.picking_diameter,view.blocked_rects]):''}`;
    if(this.accepted&&this.acceptedKey===key&&(!this.accepted.view||this.accepted.map_lod||this.accepted.spatial_lod||this.viewCurrent(this.accepted.view)))return Promise.resolve(this.accepted);
    if(this.desired?.key!==key||this.desired.binding!==binding)this.desired={key,binding,unwrapRoot:this.unwrapRootEpoch===this.epoch,request:{...(maps?{map_lod:{...maps}}:{}),...(spatial?{spatial_lod:{...spatial}}:{}),revision:binding.manifest.revision,generation:++this.serial,navigation,collapsed_topic_indices:new Uint32Array(cut),expanded_topic_indices:partial,partial_topic_indices:partial,force_reveal_ordinals:new Uint32Array(forced),previous_reveal_ordinals:this.accepted?.revision===binding.manifest.revision?this.accepted.automatic_reveal_ordinals?.slice():undefined,view}};
    const result=new Promise<LodResult>((resolve,reject)=>this.waiters.push({resolve,reject}));this.pump();return result;
  }
  private failSynchronization(binding:Binding,error:unknown):void{
    if(this.binding?.worker!==binding.worker)return;this.unsynchronized=true;this.accepted=null;this.acceptedKey='';this.desired=null;const waiters=this.waiters;this.waiters=[];for(const waiter of waiters)waiter.reject(error);
  }
  private pump():void{
    if(this.running||!this.desired)return;this.running=true;const desired=this.desired;
    desired.binding.worker.lod(desired.request).then(async candidate=>{
      const {lod,rebuilds,token}=candidate;
      if(this.desired!==desired||this.binding!==desired.binding){await desired.binding.worker.discardLod(token);return;}
      if(JSON.stringify(desired.request.navigation)!==JSON.stringify(lod.navigation)||!acceptsLodView(desired.request,lod)){await desired.binding.worker.discardLod(token);throw new Error('Invalid current LOD binding');}
      if(desired.unwrapRoot){
        this.unwrapRootEpoch=null;const root=this.residualRoot(lod);
        if(root){await desired.binding.worker.discardLod(token);if(this.desired!==desired||this.binding!==desired.binding)return;this.cells=[root];this.desired=null;this.schedule();return;}
      }
      // Spatial cuts retain every visible Source node/edge. The latest requested
      // complete cut can commit during motion; instantaneous matrices remain
      // mandatory for legacy partial views, whose coverage depends on the view.
      if(this.active&&lod.view&&!desired.request.map_lod&&!desired.request.spatial_lod&&!this.viewCurrent(lod.view)){await desired.binding.worker.discardLod(token);throw new DOMException('Camera changed before LOD acceptance','AbortError');}
      let acknowledgement:Promise<void>;try{acknowledgement=desired.binding.worker.acceptLod(candidate);}catch(error){this.failSynchronization(desired.binding,error);throw error;}
      this.accepted=lod;this.rebuilds=rebuilds;this.acceptedKey=desired.key;if(this.active)this.onAccepted(lod,rebuilds);
      try{await acknowledgement;}catch(error){this.failSynchronization(desired.binding,error);throw error;}
      if(this.desired!==desired||this.binding!==desired.binding)return;
      const waiters=this.waiters;this.waiters=[];for(const waiter of waiters)waiter.resolve(lod);
    }).catch(error=>{if(this.desired!==desired)return;this.desired=null;const waiters=this.waiters;this.waiters=[];for(const waiter of waiters)waiter.reject(error);}).finally(()=>{this.running=false;if(this.desired&&this.desired!==desired)this.pump();});
  }

  dispose():void{this.binding=null;this.desired=null;this.serial++;const waiters=this.waiters;this.waiters=[];for(const waiter of waiters)waiter.reject(new DOMException('Aborted','AbortError'));}
}
