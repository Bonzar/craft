import type {LodView} from './lod.ts';
type Point=readonly[number,number,number];
/** A far connector terminates on the actual world sphere, never a centre node. */
export function worldEdgeBoundary(center:Point,radius:number,toward:Point):[number,number,number]{
  const dx=toward[0]-center[0],dy=toward[1]-center[1],dz=toward[2]-center[2],distance=Math.hypot(dx,dy,dz);if(!radius||!distance)return[...center];return[center[0]+dx/distance*radius,center[1]+dy/distance*radius,center[2]+dz/distance*radius];
}
/** Overlapping/touching spheres have no outside gap: never invert the chord. */
export function worldEdgeBoundarySegment(a:Point,ra:number,b:Point,rb:number):readonly [[number,number,number],[number,number,number]]|null{
  if(Math.hypot(b[0]-a[0],b[1]-a[1],b[2]-a[2])<=ra+rb)return null;return[worldEdgeBoundary(a,ra,b),worldEdgeBoundary(b,rb,a)];
}
/** Conservative perspective span of the same physical world bounds as a view. */
export function worldEdgeProjectedSpan(center:Point,radius:number,view:Pick<LodView,'view_projection'|'viewport'>):number{
  const m=view.view_projection,cw=m[3]!*center[0]+m[7]!*center[1]+m[11]!*center[2]+m[15]!;if(cw+radius*Math.hypot(m[3]!,m[7]!,m[11]!)<=0)return 0;let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
  for(let corner=0;corner<8;corner++){const x=center[0]+(corner&1?radius:-radius),y=center[1]+(corner&2?radius:-radius),z=center[2]+(corner&4?radius:-radius),w=m[3]!*x+m[7]!*y+m[11]!*z+m[15]!,depth=m[2]!*x+m[6]!*y+m[10]!*z+m[14]!;if(w<=0||depth< -w)return Infinity;const px=(m[0]!*x+m[4]!*y+m[8]!*z+m[12]!)/w*view.viewport[0]/2,py=(m[1]!*x+m[5]!*y+m[9]!*z+m[13]!)/w*view.viewport[1]/2;xmin=Math.min(xmin,px);xmax=Math.max(xmax,px);ymin=Math.min(ymin,py);ymax=Math.max(ymax,py);}return Math.max(xmax-xmin,ymax-ymin);
}
/** Camera scale is reversible and has no timer or dependence on star-body LOD. */
export function worldEdgeReveal(span:number,start=4,complete=8):number{const t=Math.max(0,Math.min(1,(span-start)/(complete-start)));return t*t*(3-2*t);}
export function worldEdgeEndpoint(boundary:Point,actual:Point,mix:number):[number,number,number]{if(mix>=1)return[...actual];if(mix<=0)return[...boundary];return[boundary[0]+(actual[0]-boundary[0])*mix,boundary[1]+(actual[1]-boundary[1])*mix,boundary[2]+(actual[2]-boundary[2])*mix];}
