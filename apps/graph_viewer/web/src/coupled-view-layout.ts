import type {VisualTheme3D} from './visual-layout-3d.ts';
export const COUPLED_VIEW_PASSES=16;
const PAIR_LIMIT=32768,INTERSECTION_LIMIT=2000000;
export interface CoupledViewWork {iterations:number;pairTests:number;disjointPairs:number;intersectionVisits:number;nodeVisits:number;membershipVisits:number;edgeVisits:number}
/** Coupled hypergraph geometry: forces move real members, never an independent shell. */
export function coupleViewMembers(positions:Float32Array,themes:readonly VisualTheme3D[],seeds:Uint32Array,edges:Uint32Array,edgeActive:Uint8Array|undefined,unit:number,nodeIds?:readonly string[]):CoupledViewWork{
  const work:CoupledViewWork={iterations:0,pairTests:0,disjointPairs:0,intersectionVisits:0,nodeVisits:0,membershipVisits:0,edgeVisits:0};if(themes.length<2)return work;
  const count=positions.length/3,stamp=new Uint32Array(count),rawViews=themes.toSorted((a,b)=>a.id<b.id?-1:a.id>b.id?1:0).map((theme,slot)=>{const members:number[]=[];for(let i=0;i<theme.members.length;i++){const node=theme.members[i]!;work.membershipVisits++;if(stamp[node]===slot+1)continue;stamp[node]=slot+1;members.push(node);}members.sort((a,b)=>nodeIds?(nodeIds[a]!<nodeIds[b]!?-1:nodeIds[a]!>nodeIds[b]!?1:0):seeds[a]!-seeds[b]!||a-b);return{members};}).filter(view=>view.members.length);
  // Equal actual sets contribute ONE geometric constraint, while the caller
  // retains every distinct Source View identity and its independently read card.
  const groups=new Map<string,number[]>(),views:typeof rawViews=[];
  for(const view of rawViews){let sum=0,xor=0;for(const node of view.members){sum=(sum+seeds[node]!)>>>0;xor^=seeds[node]!;}const key=`${view.members.length}:${sum}:${xor}`,bucket=groups.get(key)??[];let same=false;for(const index of bucket){const prior=views[index]!;if(prior.members.every((node,i)=>node===view.members[i])){same=true;break;}}if(!same){bucket.push(views.length);views.push(view);groups.set(key,bucket);}}
  stamp.fill(0);const pairs:number[]=[];
  for(let a=0;a<views.length&&work.pairTests<PAIR_LIMIT&&work.intersectionVisits<INTERSECTION_LIMIT;a++){
    for(const node of views[a]!.members)stamp[node]=a+1;
    const remaining=views.length-a-1,quota=Math.min(remaining,Math.max(1,Math.floor((PAIR_LIMIT-work.pairTests)/(views.length-a))));
    for(let part=0;part<quota&&work.pairTests<PAIR_LIMIT&&work.intersectionVisits<INTERSECTION_LIMIT;part++){
      const b=a+1+Math.floor(part*remaining/quota);let shared=false,complete=true;work.pairTests++;
      for(const node of views[b]!.members){if(work.intersectionVisits++>=INTERSECTION_LIMIT){complete=false;break;}if(stamp[node]===a+1){shared=true;break;}}
      if(complete&&!shared)pairs.push(a,b);
    }
  }
  work.disjointPairs=pairs.length/2;if(!pairs.length)return work;
  const dispersion=new Float32Array(positions.length);for(let node=0;node<count;node++)for(let k=0;k<3;k++)dispersion[node*3+k]=(((seeds[node]!>>>(k*8))&255)/255*2-1)*unit*.05;
  const weights=new Float64Array(count),centres=new Float64Array(views.length*3),radii=new Float64Array(views.length),forces=new Float64Array(views.length*3),contacts=new Uint32Array(views.length),sums=new Float64Array(positions.length),edgeSums=new Float64Array(positions.length),degrees=new Uint32Array(count);
  for(const view of views)for(const node of view.members)weights[node]=weights[node]!+1/view.members.length;
  for(let pass=0;pass<COUPLED_VIEW_PASSES;pass++){
    work.iterations++;centres.fill(0);radii.fill(unit*.005);forces.fill(0);contacts.fill(0);sums.fill(0);edgeSums.fill(0);degrees.fill(0);
    for(let a=0;a<views.length;a++){const view=views[a]!;for(const node of view.members){work.membershipVisits++;for(let k=0;k<3;k++)centres[a*3+k]=centres[a*3+k]!+positions[node*3+k]!/view.members.length;}for(const node of view.members){work.membershipVisits++;radii[a]=Math.max(radii[a]!,Math.hypot(positions[node*3]!-centres[a*3]!,positions[node*3+1]!-centres[a*3+1]!,positions[node*3+2]!-centres[a*3+2]!));}}
    for(let pair=0;pair<pairs.length;pair+=2){const a=pairs[pair]!,b=pairs[pair+1]!,dx=centres[a*3]!-centres[b*3]!,dy=centres[a*3+1]!-centres[b*3+1]!,dz=centres[a*3+2]!-centres[b*3+2]!,distance=Math.hypot(dx,dy,dz),overlap=radii[a]!+radii[b]!+unit*.05-distance;if(overlap<=0)continue;const scale=overlap*.1/Math.max(1e-9,distance),delta=[dx*scale,dy*scale,dz*scale];for(let k=0;k<3;k++){forces[a*3+k]=forces[a*3+k]!+delta[k]!;forces[b*3+k]=forces[b*3+k]!-delta[k]!;}contacts[a]=contacts[a]!+1;contacts[b]=contacts[b]!+1;}
    for(let a=0;a<views.length;a++)for(const node of views[a]!.members){work.membershipVisits++;for(let k=0;k<3;k++)sums[node*3+k]=sums[node*3+k]!+(forces[a*3+k]!/Math.max(1,contacts[a]!)+(centres[a*3+k]!+dispersion[node*3+k]!-positions[node*3+k]!)*.2)/views[a]!.members.length;}
    for(let edge=0;edge<edges.length/2;edge++){work.edgeVisits++;if(edgeActive&&!edgeActive[edge])continue;const a=edges[edge*2]!,b=edges[edge*2+1]!;if(a===b)continue;degrees[a]=degrees[a]!+1;degrees[b]=degrees[b]!+1;for(let k=0;k<3;k++){edgeSums[a*3+k]=edgeSums[a*3+k]!+positions[b*3+k]!;edgeSums[b*3+k]=edgeSums[b*3+k]!+positions[a*3+k]!;}}
    for(let node=0;node<count;node++){work.nodeVisits++;const delta=[0,0,0];for(let k=0;k<3;k++)delta[k]=(weights[node]?sums[node*3+k]!/weights[node]!:0)+(degrees[node]?(edgeSums[node*3+k]!/degrees[node]!-positions[node*3+k]!)*.005:0);const scale=Math.min(1,unit*.2/Math.max(1e-9,Math.hypot(...delta)));for(let k=0;k<3;k++)positions[node*3+k]=positions[node*3+k]!+delta[k]!*scale;}
  }
  return work;
}
