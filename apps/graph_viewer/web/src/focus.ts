import { buildSpatialIndex, frustumPlanes, projectPoint, visitSpatial } from './spatial.ts';
import type { SpatialIndex, Vec3 } from './spatial.ts';
export { buildSpatialIndex } from './spatial.ts';
export interface ViewBinding {revision:string;lod_generation:number;camera_generation:number}
export interface NearRequest extends ViewBinding {view_projection:Float32Array;camera_position:Vec3;camera_target?:Vec3;focus_screen:readonly[number,number];viewport:readonly[number,number];activation:{near:boolean;explicit_ordinal?:number};limit:number;blocked_rects?:readonly (readonly[number,number,number,number])[]}
export interface NearCandidate {ordinal:number;screen_x:number;screen_y:number;depth:number;score:number}
export interface NearResult extends ViewBinding {candidates:NearCandidate[];stats:{branches:number;points:number}}
export function viewBindingMatches(current:ViewBinding,result:ViewBinding):boolean{return current.revision===result.revision&&current.lod_generation===result.lod_generation&&current.camera_generation===result.camera_generation;}
export function nearPreviewActive(previous:boolean,ratio:number,explicit:boolean):boolean{return explicit||ratio<(previous ? .55 : .45);}
export function queryNear(index:SpatialIndex,positions:Float32Array,visible:Float32Array,canonical:Float32Array,request:NearRequest):NearResult{
  const candidates:NearCandidate[]=[];const stats={branches:0,points:0};const result={revision:request.revision,lod_generation:request.lod_generation,camera_generation:request.camera_generation,candidates,stats};if(!request.activation.near)return result;
  const [width,height]=request.viewport;const [focusX,focusY]=request.focus_screen;const radius=Math.min(width*.32,170);
  const planes=frustumPlanes(request.view_projection,Math.max(-1,(focusX-radius)/width*2-1),Math.min(1,(focusX+radius)/width*2-1),Math.max(-1,1-(focusY+radius)/height*2),Math.min(1,1-(focusY-radius)/height*2));
  const target=request.camera_target??request.camera_position;const cameraDistance=Math.max(1,Math.hypot(request.camera_position[0]-target[0],request.camera_position[1]-target[1],request.camera_position[2]-target[2]));
  visitSpatial(index,planes,ordinal=>{
    if(!visible[ordinal]||!canonical[ordinal])return;const x=positions[ordinal*3]??0,y=positions[ordinal*3+1]??0,z=positions[ordinal*3+2]??0;const point=projectPoint(request.view_projection,x,y,z,width,height);if(!point.visible)return;
    if(request.blocked_rects?.some(rect=>point.x>=rect[0]&&point.x<=rect[2]&&point.y>=rect[1]&&point.y<=rect[3]))return;
    const screenDistance=Math.hypot(point.x-focusX,point.y-focusY);if(screenDistance>radius)return;
    const proximity=Math.hypot(x-target[0],y-target[1],z-target[2])/cameraDistance;
    const score=screenDistance+Math.min(100,proximity*18)-(ordinal===request.activation.explicit_ordinal?45:0);
    candidates.push({ordinal,screen_x:point.x,screen_y:point.y,depth:point.depth,score});candidates.sort((a,b)=>a.score-b.score||a.ordinal-b.ordinal);if(candidates.length>Math.min(12,request.limit))candidates.pop();
  },stats);
  return result;
}
