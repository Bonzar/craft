import { buildViewportConnections } from './viewport-connections.ts';
import type { ViewportConnections } from './viewport-connections.ts';
import { projectPoint } from './spatial.ts';
import type { ViewBinding } from './focus.ts';
import { decodeGraph, incidentIndices, patchGraph } from './binary.ts';
import { buildSpatialIndex, queryNear } from './focus.ts';
import type { NearRequest } from './focus.ts';
import type { SpatialIndex } from './spatial.ts';
import { buildEnvelopes, buildEnvelopeIndex, queryEnvelopes } from './envelopes.ts';
import type { EnvelopeBounds, EnvelopeRequest } from './envelopes.ts';
import { buildMembershipIndex, projectLod } from './lod.ts';
import type { GraphData, GraphPatch } from './binary.ts';
import type { Topics } from './contracts.ts';
import type { LodRequest, LodResult, MembershipIndex } from './lod.ts';
let connectivity:ViewportConnections|null=null;
let pointIndex:SpatialIndex|null=null;let envelopeIndex:SpatialIndex|null=null;let envelopes:EnvelopeBounds|null=null;
let graph:GraphData|null=null;let membership:MembershipIndex|null=null;let revision='';let cached:LodResult|null=null;let cacheKey='';let rebuilds=0;
type WorkerRequest={id:number;kind:'load';nodes:ArrayBuffer;edges:ArrayBuffer;nodeCount:number;edgeCount:number;history:boolean;topics:Topics;revision:string}|{id:number;kind:'load-patch';graph:GraphData;patch:GraphPatch;history:boolean;topics:Topics;revision:string}|{id:number;kind:'patch';patch:GraphPatch;history:boolean;revision:string}|{id:number;kind:'incident';ordinal:number}|{id:number;kind:'lod';request:LodRequest}|{id:number;kind:'screen-connectivity';request:ViewBinding&{view_projection:Float32Array;viewport:readonly[number,number]}}|{id:number;kind:'near';request:NearRequest}|{id:number;kind:'envelopes';request:EnvelopeRequest&{label_topic_indices?:Uint32Array;selected_ordinals?:Uint32Array}};
self.addEventListener('message',(event:MessageEvent<WorkerRequest>)=>{
  const task=event.data;
  try{
    if(task.kind==='load'){graph=decodeGraph(task.nodes,task.edges,task.nodeCount,task.edgeCount,task.history);membership=buildMembershipIndex(task.nodeCount,task.topics.topics,task.topics.memberships,task.topics.links);revision=task.revision;cached=null;cacheKey='';pointIndex=buildSpatialIndex(graph.positions);envelopes=buildEnvelopes(graph,membership,revision);envelopeIndex=buildEnvelopeIndex(envelopes);}
    if(task.kind==='load-patch'){graph=task.graph;membership=buildMembershipIndex(graph.flags.length,task.topics.topics,task.topics.memberships,task.topics.links);}
    if(task.kind==='patch'||task.kind==='load-patch'){
      if(!graph||!membership)throw new Error('Graph not loaded');graph=patchGraph(graph,task.patch,task.history);revision=task.revision;cached=null;cacheKey='';
      if(membership.offsets.length!==graph.flags.length+1){const offsets=new Uint32Array(graph.flags.length+1).fill(membership.memberships.length);offsets.set(membership.offsets);membership={...membership,offsets};}
      pointIndex=buildSpatialIndex(graph.positions);envelopes=buildEnvelopes(graph,membership,revision);envelopeIndex=buildEnvelopeIndex(envelopes);
    }
    if(!graph||!membership)throw new Error('Graph not loaded');
    if(task.kind==='screen-connectivity'){if(!cached||task.request.revision!==revision||task.request.lod_generation!==cached.generation)throw new Error('Expired connectivity query');if(!connectivity||connectivity.revision!==revision||connectivity.lod_generation!==cached.generation)connectivity=buildViewportConnections(cached,graph.flags.length);const onscreen=new Uint32Array(connectivity.active.length);let count=0;for(let representative=0;representative<connectivity.active.length;representative++){if(!connectivity.active[representative])continue;const p=connectivity.positions;const projected=projectPoint(task.request.view_projection,p[representative*3]??0,p[representative*3+1]??0,p[representative*3+2]??0,task.request.viewport[0],task.request.viewport[1]);if(projected.visible)onscreen[count++]=representative;}const screen=connectivity.update(onscreen.subarray(0,count),task.request.camera_generation);self.postMessage({id:task.id,screen:{...screen,view_projection:task.request.view_projection,viewport:task.request.viewport}});}
    else if(task.kind==='near'){if(!pointIndex||!cached||task.request.revision!==revision||task.request.lod_generation!==cached.generation)throw new Error('Expired near query');self.postMessage({id:task.id,near:queryNear(pointIndex,graph.positions,graph.visible,cached.node_render_visible,task.request)});}
    else if(task.kind==='envelopes'){if(!envelopes||!envelopeIndex||!cached||task.request.revision!==revision||task.request.lod_generation!==cached.generation)throw new Error('Expired envelope query');const priorities=new Set(task.request.priority_topic_indices);for(const ordinal of task.request.selected_ordinals??[]){for(let at=membership.offsets[ordinal]??0;at<(membership.offsets[ordinal+1]??0);at++){const topic=membership.topics[membership.memberships[at]??-1];if(topic)priorities.add(topic.index);}}const query={...task.request,priority_topic_indices:new Uint32Array(priorities)};const result=queryEnvelopes(envelopeIndex,envelopes,query);const labels=queryEnvelopes(envelopeIndex,envelopes,{...query,eligible_topic_indices:task.request.label_topic_indices??task.request.eligible_topic_indices,limit:48});self.postMessage({id:task.id,envelope_view:{...result,label_indices:labels.shell_indices}});}
    else if(task.kind==='incident'){const indices=incidentIndices(graph,task.ordinal);self.postMessage({id:task.id,indices},{transfer:[indices.buffer]});}
    else if(task.kind==='lod'){
      if(task.request.revision!==revision)throw new Error('Expired LOD revision');
      const key=`${revision}|${task.request.collapsed_topic_indices.toSorted().join(',')}|${task.request.force_reveal_ordinals.toSorted().join(',')}`;
      let lod:LodResult;
      if(cached&&cacheKey===key)lod={...cached,generation:task.request.generation,proxies:cached.proxies.map(proxy=>({...proxy,generation:task.request.generation}))};
      else{lod=projectLod(graph,membership,task.request);rebuilds++;cached=lod;cacheKey=key;}
      cached=lod;self.postMessage({id:task.id,lod,rebuilds});
    }else self.postMessage({id:task.id,graph,envelopes});
  }catch(error){self.postMessage({id:task.id,error:error instanceof Error?error.message:'Invalid graph'});}
});
