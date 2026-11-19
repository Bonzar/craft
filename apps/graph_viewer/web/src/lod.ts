import type { GraphData } from './binary.ts';
import { sourceHierarchy } from './logic.ts';
export interface LodTopic {id:string;index:number;position:readonly number[];radius:number;depth:number;member_count:number}
interface Membership {topic_index:number;node_indices:readonly number[]}
interface Link {parent:string|number;child:string|number;relation:string}
export interface MembershipIndex {topics:readonly LodTopic[];offsets:Uint32Array;memberships:Uint32Array;depths:Uint32Array;parents:readonly number[][];children:readonly number[][]}
export interface LodRequest {revision:string;generation:number;collapsed_topic_indices:Uint32Array;force_reveal_ordinals:Uint32Array}
export interface TopicProxy {topic_id:string;topic_index:number;position:[number,number,number];represented_count:number;source_member_count:number;visible_source_member_count:number;internal_edge_count:number;generation:number}
export interface LodCoverage {visible_nodes:number;canonical_nodes:number;represented_nodes:number;visible_edges:number;original_edges:number;bundled_edges:number;internal_edges:number}
export interface LodResult {revision:string;generation:number;collapsed_topic_indices:Uint32Array;force_reveal_ordinals:Uint32Array;representative_by_node:Int32Array;node_render_visible:Float32Array;proxies:TopicProxy[];render_positions:Float32Array;render_edges:Uint32Array;edge_pick_kind:Uint8Array;edge_pick_target:Uint32Array;bundle_pairs:Uint32Array;bundle_offsets:Uint32Array;bundle_edge_ordinals:Uint32Array;proxy_internal_offsets:Uint32Array;proxy_internal_edge_ordinals:Uint32Array;coverage:LodCoverage}
export function buildMembershipIndex(nodeCount:number,topics:readonly LodTopic[],memberships:readonly Membership[],links:readonly Link[]):MembershipIndex{
  const ordered=topics.toSorted((a,b)=>a.id<b.id?-1:a.id>b.id?1:0);
  const topicSlots=new Map(ordered.map((topic,index)=>[topic.index,index]));
  const byId=new Map(ordered.map((topic,index)=>[topic.id,index]));
  const parents:number[][]=ordered.map(()=>[]);const children:number[][]=ordered.map(()=>[]);
  for(const edge of sourceHierarchy(ordered,links)){const parent=byId.get(edge.parent);const child=byId.get(edge.child);if(parent===undefined||child===undefined)continue;parents[child]?.push(parent);children[parent]?.push(child);}
  const depths=new Uint32Array(ordered.length);const incoming=new Uint32Array(parents.map(values=>values.length));const queue:number[]=[];
  for(let topic=0;topic<incoming.length;topic++)if(incoming[topic]===0)queue.push(topic);
  for(let cursor=0;cursor<queue.length;cursor++){const parent=queue[cursor];if(parent===undefined)continue;for(const child of children[parent]??[]){depths[child]=Math.max(depths[child]??0,(depths[parent]??0)+1);incoming[child]=(incoming[child]??0)-1;if(incoming[child]===0)queue.push(child);}}
  if(queue.length!==ordered.length){depths.fill(0);for(const values of parents)values.length=0;for(const values of children)values.length=0;}
  const counts=new Uint32Array(nodeCount);const clean:{slot:number;nodes:Uint32Array}[]=[];const unions=new Map<number,Set<number>>();
  for(const membership of memberships){
    const slot=topicSlots.get(membership.topic_index);if(slot===undefined)throw new Error('Unknown source membership topic');
    let nodes=unions.get(slot);if(!nodes){nodes=new Set<number>();unions.set(slot,nodes);}
    for(const node of membership.node_indices){if(!Number.isSafeInteger(node)||node<0||node>=nodeCount||node>0xffffffff)throw new Error('Invalid source membership node');nodes.add(node);}
  }
  for(const [slot,unique] of unions){const nodes=new Uint32Array(unique);for(const node of nodes)counts[node]=(counts[node]??0)+1;clean.push({slot,nodes});}
  const offsets=new Uint32Array(nodeCount+1);for(let node=0;node<nodeCount;node++)offsets[node+1]=(offsets[node]??0)+(counts[node]??0);
  const membershipSlots=new Uint32Array(offsets[nodeCount]??0);const cursors=offsets.slice(0,nodeCount);
  for(const membership of clean)for(const node of membership.nodes){const at=cursors[node]??0;membershipSlots[at]=membership.slot;cursors[node]=at+1;}
  return {topics:ordered,offsets,memberships:membershipSlots,depths,parents,children};
}
function prefix(counts:readonly number[]|Uint32Array):Uint32Array{const result=new Uint32Array(counts.length+1);for(let i=0;i<counts.length;i++)result[i+1]=(result[i]??0)+(counts[i]??0);return result;}
export function projectLod(graph:GraphData,index:MembershipIndex,request:LodRequest):LodResult{
  const nodes=graph.flags.length;const sourceEdges=graph.visibleEdgeOrdinals.length;
  if(index.offsets.length!==nodes+1)throw new Error('LOD membership revision mismatch');
  const collapsed=new Set(request.collapsed_topic_indices);const forced=new Uint8Array(nodes);for(const ordinal of request.force_reveal_ordinals)if(ordinal<nodes)forced[ordinal]=1;
  const owned=new Uint32Array(index.topics.length);const visibleMemberships=new Uint32Array(index.topics.length);const owners=new Int32Array(nodes).fill(-1);
  const representative=new Int32Array(nodes).fill(-1);const visible=new Float32Array(nodes);let canonical=0;let represented=0;
  for(let node=0;node<nodes;node++){
    if(!graph.visible[node])continue;let owner=-1;
    for(let at=index.offsets[node]??0;at<(index.offsets[node+1]??0);at++){
      const slot=index.memberships[at];if(slot===undefined)continue;visibleMemberships[slot]=(visibleMemberships[slot]??0)+1;
      const topic=index.topics[slot];if(forced[node]||!topic||!collapsed.has(topic.index))continue;
      if(owner<0||(index.depths[slot]??0)>(index.depths[owner]??0)||((index.depths[slot]??0)===(index.depths[owner]??0)&&slot<owner))owner=slot;
    }
    if(owner>=0){owners[node]=owner;owned[owner]=(owned[owner]??0)+1;represented++;}
    else{representative[node]=node;visible[node]=1;canonical++;}
  }
  const proxyForTopic=new Int32Array(index.topics.length).fill(-1);const proxies:TopicProxy[]=[];
  for(let slot=0;slot<index.topics.length;slot++){const topic=index.topics[slot];const count=owned[slot]??0;if(!topic||!count)continue;proxyForTopic[slot]=proxies.length;proxies.push({topic_id:topic.id,topic_index:topic.index,position:[topic.position[0]??0,topic.position[1]??0,topic.position[2]??0],represented_count:count,source_member_count:topic.member_count,visible_source_member_count:visibleMemberships[slot]??0,internal_edge_count:0,generation:request.generation});}
  for(let node=0;node<nodes;node++){const owner=owners[node]??-1;if(owner>=0)representative[node]=nodes+(proxyForTopic[owner]??0);}
  const positions=proxies.length?new Float32Array((nodes+proxies.length)*3):graph.positions;
  if(proxies.length){positions.set(graph.positions);proxies.forEach((proxy,i)=>positions.set(proxy.position,(nodes+i)*3));}
  const originalEndpoints=new Uint32Array(graph.visibleEdges.length);const originalOrdinals=new Uint32Array(sourceEdges);const classification=new Int32Array(sourceEdges).fill(-1);
  const bundleMap=new Map<number,number>();const bundlePairs:number[]=[];const bundleCounts:number[]=[];const internalCounts=new Uint32Array(proxies.length);
  const representativeCount=nodes+proxies.length;let originals=0;let bundled=0;let internal=0;
  for(let edge=0;edge<sourceEdges;edge++){
    const from=representative[graph.visibleEdges[edge*2]??nodes]??-1;const to=representative[graph.visibleEdges[edge*2+1]??nodes]??-1;
    if(from<0||to<0)throw new Error('LOD encountered a hidden edge endpoint');
    if(from<nodes&&to<nodes){originalEndpoints[originals*2]=from;originalEndpoints[originals*2+1]=to;originalOrdinals[originals++]=graph.visibleEdgeOrdinals[edge]??0;}
    else if(from===to){const proxy=from-nodes;classification[edge]=-proxy-2;internalCounts[proxy]=(internalCounts[proxy]??0)+1;internal++;}
    else{const key=from*representativeCount+to;let bundle=bundleMap.get(key);if(bundle===undefined){bundle=bundleCounts.length;bundleMap.set(key,bundle);bundlePairs.push(from,to);bundleCounts.push(0);}bundleCounts[bundle]=(bundleCounts[bundle]??0)+1;classification[edge]=bundle;bundled++;}
  }
  const offsets=prefix(bundleCounts);const bundleEdges=new Uint32Array(bundled);const bundleCursors=offsets.slice(0,-1);
  const internalOffsets=prefix(internalCounts);const internalEdges=new Uint32Array(internal);const internalCursors=internalOffsets.slice(0,-1);
  for(let edge=0;edge<sourceEdges;edge++){const category=classification[edge]??-1;const ordinal=graph.visibleEdgeOrdinals[edge]??0;if(category>=0){const at=bundleCursors[category]??0;bundleEdges[at]=ordinal;bundleCursors[category]=at+1;}else if(category<=-2){const proxy=-category-2;const at=internalCursors[proxy]??0;internalEdges[at]=ordinal;internalCursors[proxy]=at+1;}}
  proxies.forEach((proxy,i)=>{proxy.internal_edge_count=internalCounts[i]??0;});
  const renderCount=originals+bundleCounts.length;const renderEdges=proxies.length?new Uint32Array(renderCount*2):graph.visibleEdges;
  const pickKind=new Uint8Array(renderCount);const pickTarget=proxies.length?new Uint32Array(renderCount):graph.visibleEdgeOrdinals;
  if(proxies.length){renderEdges.set(originalEndpoints.subarray(0,originals*2));renderEdges.set(bundlePairs,originals*2);pickTarget.set(originalOrdinals.subarray(0,originals));for(let bundle=0;bundle<bundleCounts.length;bundle++){pickKind[originals+bundle]=1;pickTarget[originals+bundle]=bundle;}}
  return {revision:request.revision,generation:request.generation,collapsed_topic_indices:request.collapsed_topic_indices,force_reveal_ordinals:request.force_reveal_ordinals,representative_by_node:representative,node_render_visible:visible,proxies,render_positions:positions,render_edges:renderEdges,edge_pick_kind:pickKind,edge_pick_target:pickTarget,bundle_pairs:new Uint32Array(bundlePairs),bundle_offsets:offsets,bundle_edge_ordinals:bundleEdges,proxy_internal_offsets:internalOffsets,proxy_internal_edge_ordinals:internalEdges,coverage:{visible_nodes:canonical+represented,canonical_nodes:canonical,represented_nodes:represented,visible_edges:sourceEdges,original_edges:originals,bundled_edges:bundled,internal_edges:internal}};
}
export function nextCollapsedTopics(collapsed:boolean,diameter:number,threshold:number):boolean{return collapsed?diameter<=threshold*1.35:diameter<threshold;}
export function acceptsLod(revision:string,generation:number,result:{revision:string;generation:number}):boolean{return result.revision===revision&&result.generation===generation;}
