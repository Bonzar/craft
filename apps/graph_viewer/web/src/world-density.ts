import {buildSpatialIndex} from './spatial.ts';
import type {SpatialIndex} from './spatial.ts';
export const DENSITY_SAMPLE_LIMIT=1024;
const POINT_QUERY_LIMIT=4096;
const BRANCH_QUERY_LIMIT=8192;
export interface WorldSpacing {p25:number;samples:number;pointVisits:number;branchVisits:number}
/** Fixed world-unit spacing, independent of camera, selection and Source roles. */
export function worldSpacing(positions:Float32Array):WorldSpacing|null{
  const count=positions.length/3;if(count<2)return null;
  const index=buildSpatialIndex(positions),samples=Math.min(count,DENSITY_SAMPLE_LIMIT),distances:number[]=[];let pointVisits=0,branchVisits=0;
  for(let sample=0;sample<samples;sample++){
    const ordinal=Math.floor(sample*(count-1)/(samples-1)),offset=ordinal*3,x=positions[offset]!,y=positions[offset+1]!,z=positions[offset+2]!;let best=Infinity,points=0,branches=0;
    const distance=(branch:SpatialIndex['root']):number=>{let sum=0;for(let axis=0;axis<3;axis++){const value=positions[offset+axis]!,delta=Math.max(branch.bounds.min[axis]!-value,0,value-branch.bounds.max[axis]!);sum+=delta*delta;}return sum;};
    const walk=(branch:SpatialIndex['root']):void=>{
      if(points>=POINT_QUERY_LIMIT||branches>=BRANCH_QUERY_LIMIT||distance(branch)>=best)return;branches++;
      if(branch.children.length){const first=branch.children[0]!,second=branch.children[1]!;if(distance(first)<=distance(second)){walk(first);walk(second);}else{walk(second);walk(first);}return;}
      for(const node of branch.ordinals){if(points++>=POINT_QUERY_LIMIT)break;if(node===ordinal)continue;const at=node*3,d=(positions[at]!-x)**2+(positions[at+1]!-y)**2+(positions[at+2]!-z)**2;if(d>0&&d<best)best=d;}
    };
    walk(index.root);pointVisits+=points;branchVisits+=branches;if(Number.isFinite(best))distances.push(Math.sqrt(best));
  }
  if(!distances.length)return null;distances.sort((a,b)=>a-b);return{p25:distances[Math.floor((distances.length-1)*.25)]!,samples:distances.length,pointVisits,branchVisits};
}
