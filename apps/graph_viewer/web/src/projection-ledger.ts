import type {GraphData} from './binary.ts';
import type {LodRequest,LodResult,DisplayProxy} from './lod.ts';
function prefix(counts:readonly number[]|Uint32Array):Uint32Array{const result=new Uint32Array(counts.length+1);for(let i=0;i<counts.length;i++)result[i+1]=(result[i]??0)+(counts[i]??0);return result;}
/** One exact edge classifier for legacy Source cuts and progressive display cuts. */
export function finalizeProjection(graph:GraphData,request:LodRequest,representative:Int32Array,visible:Float32Array,proxies:DisplayProxy[],canonical:number,represented:number):LodResult{
  const nodes=graph.flags.length,sourceEdges=graph.visibleEdgeOrdinals.length;
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
  return {partial_topic_indices:request.partial_topic_indices?.slice(),revision:request.revision,generation:request.generation,collapsed_topic_indices:request.collapsed_topic_indices,force_reveal_ordinals:request.force_reveal_ordinals,representative_by_node:representative,node_render_visible:visible,proxies,render_positions:positions,render_edges:renderEdges,edge_pick_kind:pickKind,edge_pick_target:pickTarget,bundle_pairs:new Uint32Array(bundlePairs),bundle_offsets:offsets,bundle_edge_ordinals:bundleEdges,proxy_internal_offsets:internalOffsets,proxy_internal_edge_ordinals:internalEdges,coverage:{visible_nodes:canonical+represented,canonical_nodes:canonical,represented_nodes:represented,visible_edges:sourceEdges,original_edges:originals,bundled_edges:bundled,internal_edges:internal}};
}
