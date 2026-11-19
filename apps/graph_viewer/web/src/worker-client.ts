import { lifecycle } from './lifecycle.ts';
import type { ScreenConnectivityResult } from './viewport-connections.ts';
import type { ViewBinding } from './focus.ts';
import type { GraphData, GraphPatch } from './binary.ts';
import type { NearRequest, NearResult } from './focus.ts';
import type { EnvelopeBounds, EnvelopeRequest, EnvelopeResult } from './envelopes.ts';
import type { Topics } from './contracts.ts';
import type { LodRequest, LodResult } from './lod.ts';
interface Reply { id:number;graph?:GraphData;indices?:Uint32Array;lod?:LodResult;rebuilds?:number;error?:string;screen?:ScreenConnectivityResult&{view_projection:Float32Array;viewport:readonly[number,number]};envelopes?:EnvelopeBounds;near?:NearResult;envelope_view?:EnvelopeResult&{label_indices:Uint32Array} }
export class GraphWorker {
  private bounds:EnvelopeBounds|null=null;
  get envelopes():EnvelopeBounds|null{return this.bounds;}
  private worker=new Worker(new URL('./graph.worker.ts',import.meta.url),{type:'module'});
  private serial=0;
  private pending=new Map<number,{kind:string;resolve:(value:Reply)=>void;reject:(reason:Error)=>void}>();
  constructor(){ lifecycle('worker-create');this.worker.addEventListener('message',(event:MessageEvent<Reply>)=>{const task=this.pending.get(event.data.id);if(!task)return;this.pending.delete(event.data.id);if(event.data.error){lifecycle('worker-query-rejected',{kind:task.kind,reason:['Expired connectivity query','Expired near query','Expired envelope query','Expired LOD revision'].includes(event.data.error)?event.data.error:'invalid-data'});task.reject(new Error('Не удалось проверить данные вселенной.'));}else task.resolve(event.data);}); }
  private request(body:Record<string,unknown>,transfer:Transferable[]=[]):Promise<Reply>{const id=++this.serial;return new Promise((resolve,reject)=>{this.pending.set(id,{kind:typeof body.kind==='string'?body.kind:'unknown',resolve,reject});this.worker.postMessage({...body,id},transfer);});}
  async load(nodes:ArrayBuffer,edges:ArrayBuffer,nodeCount:number,edgeCount:number,history:boolean,topics:Topics,revision:string):Promise<GraphData>{const reply=await this.request({kind:'load',nodes,edges,nodeCount,edgeCount,history,topics,revision},[nodes,edges]);if(!reply.graph)throw new Error('Вселенная не загрузилась');this.bounds=reply.envelopes??null;return reply.graph;}
  async loadPatchedGraph(graph:GraphData,patch:GraphPatch,history:boolean,topics:Topics,revision:string):Promise<GraphData>{const reply=await this.request({kind:'load-patch',graph,patch,history,topics,revision});if(!reply.graph)throw new Error('Обновление не загрузилось');this.bounds=reply.envelopes??null;return reply.graph;}
  async patch(patch:GraphPatch,history:boolean,revision:string):Promise<GraphData>{const reply=await this.request({kind:'patch',patch,history,revision});if(!reply.graph)throw new Error('Обновление не загрузилось');this.bounds=reply.envelopes??null;return reply.graph;}
  async lod(request:LodRequest):Promise<{lod:LodResult;rebuilds:number}>{const reply=await this.request({kind:'lod',request});if(!reply.lod)throw new Error('Проекция вселенной недоступна');return{lod:reply.lod,rebuilds:reply.rebuilds??0};}
  async screen(request:ViewBinding&{view_projection:Float32Array;viewport:readonly[number,number]}):Promise<ScreenConnectivityResult&{view_projection:Float32Array;viewport:readonly[number,number]}>{const reply=await this.request({kind:'screen-connectivity',request});if(!reply.screen)throw new Error('Связи в кадре пока недоступны');return reply.screen;}
  async near(request:NearRequest):Promise<NearResult>{const reply=await this.request({kind:'near',request});if(!reply.near)throw new Error('Текст рядом пока недоступен');return reply.near;}
  async envelopeView(request:EnvelopeRequest&{label_topic_indices:Uint32Array;selected_ordinals:Uint32Array}):Promise<EnvelopeResult&{label_indices:Uint32Array}>{const reply=await this.request({kind:'envelopes',request});if(!reply.envelope_view)throw new Error('Оболочки тем пока недоступны');return reply.envelope_view;}
  async incident(ordinal:number):Promise<Uint32Array>{const reply=await this.request({kind:'incident',ordinal});return reply.indices??new Uint32Array();}
  dispose():void {lifecycle('worker-dispose');this.worker.terminate();for(const task of this.pending.values())task.reject(new Error('Загрузка отменена'));this.pending.clear();}
}
