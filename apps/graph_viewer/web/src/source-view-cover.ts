import type {MembershipIndex} from './lod.ts';
export interface SourceViewCover {slots:Uint32Array;mapped_nodes:Uint32Array;unassigned_nodes:Uint32Array}
const cache=new WeakMap<MembershipIndex,{visible:ArrayLike<number>;key:string;value:SourceViewCover}>();
/** Stable presentation cover by EXISTING Source Views; neither a semantic hierarchy nor new groups. */
export function buildSourceViewCover(index:MembershipIndex,visible:ArrayLike<number>,primarySlots:readonly number[]):SourceViewCover{
  const viewId=(slot:number)=>index.topics[slot]!.source_ref?.view_id||index.topics[slot]!.id,slots=[...primarySlots].sort((a,b)=>viewId(a)<viewId(b)?-1:viewId(a)>viewId(b)?1:a-b),key=slots.join(','),old=cache.get(index);if(old?.visible===visible&&old.key===key)return old.value;
  const members=new Map(slots.map(slot=>[slot,[] as number[]])),primary=new Set(slots),mapped:number[]=[],unassigned:number[]=[];
  for(let node=0;node<visible.length;node++){if(!visible[node])continue;let owned=false;for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++){const slot=index.memberships[at]!;if(primary.has(slot)){members.get(slot)!.push(node);owned=true;}}(owned?mapped:unassigned).push(node);}
  // The normal personal-view case is small. Large View catalogs retain their
  // complete real cover rather than creating quadratic work or dropping facts.
  if(slots.length>512){const value={slots:new Uint32Array(slots),mapped_nodes:new Uint32Array(mapped),unassigned_nodes:new Uint32Array(unassigned)};cache.set(index,{visible,key,value});return value;}
  const radii=slots.map(slot=>index.topics[slot]!.radius).sort((a,b)=>a-b),median=Math.max(1e-9,radii[Math.floor(radii.length/2)]??1),left=new Uint8Array(visible.length),chosen:number[]=[];for(const node of mapped)left[node]=1;let remaining=mapped.length;
  const intersects=(a:number,b:number):boolean=>{const x=index.topics[a]!,y=index.topics[b]!;return Math.hypot(...x.position.map((v,i)=>v-y.position[i]!))<x.radius+y.radius;};
  while(remaining){let best=-1,bestScore=-1;for(const slot of slots){if(chosen.includes(slot))continue;let gain=0;for(const node of members.get(slot)!)gain+=left[node]!;if(!gain)continue;const overlap=chosen.reduce((sum,other)=>sum+Number(intersects(slot,other)),0),extent=Math.min(16,(index.topics[slot]!.radius/median)**2),score=gain/(1+.25*(overlap+extent));if(score>bestScore){best=slot;bestScore=score;}}if(best<0)throw new Error('Source View cover lost an eligible member');chosen.push(best);for(const node of members.get(best)!)if(left[node]){left[node]=0;remaining--;}}
  // Remove redundant real Views without merging any Source identity. The full
  // catalog and coincidence aliases remain outside this visual cover unchanged.
  const coverage=new Uint32Array(visible.length);for(const slot of chosen)for(const node of members.get(slot)!)coverage[node]=(coverage[node]??0)+1;
  for(let i=chosen.length-1;i>=0;i--){const nodes=members.get(chosen[i]!)!;if(nodes.every(node=>coverage[node]!>1)){for(const node of nodes)coverage[node]=(coverage[node]??0)-1;chosen.splice(i,1);}}
  const selected=new Set(chosen),value={slots:new Uint32Array(slots.filter(slot=>selected.has(slot))),mapped_nodes:new Uint32Array(mapped),unassigned_nodes:new Uint32Array(unassigned)};cache.set(index,{visible,key,value});return value;
}
