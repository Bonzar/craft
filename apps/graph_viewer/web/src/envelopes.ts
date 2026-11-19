import { buildSpatialIndex, frustumPlanes, intersects, projectPoint, visitSpatial } from './spatial.ts';
import type { Bounds, SpatialIndex, Vec3 } from './spatial.ts';
import type { GraphData } from './binary.ts';
import type { MembershipIndex } from './lod.ts';
import type { ViewBinding } from './focus.ts';
export interface EnvelopeBounds {revision:string;indices:Uint32Array;centers:Float32Array;radii:Float32Array;direct_visible_counts:Uint32Array}
export interface EnvelopeRequest extends ViewBinding {view_projection:Float32Array;camera_position:Vec3;viewport:readonly[number,number];eligible_topic_indices:Uint32Array;priority_topic_indices:Uint32Array;limit:number}
export interface EnvelopeResult extends ViewBinding {shell_indices:Uint32Array;stats:{branches:number;points:number}}
export function buildEnvelopes(graph:GraphData,index:MembershipIndex,revision:string):EnvelopeBounds{
  const count=index.topics.length;const boxes:(Bounds|null)[]=Array.from({length:count},()=>null);const direct=new Uint32Array(count);let extent=0;
  const include=(slot:number,x:number,y:number,z:number,radius=0):void=>{let box=boxes[slot];if(!box){box={min:[x-radius,y-radius,z-radius],max:[x+radius,y+radius,z+radius]};boxes[slot]=box;return;}const values=[x,y,z];for(let axis=0;axis<3;axis++){box.min[axis]=Math.min(box.min[axis]??Infinity,(values[axis]??0)-radius);box.max[axis]=Math.max(box.max[axis]??-Infinity,(values[axis]??0)+radius);}};
  for(let node=0;node<graph.flags.length;node++){if(!graph.visible[node])continue;const x=graph.positions[node*3]??0,y=graph.positions[node*3+1]??0,z=graph.positions[node*3+2]??0;extent=Math.max(extent,Math.abs(x),Math.abs(y),Math.abs(z));for(let at=index.offsets[node]??0;at<(index.offsets[node+1]??0);at++){const slot=index.memberships[at];if(slot===undefined)continue;include(slot,x,y,z);direct[slot]=(direct[slot]??0)+1;}}
  const centers=new Float32Array(count*3);const radii=new Float32Array(count);const order=Array.from({length:count},(_,slot)=>slot).toSorted((a,b)=>(index.depths[b]??0)-(index.depths[a]??0));
  const padding=Math.max(.5,extent*.001);
  for(const slot of order){
    const box=boxes[slot];let center:[number,number,number]|null=null;let radius=0;
    if(box){center=[(box.min[0]+box.max[0])/2,(box.min[1]+box.max[1])/2,(box.min[2]+box.max[2])/2];radius=Math.max(padding,Math.hypot((box.max[0]-box.min[0])/2,(box.max[1]-box.min[1])/2,(box.max[2]-box.min[2])/2)*1.025);}
    for(const child of index.children[slot]??[]){
      const childRadius=radii[child]??0;if(childRadius<=0)continue;const childCenter:[number,number,number]=[centers[child*3]??0,centers[child*3+1]??0,centers[child*3+2]??0];
      if(!center){center=childCenter;radius=childRadius;continue;}
      const dx=childCenter[0]-center[0],dy=childCenter[1]-center[1],dz=childCenter[2]-center[2];const distance=Math.hypot(dx,dy,dz);
      if(radius>=distance+childRadius)continue;if(childRadius>=distance+radius){center=childCenter;radius=childRadius;continue;}
      const merged=(distance+radius+childRadius)/2;const shift=(merged-radius)/Math.max(Number.EPSILON,distance);center=[center[0]+dx*shift,center[1]+dy*shift,center[2]+dz*shift];radius=merged;
    }
    if(center){centers.set(center,slot*3);radii[slot]=radius;}
  }
  const valid=order.filter(slot=>(radii[slot]??0)>0).toSorted((a,b)=>a-b);const output:EnvelopeBounds={revision,indices:new Uint32Array(valid.length),centers:new Float32Array(valid.length*3),radii:new Float32Array(valid.length),direct_visible_counts:new Uint32Array(valid.length)};
  valid.forEach((slot,i)=>{output.indices[i]=index.topics[slot]?.index??0;output.centers.set(centers.subarray(slot*3,slot*3+3),i*3);output.radii[i]=radii[slot]??0;output.direct_visible_counts[i]=direct[slot]??0;});return output;
}
export function buildEnvelopeIndex(bounds:EnvelopeBounds):SpatialIndex{return buildSpatialIndex(bounds.centers,bounds.radii);}
export function queryEnvelopes(index:SpatialIndex,bounds:EnvelopeBounds,request:EnvelopeRequest):EnvelopeResult{
  const eligible=new Set(request.eligible_topic_indices);const priority=new Set(request.priority_topic_indices);const planes=frustumPlanes(request.view_projection);const best:{slot:number;score:number}[]=[];const stats={branches:0,points:0};
  visitSpatial(index,planes,slot=>{const topic=bounds.indices[slot];if(topic===undefined||!eligible.has(topic))return;const radius=bounds.radii[slot]??0;const x=bounds.centers[slot*3]??0,y=bounds.centers[slot*3+1]??0,z=bounds.centers[slot*3+2]??0;if(!intersects({min:[x-radius,y-radius,z-radius],max:[x+radius,y+radius,z+radius]},planes))return;
    const distance=Math.max(.001,Math.hypot(x-request.camera_position[0],y-request.camera_position[1],z-request.camera_position[2]));const point=projectPoint(request.view_projection,x,y,z,request.viewport[0],request.viewport[1]);const score=(priority.has(topic)?1000000:0)+Math.min(1000,radius/distance*100)-(point.visible?0:50);best.push({slot,score});best.sort((a,b)=>b.score-a.score||a.slot-b.slot);if(best.length>Math.min(48,request.limit))best.pop();},stats);
  return{revision:request.revision,lod_generation:request.lod_generation,camera_generation:request.camera_generation,shell_indices:new Uint32Array(best.map(value=>bounds.indices[value.slot]??0)),stats};
}
