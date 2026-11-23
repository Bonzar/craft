import type {MembershipIndex} from './lod.ts';

/** Camera-derived claim scopes, not Source parent facts or stored expansion state. */
export interface CameraClaimScopes {
  active_topic_indices:Uint32Array;
  open_topic_indices:Uint32Array;
  context_topic_indices:Uint32Array;
  applicable_memberships:{offsets:Uint32Array;topic_indices:Uint32Array};
  /** Maximum applicable open path: a closed incomparable membership never vetoes it. */
  node_reveal:Float32Array;
  membership_reveals:Float32Array;
}

/**
 * Traverse every containing cover root, then only real qualified child memberships.
 * An expanded ancestor applies to residual claims only; for child claims it is
 * context. The resulting CSR lets those two roles share the same Source ViewID.
 */
export function cameraClaimScopes(index:MembershipIndex,visible:ArrayLike<number>,rootSlots:Iterable<number>,childrenBySlot:readonly (readonly number[])[],phaseBySlot:ArrayLike<number>,primaryBySlot?:ArrayLike<number>):CameraClaimScopes {
  const n=visible.length,t=index.topics.length;
  if(index.offsets.length!==n+1||childrenBySlot.length!==t||phaseBySlot.length!==t)throw new Error('Camera claim-scope dimensions mismatch');
  const roots=[...new Set(rootSlots)],counts=new Uint32Array(t);
  for(const root of roots)if(!Number.isInteger(root)||root<0||root>=t)throw new Error('Unknown cover root');
  for(let node=0;node<n;node++)if(visible[node])for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++)counts[index.memberships[at]!]!++;
  const compare=(a:number,b:number)=>{const x=index.topics[a]!.source_ref?.view_id||index.topics[a]!.id,y=index.topics[b]!.source_ref?.view_id||index.topics[b]!.id;return x<y?-1:x>y?1:0;};
  const active=new Set<number>(),open=new Set<number>(),contexts=new Set<number>(),offsets=new Uint32Array(n+1),topics:number[]=[],reveals:number[]=[],nodeReveal=new Float32Array(n);
  for(let node=0;node<n;node++){
    offsets[node]=topics.length;if(!visible[node])continue;
    const members=new Set<number>();for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++){const slot=index.memberships[at]!,primary=primaryBySlot?.[slot]??slot;if(primary>=0)members.add(primary);}
    const leaves=new Map<number,number>(),reached=new Map<number,number>(),queue:[number,number][]=[];
    for(const root of roots)if(members.has(root))queue.push([root,1]);
    for(let head=0;head<queue.length;head++){
      const [slot,path]=queue[head]!;if((reached.get(slot)??-1)>=path)continue;reached.set(slot,path);
      const raw=phaseBySlot[slot]!;if(!Number.isFinite(raw)||raw<0||raw>1)throw new Error('Invalid camera scope phase');
      const phase=counts[slot]===1?1:raw,effective=Math.min(path,phase),children=phase>0?childrenBySlot[slot]!.filter(child=>members.has(child)):[];
      if(children.length){contexts.add(slot);for(const child of children){if(child===slot)throw new Error('Cyclic camera claim scopes');queue.push([child,effective]);}}
      else leaves.set(slot,Math.max(leaves.get(slot)??0,effective));
    }
    for(const slot of [...leaves.keys()].sort(compare)){const reveal=leaves.get(slot)!;active.add(slot);if(reveal>0)open.add(slot);topics.push(index.topics[slot]!.index);reveals.push(reveal);nodeReveal[node]=Math.max(nodeReveal[node]!,reveal);}
    // No applicable Source scope means an actual canonical fallback, not a new group.
    if(!leaves.size)nodeReveal[node]=1;
  }
  offsets[n]=topics.length;const ids=(slots:Set<number>)=>new Uint32Array([...slots].sort(compare).map(slot=>index.topics[slot]!.index));
  return{active_topic_indices:ids(active),open_topic_indices:ids(open),context_topic_indices:ids(contexts),applicable_memberships:{offsets,topic_indices:new Uint32Array(topics)},node_reveal:nodeReveal,membership_reveals:new Float32Array(reveals)};
}
