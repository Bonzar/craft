import {PresentationCellCache} from './presentation-cells.ts';
import type {PresentationCell} from './presentation-cells.ts';
import {frustumPlanes,projectPoint,visitSpatial,intersects} from './spatial.ts';
import type {SpatialIndex} from './spatial.ts';
import type { GraphData } from './binary.ts';
import { sourceHierarchy } from './logic.ts';
import {finalizeProjection as finalizeLod} from './projection-ledger.ts';
import {projectVisibleGraph,visibleProjectionMetadata} from './visible-projection.ts';
import type {VisibleProjectionMetadata} from './visible-projection.ts';
import {cameraClaimScopes} from './camera-claim-scopes.ts';
import {buildVisualContainment} from './view-containment.ts';
import {buildSourceViewCover} from './source-view-cover.ts';
import {projectedSphereSpan,sphereRevealPhase} from './sphere-group-reveal.ts';
export interface LodSourceViewRef {view_id:string;version_id?:number;route_revision?:number|null}
export interface SessionResultPin {session_id:string;version_id:number;receipt_id:string}
export interface LodTopic {session_result?:SessionResultPin|null;session_filter_applied?:boolean;id:string;index:number;position:readonly number[];radius:number;depth:number;member_count:number;navigation_membership?:'selected_registry'|'historical_registry'|null;purpose?:string|null;source_ref?:LodSourceViewRef}
/** Adapter-attested receipt; session equality is checked at the manifest load boundary. */
export function verifiedSessionResult(topic:LodTopic):boolean{return topic.session_filter_applied===true&&!!topic.session_result?.session_id.trim()&&!!topic.session_result.receipt_id.trim()&&Number.isSafeInteger(topic.session_result.version_id)&&topic.session_result.version_id>0&&topic.session_result.version_id===topic.source_ref?.version_id;}
export function sourceViewId(topic:LodTopic):string{return topic.source_ref?.view_id||topic.id;}
/** Stable Source view identity; snapshot-only topics never impersonate a ViewSpec. */
export function sourceViewIdentity(topic:LodTopic):string{return JSON.stringify(topic.source_ref?.view_id?verifiedSessionResult(topic)?['view-version',topic.source_ref.view_id,topic.source_ref.version_id]:['view',topic.source_ref.view_id]:['snapshot-topic',topic.id]);}
/** The pinned Source snapshot chooses versions. All delivered nonempty views
 * share one visual class; purpose, history and review evidence are card data. */
export function eligibleSourceViewSlots(topics:readonly LodTopic[],visibleTopicIndices:ReadonlySet<number>):number[]{
  const seen=new Set<string>();for(const topic of topics){const key=sourceViewIdentity(topic);if(seen.has(key))throw new Error('Source snapshot contains duplicate ViewID entries');seen.add(key);}return topics.map((_,slot)=>slot).filter(slot=>visibleTopicIndices.has(topics[slot]!.index)).toSorted((a,b)=>{const left=sourceViewId(topics[a]!),right=sourceViewId(topics[b]!);return left<right?-1:left>right?1:0;});
}
interface Membership {topic_index:number;node_indices:readonly number[]}
interface Link {parent:string|number;child:string|number;relation:string}
export interface MembershipIndex {topics:readonly LodTopic[];offsets:Uint32Array;memberships:Uint32Array;depths:Uint32Array;parents:readonly number[][];children:readonly number[][]}
export interface LodView {camera_generation:number;view_projection:Float32Array;viewport:readonly[number,number];glyph_diameter:number;picking_diameter:number;blocked_rects?:readonly (readonly[number,number,number,number])[]}
export interface LodNavigation {epoch:number;topic_path:readonly number[];expanded_cell_ids:readonly string[]}
export interface CellProxy {kind:'cell';cell_id:string;parent_cell_id:string|null;scope_topic_index:number|null;position:[number,number,number];bounds:{min:[number,number,number];max:[number,number,number]};represented_count:number;internal_edge_count:number;generation:number;child_ids:readonly string[];coincident:boolean}
export interface CellMembersRequest {revision:string;lod_generation:number;cell_id:string;offset:number;limit:number}
export interface CellMembersResult extends CellMembersRequest {ordinals:Uint32Array;total:number;next_offset:number|null}
export interface LodSpatial {split_px:number;merge_px:number;assertion_radius?:number;min_glyph_px?:number;merge_glyph_px?:number}
function validSpatial(config:LodSpatial):boolean{const gate=config.assertion_radius!==undefined||config.min_glyph_px!==undefined||config.merge_glyph_px!==undefined;return Number.isFinite(config.split_px)&&Number.isFinite(config.merge_px)&&config.merge_px>0&&config.split_px>config.merge_px&&(!gate||Number.isFinite(config.assertion_radius)&&config.assertion_radius!>0&&Number.isFinite(config.min_glyph_px)&&Number.isFinite(config.merge_glyph_px)&&config.merge_glyph_px!>0&&config.min_glyph_px!>config.merge_glyph_px!);}
function sameSpatial(a:LodSpatial,b:LodSpatial):boolean{return(['split_px','merge_px','assertion_radius','min_glyph_px','merge_glyph_px'] as const).every(key=>a[key]===b[key]);}
export interface LodMap extends LodSpatial {assertion_radius:number;min_glyph_px:number;merge_glyph_px:number}
export interface LodMapState extends LodMap {projection?:VisibleProjectionMetadata;context_topic_indices?:Uint32Array;opened_topic_indices:Uint32Array;reachable_topic_indices?:Uint32Array;all_view_count?:number;cover_topic_indices?:Uint32Array;cover_mapped_count?:number;cover_unassigned_count?:number;shown_topic_indices?:Uint32Array;projection_root_by_node?:Int32Array;projection_owner_by_node?:Int32Array;current_frontier_topic_indices?:Uint32Array;group_reveals?:readonly {topic_index:number;span:number;reveal:number;nested:boolean;position?:readonly number[];radius?:number;projection_members?:number;projection_owned?:number;context_opacity?:number}[];node_reveal?:Float32Array;group_owner_by_node?:Int32Array;node_gate_offsets?:Uint32Array;node_gate_topic_indices?:Uint32Array;visual_containment?:readonly {primary_topic_index:number;parent_topic_indices?:Uint32Array;alias_topic_indices:Uint32Array;alias_view_ids:readonly string[]}[]}
export interface LodSpatialState extends LodSpatial {expanded_cell_ids:readonly string[]}
export interface LodRequest {map_lod?:LodMap;spatial_lod?:LodSpatial;navigation?:LodNavigation;revision:string;generation:number;collapsed_topic_indices:Uint32Array;expanded_topic_indices?:Uint32Array;force_reveal_ordinals:Uint32Array;partial_topic_indices?:Uint32Array;previous_reveal_ordinals?:Uint32Array;view?:LodView}
export interface TopicProxy {reveal?:number;nested?:boolean;radius?:number;kind?:'topic';topic_id:string;topic_index:number;position:[number,number,number];represented_count:number;source_member_count:number;visible_source_member_count:number;internal_edge_count:number;generation:number}
export type DisplayProxy = TopicProxy | CellProxy;
/** Compact identity within one accepted Source revision, independent of proxy order. */
export type RepresentativeKey = {kind:'node';ordinal:number}|{kind:'topic';topic_index:number}|{kind:'cell';cell_id:string};
export interface ProxyEdgeCounts {incoming_external:number;outgoing_external:number;internal:number;total_unique:number}

export interface LodCoverage {visible_nodes:number;canonical_nodes:number;represented_nodes:number;visible_edges:number;original_edges:number;bundled_edges:number;internal_edges:number}
export interface LodResult {map_lod?:LodMapState;spatial_lod?:LodSpatialState;navigation?:LodNavigation;frontier_topic_indices?:Uint32Array;partial_topic_indices?:Uint32Array;automatic_reveal_ordinals?:Uint32Array;view?:(LodView&{request_generation:number});revision:string;generation:number;collapsed_topic_indices:Uint32Array;force_reveal_ordinals:Uint32Array;representative_by_node:Int32Array;node_render_visible:Float32Array;proxies:DisplayProxy[];render_positions:Float32Array;render_edges:Uint32Array;edge_pick_kind:Uint8Array;edge_pick_target:Uint32Array;bundle_pairs:Uint32Array;bundle_offsets:Uint32Array;bundle_edge_ordinals:Uint32Array;proxy_internal_offsets:Uint32Array;proxy_internal_edge_ordinals:Uint32Array;coverage:LodCoverage}
/** Resolve only against the accepted cut; Source revision changes rebind Source IDs. */
export function representativeKey(lod:LodResult,representative:number):RepresentativeKey{
  const nodes=lod.representative_by_node.length;
  if(!Number.isSafeInteger(representative)||representative<0)throw new Error('Invalid representative');
  if(representative<nodes)return{kind:'node',ordinal:representative};
  const proxy=lod.proxies[representative-nodes];if(!proxy)throw new Error('Unknown representative');
  return proxy.kind==='cell'?{kind:'cell',cell_id:proxy.cell_id}:{kind:'topic',topic_index:proxy.topic_index};
}
export function representativeKeyToken(key:RepresentativeKey):string{return key.kind==='node'?`node:${key.ordinal}`:key.kind==='topic'?`topic:${key.topic_index}`:`cell:${key.cell_id}`;}
/** Graph totals use CSR weights, never screen visibility or rendered stroke counts. */
export function proxyEdgeCounts(lod:LodResult):ProxyEdgeCounts[]{
  const nodes=lod.representative_by_node.length,counts=lod.proxies.map((_,i)=>({incoming_external:0,outgoing_external:0,internal:(lod.proxy_internal_offsets[i+1]??0)-(lod.proxy_internal_offsets[i]??0),total_unique:0}));
  for(let bundle=0;bundle<lod.bundle_pairs.length/2;bundle++){
    const weight=(lod.bundle_offsets[bundle+1]??0)-(lod.bundle_offsets[bundle]??0),from=lod.bundle_pairs[bundle*2]!-nodes,to=lod.bundle_pairs[bundle*2+1]!-nodes;
    if(from>=0&&counts[from])counts[from]!.outgoing_external+=weight;
    if(to>=0&&counts[to])counts[to]!.incoming_external+=weight;
  }
  for(const count of counts)count.total_unique=count.incoming_external+count.outgoing_external+count.internal;
  return counts;
}
/** Canonical self-relations remain original Source pick targets, including parallel loops. */
export function canonicalSelfLoopCounts(lod:LodResult):Uint32Array{
  const counts=new Uint32Array(lod.representative_by_node.length);
  for(let edge=0;edge<lod.edge_pick_kind.length;edge++)if(lod.edge_pick_kind[edge]===0){const from=lod.render_edges[edge*2]!,to=lod.render_edges[edge*2+1]!;if(from===to&&from<counts.length)counts[from]=(counts[from]??0)+1;}
  return counts;
}
/** Includes coincident distinct canonical endpoints so zero-length Source edges have an affordance. */
export function canonicalZeroLengthEdgeOrdinals(lod:LodResult):Uint32Array{
  const ordinals:number[]=[],nodes=lod.representative_by_node.length;
  for(let edge=0;edge<lod.edge_pick_kind.length;edge++)if(lod.edge_pick_kind[edge]===0){
    const from=lod.render_edges[edge*2]!,to=lod.render_edges[edge*2+1]!;
    if(from<nodes&&to<nodes&&lod.render_positions[from*3]===lod.render_positions[to*3]&&lod.render_positions[from*3+1]===lod.render_positions[to*3+1]&&lod.render_positions[from*3+2]===lod.render_positions[to*3+2])ordinals.push(lod.edge_pick_target[edge]!);
  }
  return new Uint32Array(ordinals);
}
// Exact per-source-topic visible counts, computed once per graph binding.
// Duplicate membership rows are a union; many-to-many source membership stays intact.
export function visibleTopicMemberCounts(memberships:readonly Membership[],visible:ArrayLike<number>):Map<number,number>{
  const members=new Map<number,Set<number>>();
  for(const row of memberships){let union=members.get(row.topic_index);if(!union){union=new Set();members.set(row.topic_index,union);}for(const node of row.node_indices){if(!Number.isSafeInteger(node)||node<0||node>=visible.length)throw new Error('Invalid source membership node');if(visible[node])union.add(node);}}
  return new Map(Array.from(members,([topic,nodes])=>[topic,nodes.size]));
}
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
interface SourceOwners {owners:Int32Array;visibleMemberships:Uint32Array}
function resolveSourceOwners(graph:GraphData,index:MembershipIndex,request:LodRequest):SourceOwners{
  const closed=new Set(request.collapsed_topic_indices),partial=new Set(request.partial_topic_indices??[]);
  for(const topic of partial){if(!index.topics.some(value=>value.index===topic))throw new Error('Unknown partial Source topic');if(closed.has(topic))throw new Error('Source topic cannot be closed and partial');}
  const reached=new Set([...(request.expanded_topic_indices??[]),...partial]);
  const ancestors=index.topics.map(()=>new Set<number>());
  for(let slot=0;slot<index.topics.length;slot++)if(reached.has(index.topics[slot]!.index)){
    const queue=[...(index.parents[slot]??[])],seen=ancestors[slot]!;
    for(const parent of queue){if(seen.has(parent))continue;seen.add(parent);queue.push(...(index.parents[parent]??[]));}
  }
  const owners=new Int32Array(graph.flags.length).fill(-1),visibleMemberships=new Uint32Array(index.topics.length);
  const better=(slot:number,old:number)=>old<0||(index.depths[slot]??0)>(index.depths[old]??0)||((index.depths[slot]??0)===(index.depths[old]??0)&&slot<old);
  for(let node=0;node<owners.length;node++){
    if(!graph.visible[node])continue;let blocker=-1,opened=-1;
    for(let at=index.offsets[node]??0;at<(index.offsets[node+1]??0);at++){
      const slot=index.memberships[at]!;const topic=index.topics[slot]!;visibleMemberships[slot]=(visibleMemberships[slot]??0)+1;
      if(partial.has(topic.index)&&better(slot,opened))opened=slot;
      if(!closed.has(topic.index))continue;
      let released=false;for(let other=index.offsets[node]??0;other<(index.offsets[node+1]??0);other++)if(ancestors[index.memberships[other]??-1]?.has(slot)){released=true;break;}
      if(!released&&better(slot,blocker))blocker=slot;
    }
    // A deeper partial topic never bypasses an unrelated still-closed member.
    owners[node]=blocker>=0?blocker:opened;
  }
  return{owners,visibleMemberships};
}
export function projectLod(graph:GraphData,index:MembershipIndex,request:LodRequest,automatic?:Uint8Array,prepared?:SourceOwners):LodResult{
  const nodes=graph.flags.length;const sourceEdges=graph.visibleEdgeOrdinals.length;
  if(index.offsets.length!==nodes+1)throw new Error('LOD membership revision mismatch');
  const resolved=prepared??resolveSourceOwners(graph,index,request),visibleMemberships=resolved.visibleMemberships;
  const forced=new Uint8Array(nodes);for(const node of request.force_reveal_ordinals)if(node<nodes)forced[node]=1;
  const owned=new Uint32Array(index.topics.length),owners=resolved.owners.slice();
  const representative=new Int32Array(nodes).fill(-1),visible=new Float32Array(nodes);let canonical=0,represented=0;
  for(let node=0;node<nodes;node++){
    if(!graph.visible[node])continue;if(forced[node]||automatic?.[node])owners[node]=-1;
    const owner=owners[node]??-1;
    if(owner>=0){owned[owner]=(owned[owner]??0)+1;represented++;}
    else{representative[node]=node;visible[node]=1;canonical++;}
  }
  const proxyForTopic=new Int32Array(index.topics.length).fill(-1);const proxies:TopicProxy[]=[];
  for(let slot=0;slot<index.topics.length;slot++){const topic=index.topics[slot];const count=owned[slot]??0;if(!topic||!count)continue;proxyForTopic[slot]=proxies.length;proxies.push({topic_id:topic.id,topic_index:topic.index,position:[topic.position[0]??0,topic.position[1]??0,topic.position[2]??0],represented_count:count,source_member_count:topic.member_count,visible_source_member_count:visibleMemberships[slot]??0,internal_edge_count:0,generation:request.generation});}
  for(let node=0;node<nodes;node++){const owner=owners[node]??-1;if(owner>=0)representative[node]=nodes+(proxyForTopic[owner]??0);}
  return finalizeLod(graph,request,representative,visible,proxies,canonical,represented);
}
export function nextCollapsedTopics(collapsed:boolean,diameter:number,threshold:number):boolean{return collapsed?diameter<=threshold*1.35:diameter<threshold;}
export function acceptsLod(revision:string,generation:number,result:{revision:string;generation:number}):boolean{return result.revision===revision&&result.generation===generation;}

export function acceptsLodView(request:LodRequest,result:LodResult):boolean{
  if(request.revision!==result.revision)return false;
  const same=(a:Uint32Array|undefined,b:Uint32Array|undefined)=>Array.from(a??[]).join(',')===Array.from(b??[]).join(',');
  if(navigationKey(request.navigation)!==navigationKey(result.navigation))return false;
  if(!!request.map_lod!==!!result.map_lod||request.map_lod&&!sameSpatial(request.map_lod,result.map_lod!))return false;
  if(!!request.spatial_lod!==!!result.spatial_lod||request.spatial_lod&&!sameSpatial(request.spatial_lod,result.spatial_lod!))return false;
  if(!same(request.partial_topic_indices,result.partial_topic_indices)||!same(request.collapsed_topic_indices,result.collapsed_topic_indices)||!same(request.force_reveal_ordinals,result.force_reveal_ordinals))return false;
  if(!request.view)return result.view===undefined;
  const a=request.view,b=result.view;
  return !!b&&b.request_generation===request.generation&&b.camera_generation===a.camera_generation&&b.glyph_diameter===a.glyph_diameter&&b.picking_diameter===a.picking_diameter&&JSON.stringify(a.blocked_rects??[])===JSON.stringify(b.blocked_rects??[])&&a.viewport.every((v,i)=>v===b.viewport[i])&&a.view_projection.length===16&&b.view_projection.length===16&&a.view_projection.every((v,i)=>v===b.view_projection[i]);
}
/** Physical detail only. Every rejected position retains its existing Source owner. */
export function projectPartialLod(graph:GraphData,index:MembershipIndex,pointIndex:SpatialIndex,request:LodRequest,previous?:LodResult|null):LodResult{
  if(index.offsets.length!==graph.flags.length+1||pointIndex.size!==graph.flags.length)throw new Error('Partial spatial revision mismatch');
  const resolved=resolveSourceOwners(graph,index,request),partial=new Set(request.partial_topic_indices??[]),forced=new Set(request.force_reveal_ordinals),view=request.view,n=graph.flags.length,automatic=new Uint8Array(n),admitted:number[]=[];
  if(view){
    const [width,height]=view.viewport,diameter=Math.max(view.glyph_diameter,view.picking_diameter),cell=Math.max(36,diameter);
    if(!Number.isSafeInteger(view.camera_generation)||view.camera_generation<0||view.view_projection.length!==16||!view.view_projection.every(Number.isFinite)||![width,height,diameter].every(v=>Number.isFinite(v)&&v>0))throw new Error('Invalid partial Source view');
    const bins=new Map<string,{x:number;y:number;r:number}[]>(),key=(x:number,y:number)=>`${x},${y}`;
    const add=(p:{x:number;y:number},r:number)=>{const k=key(Math.floor(p.x/cell),Math.floor(p.y/cell)),values=bins.get(k)??[];values.push({...p,r});bins.set(k,values);};
    const clear=(p:{x:number;y:number},r:number)=>{const x=Math.floor(p.x/cell),y=Math.floor(p.y/cell);for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++)for(const q of bins.get(key(x+dx,y+dy))??[])if((p.x-q.x)**2+(p.y-q.y)**2<(r+q.r)**2)return false;return true;};
    const project=(position:ArrayLike<number>)=>projectPoint(view.view_projection,position[0]??0,position[1]??0,position[2]??0,width,height);
    const depthVisible=(position:ArrayLike<number>)=>{const m=view.view_projection,x=position[0]??0,y=position[1]??0,z=position[2]??0,w=(m[3]??0)*x+(m[7]??0)*y+(m[11]??0)*z+(m[15]??0),depth=(m[2]??0)*x+(m[6]??0)*y+(m[10]??0)*z+(m[14]??0);return w>0&&depth>=-w&&depth<=w;};
    const candidates:{ordinal:number;x:number;y:number;depth:number}[]=[],owned=new Uint32Array(index.topics.length);
    for(let node=0;node<n;node++)if(graph.visible[node]&&!forced.has(node)&&(resolved.owners[node]??-1)>=0)owned[resolved.owners[node]!]=(owned[resolved.owners[node]!]??0)+1;
    // Proxy rings keep their real source position and maximum existing36px footprint.
    for(let slot=0;slot<index.topics.length;slot++)if(owned[slot]){const position=index.topics[slot]!.position,p=project(position);if(depthVisible(position)&&p.x>=-18&&p.x<=width+18&&p.y>=-18&&p.y<=height+18)add(p,18);}
    visitSpatial(pointIndex,frustumPlanes(view.view_projection,-1-cell*2/width,1+cell*2/width,-1-cell*2/height,1+cell*2/height),ordinal=>{
      if(!graph.visible[ordinal])return;const position=graph.positions.subarray(ordinal*3,ordinal*3+3),p=project(position),owner=resolved.owners[ordinal]??-1;
      if(owner<0||forced.has(ordinal)){if(depthVisible(position)&&p.x>=-diameter/2&&p.x<=width+diameter/2&&p.y>=-diameter/2&&p.y<=height+diameter/2)add(p,diameter/2);return;}
      if(view.blocked_rects?.some(r=>p.x+diameter/2>r[0]&&p.x-diameter/2<r[2]&&p.y+diameter/2>r[1]&&p.y-diameter/2<r[3]))return;
      if(!partial.has(index.topics[owner]!.index)||!p.visible||p.x<diameter/2||p.x>width-diameter/2||p.y<diameter/2||p.y>height-diameter/2)return;
      candidates.push({ordinal,...p});
    },{branches:0,points:0});
    const eligible=new Map(candidates.map(p=>[p.ordinal,p]));
    const admit=(p:{ordinal:number;x:number;y:number})=>{if(!clear(p,diameter/2))return;automatic[p.ordinal]=1;admitted.push(p.ordinal);add(p,diameter/2);};
    // Sticky inputs come from the last ACCEPTED representation, never a stale reply.
    if(previous?.revision===request.revision)for(const ordinal of Array.from(request.previous_reveal_ordinals??previous.automatic_reveal_ordinals??[]).toSorted((a,b)=>a-b)){const p=eligible.get(ordinal);if(p)admit(p);}
    candidates.sort((a,b)=>a.depth-b.depth||(graph.positions[a.ordinal*3]??0)-(graph.positions[b.ordinal*3]??0)||(graph.positions[a.ordinal*3+1]??0)-(graph.positions[b.ordinal*3+1]??0)||(graph.positions[a.ordinal*3+2]??0)-(graph.positions[b.ordinal*3+2]??0)||a.ordinal-b.ordinal);
    for(const p of candidates)if(!automatic[p.ordinal])admit(p);
  }
  const binding=view?{...view,view_projection:view.view_projection.slice(),viewport:[...view.viewport] as[number,number],blocked_rects:view.blocked_rects?.map(r=>[...r] as[number,number,number,number]),request_generation:request.generation}:undefined;
  let same=previous?.revision===request.revision&&previous.representative_by_node.length===n;
  if(same&&previous)for(let node=0;node<n;node++){
    const old=previous.representative_by_node[node]??-1,owner=resolved.owners[node]??-1;
    if(!graph.visible[node]){if(old!==-1){same=false;break;}}
    else if(owner<0||forced.has(node)||automatic[node]){if(old!==node){same=false;break;}}
    else if(old<n||(previous.proxies[old-n]?.kind==='cell'||(previous.proxies[old-n] as TopicProxy|undefined)?.topic_index!==index.topics[owner]?.index)){same=false;break;}
  }
  const fields={...(previous?.map_lod?{map_lod:undefined}:{}),...(previous?.spatial_lod?{spatial_lod:undefined}:{}),navigation:undefined,frontier_topic_indices:undefined,partial_topic_indices:new Uint32Array(request.partial_topic_indices??[]),automatic_reveal_ordinals:new Uint32Array(admitted.toSorted((a,b)=>a-b)),view:binding};
  if(same&&previous)return{...previous,collapsed_topic_indices:request.collapsed_topic_indices,force_reveal_ordinals:request.force_reveal_ordinals,...fields};
  return{...projectLod(graph,index,request,automatic,resolved),...fields};
}

/** Camera-only spatial cut. Source themes never define these physical bodies. */
export function projectSpatialLod(graph:GraphData,request:LodRequest,cache=new PresentationCellCache(),previous?:LodResult|null):LodResult{
  const config=request.spatial_lod,view=request.view,n=graph.flags.length;
  if(!config||!view||!validSpatial(config))throw new Error('Invalid spatial LOD thresholds');
  if(request.navigation||request.collapsed_topic_indices.length||request.force_reveal_ordinals.length||request.partial_topic_indices?.length||request.expanded_topic_indices?.length)throw new Error('Spatial LOD requires camera-only cut');
  if(!Number.isSafeInteger(view.camera_generation)||view.camera_generation<0||view.view_projection.length!==16||!view.view_projection.every(Number.isFinite)||![...view.viewport,view.glyph_diameter,view.picking_diameter].every(v=>Number.isFinite(v)&&v>0))throw new Error('Invalid spatial LOD view');
  const members=Uint32Array.from({length:n},(_,ordinal)=>ordinal).filter(ordinal=>!!graph.visible[ordinal]),root=cache.tree(graph.positions,members,`spatial:${encodeURIComponent(request.revision)}`),planes=frustumPlanes(view.view_projection);
  const prior=previous?.revision===request.revision&&previous.spatial_lod?previous:null,expandedBefore=new Set(prior?.spatial_lod?.expanded_cell_ids??[]),expanded:string[]=[],automatic:number[]=[],proxies:DisplayProxy[]=[],representative=new Int32Array(n).fill(-1),visible=new Float32Array(n);
  let represented=0;
  // Project the actual eight 3D bounds corners. CSS diameter is a local camera
  // error estimate, not distance to the controls target or a semantic radius.
  const diameter=(cell:PresentationCell):number=>{
    let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;const m=view.view_projection;
    for(let corner=0;corner<8;corner++){
      const x=corner&1?cell.bounds.max[0]:cell.bounds.min[0],y=corner&2?cell.bounds.max[1]:cell.bounds.min[1],z=corner&4?cell.bounds.max[2]:cell.bounds.min[2];
      const w=m[3]!*x+m[7]!*y+m[11]!*z+m[15]!,depth=m[2]!*x+m[6]!*y+m[10]!*z+m[14]!;
      // A visible bound crossing the camera/near plane is conservatively split.
      if(w<=0||depth< -w)return Infinity;
      const p=projectPoint(m,x,y,z,view.viewport[0],view.viewport[1]);xmin=Math.min(xmin,p.x);xmax=Math.max(xmax,p.x);ymin=Math.min(ymin,p.y);ymax=Math.max(ymax,p.y);
    }
    return Math.max(xmax-xmin,ymax-ymin);
  };
  const admit=(node:number)=>{representative[node]=node;visible[node]=1;automatic.push(node);};
  // The renderer and worker share ONE presentation radius in world units.
  // Perspective's clip-w is positive forward depth; behind-camera members do
  // not prevent an otherwise visible leaf from ever becoming readable.
  const readable=(cell:PresentationCell):boolean=>{if(config.assertion_radius===undefined)return true;let depth=0;const m=view.view_projection;for(const node of cell.members){const at=node*3,w=m[3]!*graph.positions[at]!+m[7]!*graph.positions[at+1]!+m[11]!*graph.positions[at+2]!+m[15]!;if(w>0)depth=Math.max(depth,w);}if(!depth)return false;const diameter=config.assertion_radius*view.viewport[1]*Math.hypot(m[1]!,m[5]!,m[9]!)/depth;return diameter>=(expandedBefore.has(cell.id)?config.merge_glyph_px!:config.min_glyph_px!);};
  const walk=(cell:PresentationCell):void=>{
    if(cell.members.length===1&&(config.assertion_radius===undefined||intersects(cell.bounds,planes))&&readable(cell)){if(config.assertion_radius!==undefined)expanded.push(cell.id);admit(cell.members[0]!);return;}
    const split=cell.members.length>1&&!cell.coincident&&intersects(cell.bounds,planes)&&diameter(cell)>(expandedBefore.has(cell.id)?config.merge_px:config.split_px)&&(cell.children.length>0||readable(cell));
    if(split){expanded.push(cell.id);if(cell.children.length)for(const child of cell.children)walk(child);else for(const node of cell.members)admit(node);return;}
    const proxy=proxies.length;proxies.push({kind:'cell',cell_id:cell.id,parent_cell_id:cell.parent,scope_topic_index:null,position:cell.position,bounds:cell.bounds,represented_count:cell.members.length,internal_edge_count:0,generation:request.generation,child_ids:cell.children.map(child=>child.id),coincident:cell.coincident});
    for(const node of cell.members)representative[node]=n+proxy;represented+=cell.members.length;
  };
  if(root)walk(root);
  const unchanged=prior&&prior.representative_by_node.length===n&&prior.representative_by_node.every((value,node)=>value===representative[node])&&prior.proxies.length===proxies.length&&prior.proxies.every((proxy,i)=>proxy.kind==='cell'&&proxies[i]?.kind==='cell'&&proxy.cell_id===(proxies[i] as CellProxy).cell_id);
  const result=unchanged?prior:finalizeLod(graph,request,representative,visible,proxies,automatic.length,represented);
  return{...result,spatial_lod:{...config,expanded_cell_ids:expanded},navigation:undefined,frontier_topic_indices:new Uint32Array(),collapsed_topic_indices:request.collapsed_topic_indices,force_reveal_ordinals:request.force_reveal_ordinals,partial_topic_indices:undefined,automatic_reveal_ordinals:new Uint32Array(automatic.toSorted((a,b)=>a-b)),view:{...view,view_projection:view.view_projection.slice(),viewport:[...view.viewport],blocked_rects:view.blocked_rects?.map(rect=>[...rect]),request_generation:request.generation}};
}

/** Public camera aggregates are distinct pinned Source views, never spatial cells. */
export function projectCameraMapLod(graph:GraphData,index:MembershipIndex,request:LodRequest,previous?:LodResult|null):LodResult{
  const config=request.map_lod,view=request.view,n=graph.flags.length;
  if(!config||!validSpatial(config)||config.assertion_radius===undefined)throw new Error('Invalid map LOD thresholds');
  if(index.offsets.length!==n+1||request.navigation||request.spatial_lod||request.collapsed_topic_indices.length||request.force_reveal_ordinals.length||request.partial_topic_indices?.length||request.expanded_topic_indices?.length)throw new Error('Map LOD requires camera-only cut');
  if(view&&(!Number.isSafeInteger(view.camera_generation)||view.camera_generation<0||view.view_projection.length!==16||!view.view_projection.every(Number.isFinite)||![...view.viewport,view.glyph_diameter,view.picking_diameter].every(v=>Number.isFinite(v)&&v>0)))throw new Error('Invalid map LOD view');
  const prior=previous?.revision===request.revision&&previous.map_lod&&sameSpatial(config,previous.map_lod)?previous:null,openedBefore=new Set(prior?.map_lod?.opened_topic_indices??[]),visibleCounts=new Uint32Array(index.topics.length);
  for(let node=0;node<n;node++)for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++){const slot=index.memberships[at]!;if(graph.visible[node])visibleCounts[slot]=visibleCounts[slot]!+1;}
  const allEligible=eligibleSourceViewSlots(index.topics,new Set(index.topics.flatMap((topic,slot)=>visibleCounts[slot]?[topic.index]:[]))),containment=buildVisualContainment(index,graph.visible,allEligible),rank=new Int32Array(index.topics.length).fill(-1),opened=new Set<number>();allEligible.forEach((slot,i)=>{rank[slot]=i;});
  const ordered=allEligible.filter(slot=>containment.primaryBySlot[slot]===slot).toSorted((a,b)=>visibleCounts[b]!-visibleCounts[a]!||rank[a]!-rank[b]!),phases=new Float32Array(index.topics.length),spans=new Float32Array(index.topics.length);
  for(const slot of ordered){const topic=index.topics[slot]!;if(!view)continue;const span=projectedSphereSpan(topic.position,topic.radius,view),phase=sphereRevealPhase(span,config.split_px,config.merge_px,openedBefore.has(topic.index));spans[slot]=span;phases[slot]=phase.reveal;if(phase.opened)opened.add(slot);}
  const cover=buildSourceViewCover(index,graph.visible,[...containment.aliasesByPrimary.keys()]),scopes=cameraClaimScopes(index,graph.visible,cover.slots,containment.childrenBySlot,phases,containment.primaryBySlot),openSet=new Set(scopes.open_topic_indices),previousPrimary=prior?.map_lod?.projection?.node_primary_topic_indices,previousOpen=previousPrimary?Int32Array.from(previousPrimary,index=>openSet.has(index)?index:-1):undefined;
  const resultOnly=index.topics.flatMap((topic,slot)=>verifiedSessionResult(topic)&&visibleCounts[slot]===0?[topic.index]:[]),activeTopics=Uint32Array.from(new Set([...scopes.active_topic_indices,...resultOnly]));
  const projection=projectVisibleGraph(graph,index,{revision:request.revision,generation:request.generation,active_topic_indices:activeTopics,open_topic_indices:scopes.open_topic_indices,previous_owner_by_node:previousOpen,applicable_memberships:scopes.applicable_memberships}),activeSet=new Set(activeTopics),contexts=new Set([...scopes.context_topic_indices,...scopes.open_topic_indices]),summaries=new Map(projection.topic_summaries.map(topic=>[topic.topic_index,topic])),descendants=new Map<number,Set<number>>();
  const below=(slot:number):Set<number>=>{const old=descendants.get(slot);if(old)return old;const result=new Set<number>();for(const child of containment.childrenBySlot[slot]!){result.add(index.topics[child]!.index);for(const topic of below(child))result.add(topic);}descendants.set(slot,result);return result;};
  const contextOpacity=new Float32Array(index.topics.length),contextCount=new Uint32Array(index.topics.length);
  for(let node=0;node<n;node++)if(graph.visible[node]){let pixels=0;if(view){const at=node*3,m=view.view_projection,w=m[3]!*graph.positions[at]!+m[7]!*graph.positions[at+1]!+m[11]!*graph.positions[at+2]!+m[15]!;pixels=w>0?config.assertion_radius*view.viewport[1]*Math.hypot(m[1]!,m[5]!,m[9]!)/w:0;}const t=Math.max(0,Math.min(1,(pixels-4)/6)),visibleReplacement=projection.node_render_visible[node]?scopes.node_reveal[node]!*t*t*(3-2*t):0,applicable=scopes.applicable_memberships.topic_indices.subarray(scopes.applicable_memberships.offsets[node]!,scopes.applicable_memberships.offsets[node+1]!);
    const seen=new Set<number>();for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++){const slot=containment.primaryBySlot[index.memberships[at]!]!;if(slot<0||seen.has(slot))continue;seen.add(slot);const topic=index.topics[slot]!.index;if(!contexts.has(topic))continue;const replacedByChild=Array.from(applicable).some(child=>below(slot).has(child))?phases[slot]!:0;contextOpacity[slot]=(contextOpacity[slot]??0)+1-Math.max(visibleReplacement,replacedByChild);contextCount[slot]=contextCount[slot]!+1;}}
  for(const topic of projection.canonical_fallback_ordinals)scopes.node_reveal[topic]=1;
  const unchanged=prior&&prior.representative_by_node.length===n&&prior.representative_by_node.every((value,node)=>value===projection.representative_by_node[node])&&prior.proxies.length===projection.proxies.length&&prior.proxies.every((proxy,i)=>{const next=projection.proxies[i];return proxy.kind==='topic'&&next?.kind==='topic'&&proxy.topic_index===next.topic_index&&proxy.radius===next.radius&&proxy.position.every((value,axis)=>value===next.position[axis]);});
  return{...(unchanged?prior:projection),map_lod:{...config,projection:visibleProjectionMetadata(projection),opened_topic_indices:new Uint32Array(ordered.filter(slot=>opened.has(slot)).map(slot=>index.topics[slot]!.index)),reachable_topic_indices:activeTopics,all_view_count:allEligible.length+resultOnly.length,cover_topic_indices:new Uint32Array([...cover.slots].map(slot=>index.topics[slot]!.index)),cover_mapped_count:cover.mapped_nodes.length,cover_unassigned_count:cover.unassigned_nodes.length,shown_topic_indices:new Uint32Array(projection.proxies.flatMap(proxy=>proxy.kind==='cell'?[]:[proxy.topic_index])),projection_owner_by_node:projection.node_primary_topic_indices.slice(),current_frontier_topic_indices:activeTopics,context_topic_indices:new Uint32Array(contexts),node_reveal:scopes.node_reveal,group_owner_by_node:projection.node_primary_topic_indices.slice(),group_reveals:ordered.map(slot=>{const topic=index.topics[slot]!,summary=summaries.get(topic.index);return{topic_index:topic.index,span:spans[slot]!,reveal:phases[slot]!,nested:containment.parentsBySlot[slot]!.length>0&&visibleCounts[slot]!>1,position:[...topic.position],radius:topic.radius,projection_members:summary?.owned_node_count??0,projection_owned:summary?.owned_node_count??0,context_opacity:contexts.has(topic.index)?contextCount[slot]?Math.max(0,Math.min(1,contextOpacity[slot]!/contextCount[slot]!)):0:activeSet.has(topic.index)?1:0};}),visual_containment:[...containment.aliasesByPrimary].map(([primary,aliases])=>({primary_topic_index:index.topics[primary]!.index,parent_topic_indices:new Uint32Array(containment.parentsBySlot[primary]!.map(slot=>index.topics[slot]!.index)),alias_topic_indices:new Uint32Array(aliases.map(slot=>index.topics[slot]!.index)),alias_view_ids:aliases.map(slot=>sourceViewId(index.topics[slot]!))}))},spatial_lod:undefined,navigation:undefined,collapsed_topic_indices:request.collapsed_topic_indices,force_reveal_ordinals:request.force_reveal_ordinals,frontier_topic_indices:activeTopics,automatic_reveal_ordinals:new Uint32Array(Array.from(projection.node_render_visible).flatMap((flag,node)=>flag?[node]:[])),view:view?{...view,view_projection:view.view_projection.slice(),viewport:[...view.viewport],blocked_rects:view.blocked_rects?.map(rect=>[...rect]),request_generation:request.generation}:undefined};
}

export interface LodCandidate {lod:LodResult;rebuilds:number;token:number}
export interface LodReuse {base:{token:number;revision:string;generation:number};metadata:Pick<LodResult,'collapsed_topic_indices'|'force_reveal_ordinals'|'partial_topic_indices'|'automatic_reveal_ordinals'|'view'|'navigation'|'frontier_topic_indices'|'spatial_lod'|'map_lod'>}
export function lodReuse(token:number,base:LodResult,next:LodResult):LodReuse{
  return{base:{token,revision:base.revision,generation:base.generation},metadata:{...(next.map_lod||base.map_lod?{map_lod:next.map_lod,frontier_topic_indices:next.frontier_topic_indices}:{}),...(next.spatial_lod||base.spatial_lod?{spatial_lod:next.spatial_lod}:{}),...(next.navigation||base.navigation?{navigation:next.navigation?copyNavigation(next.navigation):undefined,frontier_topic_indices:next.frontier_topic_indices}:{}),collapsed_topic_indices:next.collapsed_topic_indices,force_reveal_ordinals:next.force_reveal_ordinals,partial_topic_indices:next.partial_topic_indices,automatic_reveal_ordinals:next.automatic_reveal_ordinals,view:next.view}};
}
export function restoreLodReuse(base:{token:number;lod:LodResult}|null,reuse:LodReuse):LodResult{
  if(!base||base.token!==reuse.base.token||base.lod.revision!==reuse.base.revision||base.lod.generation!==reuse.base.generation)throw new Error('Stale LOD reuse base');
  const m=reuse.metadata;if(!(m.collapsed_topic_indices instanceof Uint32Array)||!(m.force_reveal_ordinals instanceof Uint32Array)||m.partial_topic_indices!==undefined&&!(m.partial_topic_indices instanceof Uint32Array)||m.automatic_reveal_ordinals!==undefined&&!(m.automatic_reveal_ordinals instanceof Uint32Array))throw new Error('Invalid LOD reuse metadata');
  if(m.navigation&&(!Number.isSafeInteger(m.navigation.epoch)||m.navigation.epoch<0||!Array.isArray(m.navigation.topic_path)||!m.navigation.topic_path.every(v=>Number.isSafeInteger(v)&&v>=0)||!Array.isArray(m.navigation.expanded_cell_ids)||!m.navigation.expanded_cell_ids.every(v=>typeof v==='string')||!(m.frontier_topic_indices instanceof Uint32Array)))throw new Error('Invalid LOD navigation reuse metadata');
  if(m.spatial_lod&&(!validSpatial(m.spatial_lod)||!Array.isArray(m.spatial_lod.expanded_cell_ids)||!m.spatial_lod.expanded_cell_ids.every(id=>typeof id==='string')))throw new Error('Invalid spatial LOD reuse metadata');
  if(m.map_lod&&(!validSpatial(m.map_lod)||m.map_lod.assertion_radius===undefined||!(m.map_lod.opened_topic_indices instanceof Uint32Array)))throw new Error('Invalid map LOD reuse metadata');
  const phases=m.map_lod?.group_reveals?new Map(m.map_lod.group_reveals.map(group=>[group.topic_index,group])):null;
  return{...base.lod,...m,...(phases?{proxies:base.lod.proxies.map(proxy=>{const phase=proxy.kind==='cell'?undefined:phases.get(proxy.topic_index);return phase?{...proxy,nested:phase.nested,reveal:m.map_lod?.projection?0:proxy.kind!=='cell'&&proxy.visible_source_member_count>1?phase.reveal:1}:proxy;})}:{})};
}

/** Ordered navigation is a Source path, never a breadth-first geometric expansion. */
export function navigationKey(navigation:LodNavigation|undefined):string{return navigation?JSON.stringify([navigation.epoch,navigation.topic_path,navigation.expanded_cell_ids]):'';}
function copyNavigation(n:LodNavigation):LodNavigation{return{epoch:n.epoch,topic_path:[...n.topic_path],expanded_cell_ids:[...n.expanded_cell_ids]};}
/** Explicit semantic frontier plus independent, honest spatial residuals. */
export function projectNavigationLod(graph:GraphData,index:MembershipIndex,request:LodRequest,cache=new PresentationCellCache(),previous?:LodResult|null):LodResult{
  const navigation=request.navigation;if(!navigation)throw new Error('Navigation required');
  const n=graph.flags.length;if(index.offsets.length!==n+1)throw new Error('Navigation membership revision mismatch');
  if(!Number.isSafeInteger(navigation.epoch)||navigation.epoch<0)throw new Error('Invalid navigation epoch');
  const byIndex=new Map(index.topics.map((t,slot)=>[t.index,slot])),path=navigation.topic_path.map(value=>{const slot=byIndex.get(value);if(slot===undefined)throw new Error('Unknown navigation Source topic');return slot;});
  for(let i=1;i<path.length;i++)if(!index.children[path[i-1]!]!.includes(path[i]!))throw new Error('Navigation skips Source hierarchy');
  if(new Set(path).size!==path.length)throw new Error('Cyclic navigation path');
  // Explicit current registry roots lead ordinary navigation. Other roles keep
  // the established stable-ID order; absent metadata changes nothing.
  const rootPriority=(slot:number)=>!index.parents[slot]!.length&&index.topics[slot]!.navigation_membership==='selected_registry'?0:1;
  const compareOwners=(a:number,b:number)=>rootPriority(a)-rootPriority(b)||a-b;
  const roots=index.topics.flatMap((_,slot)=>index.parents[slot]!.length?[]:[slot]).toSorted(compareOwners),active=path.at(-1),frontier=active===undefined?roots:index.children[active]!,frontierSet=new Set(frontier),closed=new Set(roots);
  for(let i=0;i<path.length;i++){closed.delete(path[i]!);if(i<path.length-1)for(const sibling of index.children[path[i]!]!)if(sibling!==path[i+1])closed.add(sibling);}
  for(const slot of frontier)closed.add(slot);
  const sourceCounts=new Uint32Array(index.topics.length),owners=new Int32Array(n).fill(-1),topicMembers=index.topics.map(()=>[] as number[]),residual:number[]=[],global:number[]=[],forced=new Set(request.force_reveal_ordinals);
  // Current roots win shared memberships; stable Source IDs break role ties.
  for(let node=0;node<n;node++){
    if(!graph.visible[node])continue;let child=-1,context=-1,inside=false;
    for(let at=index.offsets[node]!;at<index.offsets[node+1]!;at++){
      const slot=index.memberships[at]!;sourceCounts[slot]=sourceCounts[slot]!+1;
      if(frontierSet.has(slot)&&(child<0||compareOwners(slot,child)<0))child=slot;
      if(slot===active)inside=true;
      if(closed.has(slot)&&(context<0||compareOwners(slot,context)<0))context=slot;
    }
    if(child>=0){owners[node]=child;topicMembers[child]!.push(node);}
    else if(active!==undefined&&inside){owners[node]=-2;residual.push(node);}
    else if(context>=0){owners[node]=context;topicMembers[context]!.push(node);}
    else{owners[node]=-3;global.push(node);}
  }
  const proxies:DisplayProxy[]=[],representative=new Int32Array(n).fill(-1),visible=new Float32Array(n),automatic:number[]=[];
  let canonical=0,represented=0;
  const admit=(node:number,auto=false)=>{if(representative[node]===node)return;representative[node]=node;visible[node]=1;canonical++;if(auto)automatic.push(node);};
  for(const node of forced)if(node<n&&graph.visible[node])admit(node);
  for(let slot=0;slot<index.topics.length;slot++){
    const members=topicMembers[slot]!.filter(node=>!forced.has(node));if(!members.length)continue;
    const topic=index.topics[slot]!,proxy=proxies.length;proxies.push({kind:'topic',topic_id:topic.id,topic_index:topic.index,position:[topic.position[0]??0,topic.position[1]??0,topic.position[2]??0],represented_count:members.length,source_member_count:topic.member_count,visible_source_member_count:sourceCounts[slot]!,internal_edge_count:0,generation:request.generation});
    for(const node of members)representative[node]=n+proxy;represented+=members.length;
  }
  const view=request.view,diameter=view?Math.max(view.glyph_diameter,view.picking_diameter):0;
  if(view&&(!Number.isSafeInteger(view.camera_generation)||view.camera_generation<0||view.view_projection.length!==16||!view.view_projection.every(Number.isFinite)||![...view.viewport,diameter].every(v=>Number.isFinite(v)&&v>0)))throw new Error('Invalid navigation view');
  const context=`cell:${encodeURIComponent(request.revision)}:${navigation.topic_path.join('.')||'global'}`;
  const rootsWithScope:{cell:PresentationCell;scope:number|null}[]=[];
  if(residual.length){const cell=cache.tree(graph.positions,new Uint32Array(residual),`${context}:residual`);if(cell)rootsWithScope.push({cell,scope:index.topics[active!]!.index});}
  if(global.length){const cell=cache.tree(graph.positions,new Uint32Array(global),`${context}:unowned`);if(cell)rootsWithScope.push({cell,scope:null});}
  // A route is one ancestor chain, never a set of accumulated sibling entries.
  // Validate against deterministic full geometry before filtering forced members.
  let candidates=rootsWithScope.map(root=>root.cell);
  for(const id of navigation.expanded_cell_ids){
    const cell=candidates.find(candidate=>candidate.id===id);
    if(!cell)throw new Error('Navigation skips presentation cell ancestry');
    candidates=cell.children;
  }
  const expanded=new Set(navigation.expanded_cell_ids);
  const walk=(cell:PresentationCell,scope:number|null):void=>{
    if(expanded.has(cell.id)&&cell.children.length){for(const child of cell.children)walk(child,scope);return;}
    const members=Array.from(cell.members).filter(node=>!forced.has(node));if(!members.length)return;
    // Camera/frustum/label readability never changes canonical membership.
    // Entering an internal cell exposes only its immediate children; entering
    // a noncoincident leaf exposes every Source member at its original position.
    if(members.length===1||expanded.has(cell.id)&&!cell.children.length&&!cell.coincident){for(const node of members)admit(node,true);return;}
    const proxy=proxies.length;proxies.push({kind:'cell',cell_id:cell.id,parent_cell_id:cell.parent,scope_topic_index:scope,position:cell.position,bounds:cell.bounds,represented_count:members.length,internal_edge_count:0,generation:request.generation,child_ids:cell.children.map(c=>c.id),coincident:cell.coincident});
    for(const node of members)representative[node]=n+proxy;represented+=members.length;
  };
  for(const {cell,scope} of rootsWithScope)walk(cell,scope);
  const unchanged=previous?.revision===request.revision&&previous.representative_by_node.length===n&&previous.representative_by_node.every((v,i)=>v===representative[i])&&previous.proxies.length===proxies.length&&previous.proxies.every((p,i)=>{const q=proxies[i]!;return p.represented_count===q.represented_count&&(p.kind==='cell'?q.kind==='cell'&&p.cell_id===q.cell_id:q.kind!=='cell'&&p.topic_id===q.topic_id);});
  const result=unchanged?{...previous!,collapsed_topic_indices:request.collapsed_topic_indices,force_reveal_ordinals:request.force_reveal_ordinals}:finalizeLod(graph,request,representative,visible,proxies,canonical,represented);
  return{...result,...(result.map_lod?{map_lod:undefined}:{}),...(result.spatial_lod?{spatial_lod:undefined}:{}),navigation:copyNavigation(navigation),frontier_topic_indices:new Uint32Array(frontier.map(slot=>index.topics[slot]!.index)),automatic_reveal_ordinals:new Uint32Array(automatic.toSorted((a,b)=>a-b)),view:view?{...view,view_projection:view.view_projection.slice(),viewport:[...view.viewport],blocked_rects:view.blocked_rects?.map(r=>[...r]),request_generation:request.generation}:undefined};
}
/** Resolve from the accepted partition only; no tree or Source endpoint is exposed. */
export function queryCellMembers(lod:LodResult,request:CellMembersRequest):CellMembersResult{
  if(request.revision!==lod.revision||request.lod_generation!==lod.generation)throw new Error('Expired cell query');
  if(!Number.isSafeInteger(request.offset)||request.offset<0||!Number.isSafeInteger(request.limit)||request.limit<1||request.limit>100)throw new Error('Invalid cell page');
  const proxy=lod.proxies.findIndex(p=>p.kind==='cell'&&p.cell_id===request.cell_id);if(proxy<0)throw new Error('Expired cell query');
  const representative=lod.representative_by_node.length+proxy,members:number[]=[];let total=0;
  for(let node=0;node<lod.representative_by_node.length;node++)if(lod.representative_by_node[node]===representative){if(total>=request.offset&&members.length<request.limit)members.push(node);total++;}
  return{...request,ordinals:new Uint32Array(members),total,next_offset:request.offset+members.length<total?request.offset+members.length:null};
}
