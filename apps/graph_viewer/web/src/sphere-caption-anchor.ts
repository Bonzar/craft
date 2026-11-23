import {Vector3,Vector4} from 'three';
import type {PerspectiveCamera} from 'three';
export interface SphereCaptionViewport {width:number;height:number;top?:number;bottom?:number;margin?:number}
export interface SphereCaptionAnchor {x:number;y:number;visible:true;world:[number,number,number];angle:number}
const TAU=2*Math.PI;
const wrap=(angle:number):number=>(angle%TAU+TAU)%TAU;
/** A point on the actual perspective silhouette, never a projected centre proxy. */
export function sphereCaptionAnchor(center:readonly[number,number,number],radius:number,camera:PerspectiveCamera,viewport:SphereCaptionViewport,previousAngle?:number):SphereCaptionAnchor|null{
  if(!Number.isFinite(radius)||radius<=0||viewport.width<=0||viewport.height<=0)return null;
  const c=new Vector3(...center).applyMatrix4(camera.matrixWorldInverse),distance=c.length();
  // An observer inside the volume has no visible silhouette; a centre label
  // would invent a boundary. The existing selection/Reader remains available.
  if(!Number.isFinite(distance)||distance<=radius)return null;
  const normal=c.clone().divideScalar(distance),down=new Vector3(0,-1,0),a=down.addScaledVector(normal,-down.dot(normal));
  if(a.lengthSq()<1e-12)a.set(1,0,0).addScaledVector(normal,-normal.x);
  a.normalize();const b=new Vector3().crossVectors(normal,a).normalize(),k=1-radius*radius/(distance*distance),base=c.clone().multiplyScalar(k),extent=radius*Math.sqrt(k);
  a.multiplyScalar(extent);b.multiplyScalar(extent);
  const clip=(point:Vector3,w:number)=>new Vector4(point.x,point.y,point.z,w).applyMatrix4(camera.projectionMatrix),p=clip(base,1),u=clip(a,0),v=clip(b,0),margin=viewport.margin??15,top=viewport.top??0,bottom=viewport.bottom??0;
  const bounds=[(margin/viewport.width)*2-1,1-(margin/viewport.width)*2,1-(viewport.height-bottom)/viewport.height*2,1-top/viewport.height*2];
  const angles=[previousAngle??0,0,Math.PI/2,Math.PI,Math.PI*1.5];
  // Include exact intersections of the projected tangent circle with each
  // viewport/clip plane, so even a narrow visible off-axis arc is discoverable.
  const intersections=(axis:0|1|2,limit:number):void=>{const constant=p.getComponent(axis)-limit*p.w,cos=u.getComponent(axis)-limit*u.w,sin=v.getComponent(axis)-limit*v.w,amplitude=Math.hypot(cos,sin);if(amplitude<=1e-12||Math.abs(constant)>amplitude)return;const phase=Math.atan2(sin,cos),offset=Math.acos(Math.max(-1,Math.min(1,-constant/amplitude)));for(const angle of[phase-offset,phase+offset])angles.push(angle,angle-1e-7,angle+1e-7);};
  intersections(0,bounds[0]!);intersections(0,bounds[1]!);intersections(1,bounds[2]!);intersections(1,bounds[3]!);intersections(2,-1);intersections(2,1);
  const preferred=wrap(previousAngle??0);let best:SphereCaptionAnchor|null=null,bestScore=Infinity;
  for(const value of angles){const angle=wrap(value),point=base.clone().addScaledVector(a,Math.cos(angle)).addScaledVector(b,Math.sin(angle)),projected=clip(point,1);if(projected.w<=0)continue;const x=projected.x/projected.w,y=projected.y/projected.w,z=projected.z/projected.w,tolerance=1e-8;if(z< -1||z>1||x<bounds[0]!-tolerance||x>bounds[1]!+tolerance||y<bounds[2]!-tolerance||y>bounds[3]!+tolerance)continue;const delta=Math.abs(angle-preferred),score=Math.min(delta,TAU-delta);if(score>=bestScore)continue;bestScore=score;best={x:(x*.5+.5)*viewport.width,y:(.5-y*.5)*viewport.height,visible:true,world:point.applyMatrix4(camera.matrixWorld).toArray() as[number,number,number],angle};}
  return best;
}
/** Try other real rim points when label occupancy blocks the preferred one. */
export function sphereCaptionCandidates(center:readonly[number,number,number],radius:number,camera:PerspectiveCamera,viewport:SphereCaptionViewport,previousAngle?:number):SphereCaptionAnchor[]{
  const first=sphereCaptionAnchor(center,radius,camera,viewport,previousAngle);if(!first)return[];
  const result=[first];for(let i=0;i<16;i++){const point=sphereCaptionAnchor(center,radius,camera,viewport,i*Math.PI/8);if(point&&!result.some(old=>Math.hypot(old.x-point.x,old.y-point.y)<1))result.push(point);}return result;
}

export interface CaptionSphereOccluder {center:readonly[number,number,number];radius:number;reveal:number;opacity:number}
/** Only a genuinely closed, visible foreground scope can hide a Source anchor. */
export function sphereCaptionOccluded(point:readonly[number,number,number],camera:readonly[number,number,number],occluders:readonly CaptionSphereOccluder[]):boolean{const ray=point.map((value,k)=>value-camera[k]!) as[number,number,number],distance=Math.hypot(...ray);if(distance<=0)return true;for(let k=0;k<3;k++)ray[k]=ray[k]!/distance;for(const other of occluders){if(other.radius<=0||other.reveal>=.5||other.opacity<.5)continue;const centre=other.center.map((value,k)=>value-camera[k]!) as[number,number,number],centreDistance=Math.hypot(...centre);if(centreDistance<=other.radius*1.35)continue;const along=ray[0]*centre[0]+ray[1]*centre[1]+ray[2]*centre[2],disc=along*along-centreDistance*centreDistance+other.radius*other.radius;if(disc<=0)continue;const hit=along-Math.sqrt(disc);if(hit>0&&hit<distance-Math.max(1e-5,other.radius*1e-4))return true;}return false;}
