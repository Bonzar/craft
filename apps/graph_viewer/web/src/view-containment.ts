import type {MembershipIndex} from './lod.ts';
/** Viewer-only containment of actual sets AND actual spheres; never a Source parent fact. */
export interface VisualContainment {
  parentsBySlot:number[][];
  childrenBySlot:number[][];
  primaryBySlot:Int32Array;
  aliasesByPrimary:Map<number,number[]>;
}
const cache=new WeakMap<MembershipIndex,{visible:ArrayLike<number>;eligible:string;value:VisualContainment}>();
function subset(a:readonly number[],b:readonly number[]):boolean{let at=0;for(const node of a){while(at<b.length&&b[at]!<node)at++;if(b[at]!==node)return false;}return true;}
export function buildVisualContainment(index:MembershipIndex,visible:ArrayLike<number>,eligibleSlots:readonly number[]):VisualContainment{
  const key=eligibleSlots.join(','),old=cache.get(index);if(old?.visible===visible&&old.eligible===key)return old.value;
  const count=index.topics.length,members:number[][]=Array.from({length:count},()=>[]),eligible=new Set(eligibleSlots),primaryBySlot=new Int32Array(count).fill(-1),aliasesByPrimary=new Map<number,number[]>(),groups=new Map<string,number[]>();
  for(let node=0;node<visible.length;node++)if(visible[node])for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++){const slot=index.memberships[at]!;if(eligible.has(slot))members[slot]!.push(node);}
  const sameBounds=(a:number,b:number):boolean=>{const x=index.topics[a]!,y=index.topics[b]!,tolerance=Math.max(1,x.radius,y.radius)*1e-7;return Math.abs(x.radius-y.radius)<=tolerance&&Math.hypot(...x.position.map((v,i)=>v-y.position[i]!))<=tolerance;};
  // Eligible order is stable Source ViewID order. Equal sets only share a visual
  // outline when their physical bounds also coincide; all IDs remain aliases.
  for(const slot of eligibleSlots){const signature=members[slot]!.join(','),candidates=groups.get(signature)??[],primary=candidates.find(other=>sameBounds(slot,other))??slot;primaryBySlot[slot]=primary;const aliases=aliasesByPrimary.get(primary)??[];aliases.push(slot);aliasesByPrimary.set(primary,aliases);if(primary===slot){candidates.push(slot);groups.set(signature,candidates);}}
  const allParents:number[][]=Array.from({length:count},()=>[]),primaries=[...aliasesByPrimary.keys()];
  for(const child of primaries){const own=members[child]!;if(!own.length)continue;let rare=own[0]!;for(const node of own)if(index.offsets[node+1]!-index.offsets[node]!<index.offsets[rare+1]!-index.offsets[rare]!)rare=node;const candidates=new Set<number>();for(let at=index.offsets[rare]!;at<index.offsets[rare+1]!;at++){const parent=primaryBySlot[index.memberships[at]!]!;if(parent>=0)candidates.add(parent);}
    for(const parent of candidates){if(parent===child||members[parent]!.length<=own.length)continue;const a=index.topics[child]!,b=index.topics[parent]!,distance=Math.hypot(...a.position.map((v,i)=>v-b.position[i]!)),tolerance=Math.max(1,a.radius,b.radius)*1e-7;if(distance+a.radius>b.radius+tolerance||!subset(own,members[parent]!))continue;allParents[child]!.push(parent);}
  }
  const parentsBySlot:number[][]=Array.from({length:count},()=>[]),childrenBySlot:number[][]=Array.from({length:count},()=>[]);
  // Transitive reduction prevents an opened broad View from skipping its still
  // closed intermediate detail View. Multiple incomparable containers remain.
  for(const child of primaries){const parents=allParents[child]!;parentsBySlot[child]=parents.filter(parent=>!parents.some(mid=>mid!==parent&&allParents[mid]!.includes(parent)));for(const parent of parentsBySlot[child]!)childrenBySlot[parent]!.push(child);}
  const value={parentsBySlot,childrenBySlot,primaryBySlot,aliasesByPrimary};cache.set(index,{visible,eligible:key,value});return value;
}
