import { lifecycle } from './lifecycle.ts';
import type { ScreenConnectivityResult } from './viewport-connections.ts';
import type { ViewBinding } from './focus.ts';
import type { GraphData, GraphPatch } from './binary.ts';
import type { NearRequest, NearResult } from './focus.ts';
import type { EnvelopeBounds, EnvelopeRequest, EnvelopeResult } from './envelopes.ts';
import type { Topics } from './contracts.ts';
import {restoreLodReuse} from './lod.ts';
import type { LodRequest, LodResult, LodReuse, LodCandidate, CellMembersRequest, CellMembersResult } from './lod.ts';
interface Reply { id:number;preview?:{base_token:number;lod:LodResult};cell_members?:CellMembersResult;candidate?:{token:number;base_token:number|null};lod_reuse?:LodReuse;accepted_token?:number;revision?:string;generation?:number;discarded_token?:number;graph?:GraphData;indices?:Uint32Array;lod?:LodResult;rebuilds?:number;error?:string;screen?:ScreenConnectivityResult&{view_projection:Float32Array;viewport:readonly[number,number]};envelopes?:EnvelopeBounds;near?:NearResult;envelope_view?:EnvelopeResult&{label_indices:Uint32Array} }
export class GraphWorker {
  private bounds:EnvelopeBounds|null=null;private accepted:{token:number;lod:LodResult}|null=null;private staged:LodCandidate|null=null;private disposed=false;private unsynchronized=false;
  get envelopes():EnvelopeBounds|null{return this.bounds;}
  private worker=new Worker(new URL('./graph.worker.ts',import.meta.url),{type:'module'});
  private serial=0;
  private pending=new Map<number,{kind:string;resolve:(value:Reply)=>void;reject:(reason:Error)=>void}>();
  constructor(){ lifecycle('worker-create');this.worker.addEventListener('message',(event:MessageEvent<Reply>)=>{const task=this.pending.get(event.data.id);if(!task)return;this.pending.delete(event.data.id);if(event.data.error){lifecycle('worker-query-rejected',{kind:task.kind,reason:['Expired connectivity query','Expired near query','Expired envelope query','Expired LOD revision'].includes(event.data.error)?event.data.error:'invalid-data'});task.reject(new Error('Не удалось проверить данные вселенной.'));}else task.resolve(event.data);}); }
  private request(body:Record<string,unknown>,transfer:Transferable[]=[],freshGraph=false):Promise<Reply>{
    if(this.disposed)throw new Error('Worker disposed');if(this.unsynchronized&&!freshGraph)throw new Error('Worker unsynchronized; fresh graph bind required');const id=++this.serial;let resolve!:(value:Reply)=>void,reject!:(reason:Error)=>void;const result=new Promise<Reply>((yes,no)=>{resolve=yes;reject=no;});this.pending.set(id,{kind:typeof body.kind==='string'?body.kind:'unknown',resolve,reject});
    try{this.worker.postMessage({...body,id},transfer);}catch(error){this.pending.delete(id);throw error;}return result;
  }
  async load(nodes:ArrayBuffer,edges:ArrayBuffer,nodeCount:number,edgeCount:number,history:boolean,topics:Topics,revision:string):Promise<GraphData>{this.unsynchronized=true;this.accepted=null;this.staged=null;const reply=await this.request({kind:'load',nodes,edges,nodeCount,edgeCount,history,topics,revision},[nodes,edges],true);if(!reply.graph)throw new Error('Вселенная не загрузилась');this.bounds=reply.envelopes??null;this.unsynchronized=false;return reply.graph;}
  async loadPatchedGraph(graph:GraphData,patch:GraphPatch,history:boolean,topics:Topics,revision:string):Promise<GraphData>{this.unsynchronized=true;this.accepted=null;this.staged=null;const reply=await this.request({kind:'load-patch',graph,patch,history,topics,revision},[],true);if(!reply.graph)throw new Error('Обновление не загрузилось');this.bounds=reply.envelopes??null;this.unsynchronized=false;return reply.graph;}
  async patch(patch:GraphPatch,history:boolean,revision:string):Promise<GraphData>{this.accepted=null;this.staged=null;const reply=await this.request({kind:'patch',patch,history,revision});if(!reply.graph)throw new Error('Обновление не загрузилось');this.bounds=reply.envelopes??null;return reply.graph;}
  async lod(request:LodRequest):Promise<LodCandidate>{
    const reply=await this.request({kind:'lod',request});if(!Number.isSafeInteger(reply.candidate?.token)||reply.candidate?.token!==reply.id||reply.candidate.base_token!==(this.accepted?.token??null))throw new Error('LOD candidate token or base mismatch');
    const lod=reply.lod_reuse?restoreLodReuse(this.accepted,reply.lod_reuse):reply.lod;if(!lod)throw new Error('Проекция вселенной недоступна');
    const value={lod,rebuilds:reply.rebuilds??0,token:reply.candidate!.token};this.staged=value;return value;
  }
  /** Visual-only exact next cut. Never replaces or accepts a normal candidate. */
  async previewLod(request:LodRequest):Promise<LodResult|null>{
    const base=this.accepted;if(!base||this.disposed||this.unsynchronized)return null;
    const reply=await this.request({kind:'preview-lod',base_token:base.token,request});
    if(this.accepted!==base||this.disposed||this.unsynchronized)return null;
    if(!reply.preview||reply.preview.base_token!==base.token||reply.preview.lod.revision!==request.revision)throw new Error('LOD preview binding mismatch');
    return reply.preview.lod;
  }
  // Dispatch and the Scene commit occur in one main-thread turn. Queries posted
  // by that commit follow accept-lod on the same ordered Worker message port.
  private failSynchronization(error:unknown):void{
    this.unsynchronized=true;this.accepted=null;this.staged=null;const failure=error instanceof Error?error:new Error('Worker acceptance failed');for(const task of this.pending.values())task.reject(failure);this.pending.clear();
  }
  acceptLod(value:LodCandidate):Promise<void>{
    try{
      if(this.staged!==value)throw new Error('LOD candidate is not retained');const ack=this.request({kind:'accept-lod',token:value.token});this.accepted={token:value.token,lod:value.lod};this.staged=null;
      return ack.then(reply=>{if(reply.accepted_token!==value.token||reply.revision!==value.lod.revision||reply.generation!==value.lod.generation)throw new Error('LOD acceptance acknowledgement mismatch');}).catch(error=>{this.failSynchronization(error);throw error;});
    }catch(error){this.failSynchronization(error);throw error;}
  }
  async discardLod(token:number):Promise<void>{if(this.staged?.token===token)this.staged=null;if(!this.disposed)await this.request({kind:'discard-lod',token});}
  async screen(request:ViewBinding&{view_projection:Float32Array;viewport:readonly[number,number]}):Promise<ScreenConnectivityResult&{view_projection:Float32Array;viewport:readonly[number,number]}>{const reply=await this.request({kind:'screen-connectivity',request});if(!reply.screen)throw new Error('Связи в кадре пока недоступны');return reply.screen;}
  async near(request:NearRequest):Promise<NearResult>{const reply=await this.request({kind:'near',request});if(!reply.near)throw new Error('Текст рядом пока недоступен');return reply.near;}
  async envelopeView(request:EnvelopeRequest&{label_topic_indices:Uint32Array;selected_ordinals:Uint32Array}):Promise<EnvelopeResult&{label_indices:Uint32Array}>{const reply=await this.request({kind:'envelopes',request});if(!reply.envelope_view)throw new Error('Оболочки тем пока недоступны');return reply.envelope_view;}
  async cellMembers(request:CellMembersRequest):Promise<CellMembersResult>{const reply=await this.request({kind:'cell-members',request});if(!reply.cell_members)throw new Error('Участок карты пока недоступен');return reply.cell_members;}
  async incident(ordinal:number):Promise<Uint32Array>{const reply=await this.request({kind:'incident',ordinal});return reply.indices??new Uint32Array();}
  dispose():void {this.disposed=true;this.accepted=null;this.staged=null;lifecycle('worker-dispose');this.worker.terminate();for(const task of this.pending.values())task.reject(new Error('Загрузка отменена'));this.pending.clear();}
}
