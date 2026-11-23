import {frustumPlanes} from './spatial.ts';
import type {LodView} from './lod.ts';
/** Exact projected major diameter of a world sphere for the full perspective viewport. */
export function projectedSphereSpan(center:readonly number[],radius:number,view:Pick<LodView,'view_projection'|'viewport'>):number{
  if(radius<=0||!Number.isFinite(radius)||center.length!==3||!center.every(Number.isFinite))return 0;
  for(const plane of frustumPlanes(view.view_projection)){const norm=Math.hypot(plane[0],plane[1],plane[2]),distance=plane[0]*center[0]!+plane[1]*center[1]!+plane[2]*center[2]!+plane[3];if(distance< -radius*norm)return 0;}
  const m=view.view_projection,clip=(row:number)=>m[row]!*center[0]!+m[row+4]!*center[1]!+m[row+8]!*center[2]!+m[row+12]!,depth=clip(3);
  if(depth+radius<=0)return 0;if(depth<=radius)return Infinity;
  const sx=Math.hypot(m[0]!,m[4]!,m[8]!),sy=Math.hypot(m[1]!,m[5]!,m[9]!);if(!sx||!sy)return 0;
  const x=clip(0)/sx,y=clip(1)/sy,den=depth*depth-radius*radius,r2=radius*radius,radial2=x*x+y*y,minor2=r2/den,major2=r2*(den+radial2)/(den*den),delta=major2-minor2;
  const fx=sx*view.viewport[0]/2,fy=sy*view.viewport[1]/2,ux=radial2>0?x/Math.sqrt(radial2):1,uy=radial2>0?y/Math.sqrt(radial2):0;
  const a=fx*fx*(minor2+delta*ux*ux),b=fx*fy*delta*ux*uy,c=fy*fy*(minor2+delta*uy*uy),largest=(a+c+Math.hypot(a-c,2*b))/2;
  return 2*Math.sqrt(Math.max(0,largest));
}
function smooth(start:number,end:number,value:number):number{const t=Math.max(0,Math.min(1,(value-start)/(end-start)));return t*t*(3-2*t);}
/** Camera-only reveal with continuous latch changes and a separate inverse band. */
export function sphereRevealPhase(span:number,split:number,merge:number,wasOpen:boolean):{reveal:number;opened:boolean}{
  if(!Number.isFinite(split)||!Number.isFinite(merge)||merge<=0||split<=merge)throw new Error('Invalid sphere reveal thresholds');
  const full=split+(split-merge),opened=wasOpen?span>merge:span>=full;
  return{reveal:wasOpen?smooth(merge,split,span):smooth(split,full,span),opened};
}
