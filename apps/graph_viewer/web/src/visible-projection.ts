import type {GraphData} from './binary.ts';
import type {LodResult,LodRequest,MembershipIndex,TopicProxy} from './lod.ts';
import {finalizeProjection} from './projection-ledger.ts';
export interface VisibleProjectionRequest {
  revision:string;generation:number;
  /** Current claim-scope frontier. Ancestor G may apply only to residual claims via the explicit CSR below. */
  active_topic_indices:Uint32Array;open_topic_indices:Uint32Array;
  preferred_open_topic_index?:number|null;
  /** Source topic indices, not array slots. Previous closed ownership never vetoes an open membership. */
  previous_owner_by_node?:Int32Array;
  /** Source topic indices. Each entry must be an actual active membership of this node. */
  applicable_memberships?:{offsets:Uint32Array;topic_indices:Uint32Array};
}
export interface ProjectionTopicSummary {
  topic_id:string;topic_index:number;source_view_id:string;open:boolean;
  source_member_count:number;visible_source_member_count:number;
  owned_node_count:number;hidden_owned_count:number;canonical_owned_count:number;
  referenced_elsewhere_count:number;visible_elsewhere_count:number;shared_member_count:number;
}
export interface VisibleProjectionMetadata {
  node_primary_topic_indices:Int32Array;
  source_memberships:{offsets:Uint32Array;topic_indices:Uint32Array};
  /** All actual active OPEN memberships, independent of claim-scope applicability. */
  all_open_memberships:{offsets:Uint32Array;topic_indices:Uint32Array};
  applicable_memberships:{offsets:Uint32Array;topic_indices:Uint32Array};
  /** Non-primary memberships are references, never copies of a star or factual edges. */
  shared_references:{topic_indices:Uint32Array;node_ordinals:Uint32Array;representatives:Int32Array;canonical:Uint8Array};
  open_contexts:ProjectionTopicSummary[];topic_summaries:ProjectionTopicSummary[];
  canonical_fallback_ordinals:Uint32Array;
}
export interface VisibleProjection extends LodResult,VisibleProjectionMetadata {}
/** Metadata carries no duplicate geometry or edge ledger. */
export function visibleProjectionMetadata(value:VisibleProjection):VisibleProjectionMetadata{
  const {node_primary_topic_indices,source_memberships,all_open_memberships,applicable_memberships,shared_references,open_contexts,topic_summaries,canonical_fallback_ordinals}=value;
  return{node_primary_topic_indices,source_memberships,all_open_memberships,applicable_memberships,shared_references,open_contexts,topic_summaries,canonical_fallback_ordinals};
}
/** Canonical pinned graph -> visible graph. No camera, selection, Source mutation or layout policy. */
export function projectVisibleGraph(graph:GraphData,index:MembershipIndex,request:VisibleProjectionRequest):VisibleProjection{
  const n=graph.flags.length;if(index.offsets.length!==n+1||graph.positions.length!==n*3||graph.visible.length!==n||graph.visibleEdges.length!==graph.visibleEdgeOrdinals.length*2)throw new Error('Canonical projection membership/position mismatch');
  if(request.previous_owner_by_node&&request.previous_owner_by_node.length!==n)throw new Error('Projection owner context length mismatch');
  const byIndex=new Map(index.topics.map((topic,slot)=>[topic.index,slot])),viewId=(slot:number)=>index.topics[slot]!.source_ref?.view_id||index.topics[slot]!.id;
  const active=new Set<number>();for(const value of request.active_topic_indices){const slot=byIndex.get(value);if(slot===undefined||active.has(slot))throw new Error('Unknown or duplicate active Source topic');active.add(slot);}
  const opened=new Set<number>();for(const value of request.open_topic_indices){const slot=byIndex.get(value);if(slot===undefined||!active.has(slot)||opened.has(slot))throw new Error('Open Source topic must occur once in active frontier');opened.add(slot);}
  const supplied=request.applicable_memberships;if(supplied&&(supplied.offsets.length!==n+1||supplied.offsets[0]!==0||supplied.offsets[n]!==supplied.topic_indices.length))throw new Error('Invalid applicable membership CSR');
  const ordered=[...active].toSorted((a,b)=>viewId(a)<viewId(b)?-1:viewId(a)>viewId(b)?1:0),rank=new Int32Array(index.topics.length).fill(-1);ordered.forEach((slot,i)=>{rank[slot]=i;});
  const representative=new Int32Array(n).fill(-1),canonical=new Float32Array(n),primary=new Int32Array(n).fill(-1),visibleCounts=new Uint32Array(index.topics.length),owned=new Uint32Array(index.topics.length),hiddenOwned=new Uint32Array(index.topics.length),canonicalOwned=new Uint32Array(index.topics.length),sharedCounts=new Uint32Array(index.topics.length),elsewhere=new Uint32Array(index.topics.length),visibleElsewhere=new Uint32Array(index.topics.length),fullMemberships=new Uint32Array(index.memberships.length),openOffsets=new Uint32Array(n+1),openTopics:number[]=[],fallback:number[]=[],applicableOffsets=new Uint32Array(n+1),applicableTopics:number[]=[];
  const nodeMembers:(number[])[]=Array.from({length:n},()=>[]);let canonicalCount=0,representedCount=0;
  for(let node=0;node<n;node++){
    const members=nodeMembers[node]!;for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++){const slot=index.memberships[at]!;fullMemberships[at]=index.topics[slot]!.index;if(active.has(slot))members.push(slot);if(graph.visible[node])visibleCounts[slot]=visibleCounts[slot]!+1;}
    members.sort((a,b)=>rank[a]!-rank[b]!);const open=members.filter(slot=>opened.has(slot));openOffsets[node]=openTopics.length;for(const slot of open)openTopics.push(index.topics[slot]!.index);
    let applicable=members;if(supplied){const start=supplied.offsets[node]!,end=supplied.offsets[node+1]!;if(start>end||end>supplied.topic_indices.length)throw new Error('Invalid applicable membership CSR range');const seen=new Set<number>();applicable=[];for(let at=start;at<end;at++){const value=supplied.topic_indices[at]!,slot=byIndex.get(value);if(slot===undefined||!active.has(slot)||!members.includes(slot)||seen.has(slot))throw new Error('Applicable membership must be a unique actual active Source membership');seen.add(slot);applicable.push(slot);}applicable.sort((a,b)=>rank[a]!-rank[b]!);}
    applicableOffsets[node]=applicableTopics.length;for(const slot of applicable)applicableTopics.push(index.topics[slot]!.index);const applicableOpen=applicable.filter(slot=>opened.has(slot));
    if(!graph.visible[node])continue;
    const previous=byIndex.get(request.previous_owner_by_node?.[node]??-1),preferred=byIndex.get(request.preferred_open_topic_index??-1);
    const owner=applicableOpen.length?(previous!==undefined&&applicableOpen.includes(previous)?previous:preferred!==undefined&&applicableOpen.includes(preferred)?preferred:applicableOpen[0]!):(previous!==undefined&&applicable.includes(previous)?previous:applicable[0]??-1);
    if(owner>=0){primary[node]=index.topics[owner]!.index;owned[owner]=owned[owner]!+1;}
    if(applicableOpen.length||owner<0){representative[node]=node;canonical[node]=1;canonicalCount++;if(owner>=0)canonicalOwned[owner]=canonicalOwned[owner]!+1;else fallback.push(node);}else{hiddenOwned[owner]=hiddenOwned[owner]!+1;representedCount++;}
    const sourceMembershipCount=index.offsets[node+1]!-index.offsets[node]!;for(const slot of members){if(sourceMembershipCount>1)sharedCounts[slot]=sharedCounts[slot]!+1;if(slot!==owner){elsewhere[slot]=elsewhere[slot]!+1;if(canonical[node])visibleElsewhere[slot]=visibleElsewhere[slot]!+1;}}
  }
  openOffsets[n]=openTopics.length;applicableOffsets[n]=applicableTopics.length;
  const closed=ordered.filter(slot=>!opened.has(slot)),proxyBySlot=new Int32Array(index.topics.length).fill(-1),proxies:TopicProxy[]=closed.map((slot,proxy)=>{proxyBySlot[slot]=proxy;const topic=index.topics[slot]!;return{kind:'topic',topic_id:topic.id,topic_index:topic.index,position:[topic.position[0]!,topic.position[1]!,topic.position[2]!],radius:topic.radius,reveal:0,represented_count:hiddenOwned[slot]!,source_member_count:topic.member_count,visible_source_member_count:visibleCounts[slot]!,internal_edge_count:0,generation:request.generation};});
  for(let node=0;node<n;node++)if(graph.visible[node]&&!canonical[node]){const slot=byIndex.get(primary[node]!)!;if(proxyBySlot[slot]!<0)throw new Error('Hidden canonical claim has no closed Source representative');representative[node]=n+proxyBySlot[slot]!;}
  const summary=(slot:number):ProjectionTopicSummary=>{const topic=index.topics[slot]!;return{topic_id:topic.id,topic_index:topic.index,source_view_id:viewId(slot),open:opened.has(slot),source_member_count:topic.member_count,visible_source_member_count:visibleCounts[slot]!,owned_node_count:owned[slot]!,hidden_owned_count:hiddenOwned[slot]!,canonical_owned_count:canonicalOwned[slot]!,referenced_elsewhere_count:elsewhere[slot]!,visible_elsewhere_count:visibleElsewhere[slot]!,shared_member_count:sharedCounts[slot]!};};
  const refTopics:number[]=[],refNodes:number[]=[],refReps:number[]=[],refCanonical:number[]=[];for(let node=0;node<n;node++)if(graph.visible[node])for(const slot of nodeMembers[node]!)if(index.topics[slot]!.index!==primary[node]){refTopics.push(index.topics[slot]!.index);refNodes.push(node);refReps.push(representative[node]!);refCanonical.push(canonical[node]!);}
  const lodRequest:LodRequest={revision:request.revision,generation:request.generation,collapsed_topic_indices:new Uint32Array(closed.map(slot=>index.topics[slot]!.index)),force_reveal_ordinals:new Uint32Array()},base=finalizeProjection(graph,lodRequest,representative,canonical,proxies,canonicalCount,representedCount),summaries=ordered.map(summary);
  return{...base,render_positions:base.render_positions.slice(),render_edges:base.render_edges.slice(),edge_pick_target:base.edge_pick_target.slice(),frontier_topic_indices:new Uint32Array(ordered.map(slot=>index.topics[slot]!.index)),node_primary_topic_indices:primary,source_memberships:{offsets:index.offsets.slice(),topic_indices:fullMemberships},all_open_memberships:{offsets:openOffsets,topic_indices:new Uint32Array(openTopics)},applicable_memberships:{offsets:applicableOffsets,topic_indices:new Uint32Array(applicableTopics)},shared_references:{topic_indices:new Uint32Array(refTopics),node_ordinals:new Uint32Array(refNodes),representatives:new Int32Array(refReps),canonical:new Uint8Array(refCanonical)},open_contexts:summaries.filter(topic=>topic.open),topic_summaries:summaries,canonical_fallback_ordinals:new Uint32Array(fallback)};
}
