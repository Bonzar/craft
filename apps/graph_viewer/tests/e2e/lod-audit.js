import{expectedLod}from'./lod-oracle.js';
const requireValue=(ok,message)=>{if(!ok)throw new Error(message);};
const sameArray=(a,b,label)=>{requireValue(a?.length===b?.length,label+' length');for(let i=0;i<a.length;i++)requireValue(a[i]===b[i],label+' mismatch at '+i);};
function offsetsValid(offsets,total,groups,label){requireValue(offsets.length===groups+1&&offsets[0]===0&&offsets[groups]===total,label+' endpoints');for(let i=1;i<offsets.length;i++)requireValue(Number.isSafeInteger(offsets[i])&&offsets[i]>=offsets[i-1]&&offsets[i]<=total,label+' monotonic');}
export function auditProjection(actual,source){
 const n=source.visible.length,m=source.edges.length/2,p=actual.proxy_topic_indices.length;
 requireValue(actual.node_count===n,'source node count');sameArray(actual.source_edges,source.edges,'source edge bytes');
 sameArray(actual.visible_node_flags,source.visible,'source node visibility');if(source.edgeActive)sameArray(actual.source_edge_active,source.edgeActive,'source edge activity');
 const expected=expectedLod({...source,collapsed:actual.collapsed_topic_indices,forced:actual.force_reveal_ordinals});
 const topicCounts=new Map(expected.topics.map(topic=>[topic.index,topic]));const seenProxyTopics=new Set();
 for(let i=0;i<p;i++){
  const topic=topicCounts.get(actual.proxy_topic_indices[i]);requireValue(topic&&!seenProxyTopics.has(topic.index),'unknown/duplicate proxy source topic');seenProxyTopics.add(topic.index);
  requireValue(topic.owned>0,'zero-owned proxy');requireValue(actual.proxy_represented_counts[i]===topic.owned,'proxy owner count mismatch');
  requireValue(actual.proxy_source_member_counts[i]===topic.sourceMembers,'source membership count mismatch');requireValue(actual.proxy_visible_source_member_counts[i]===topic.visibleMembers,'visible source membership count mismatch');
  if(actual.proxy_topic_ids)requireValue(actual.proxy_topic_ids[i]===topic.id,'proxy source identity mismatch');
 }
 requireValue(p===expected.topics.filter(topic=>topic.owned>0).length,'missing proxy owner');
 const normalize=rep=>{if(rep<n)return rep;const proxy=rep-n;requireValue(proxy>=0&&proxy<p,'invalid proxy representative');return n+actual.proxy_topic_indices[proxy];};
 requireValue(actual.representative_by_node.length===n&&actual.node_render_visible.length===n,'node partition lengths');
 for(let i=0;i<n;i++){
  requireValue(normalize(actual.representative_by_node[i])===expected.representativeByNode[i],'node owner mismatch at '+i);
  requireValue(actual.node_render_visible[i]===(expected.representativeByNode[i]===i?1:0),'canonical visibility mismatch');
 }
 const seenCanonical=new Uint8Array(n);for(const node of actual.canonical_render_ordinals){requireValue(node<n&&!seenCanonical[node]&&expected.representativeByNode[node]===node,'duplicate/wrong canonical source ordinal');seenCanonical[node]=1;}
 requireValue(actual.canonical_render_ordinals.length===expected.coverage.canonicalNodes,'canonical count mismatch');
 const visibleSeen=new Uint8Array(m);for(const edge of actual.visible_source_edge_ordinals){requireValue(edge<m&&!visibleSeen[edge]&&expected.edgeClass[edge]!==255,'duplicate/hidden visible edge ordinal');visibleSeen[edge]=1;}
 for(let i=0;i<m;i++)requireValue(Boolean(visibleSeen[i])===(expected.edgeClass[i]!==255),'source visible edge coverage mismatch');
 const seenEdges=new Uint8Array(m);let originals=0,bundled=0,internal=0;
 const claim=(edge,category)=>{requireValue(Number.isInteger(edge)&&edge>=0&&edge<m,'out of range source edge');requireValue(!seenEdges[edge],'duplicate source edge ordinal '+edge);requireValue(expected.edgeClass[edge]===category,'wrong source edge category '+edge);seenEdges[edge]=1;};
 const b=actual.bundle_pairs.length/2;requireValue(Number.isInteger(b),'unpaired bundle endpoints');
 offsetsValid(actual.bundle_offsets,actual.bundle_edge_ordinals.length,b,'bundle CSR');offsetsValid(actual.proxy_internal_offsets,actual.proxy_internal_edge_ordinals.length,p,'internal CSR');
 const renderCount=actual.edge_pick_kind.length;requireValue(actual.render_edges.length===renderCount*2&&actual.edge_pick_target.length===renderCount,'render edge mapping lengths');const renderedBundles=new Uint8Array(b);const originalOrder=[];
 for(let line=0;line<renderCount;line++){
  const kind=actual.edge_pick_kind[line],target=actual.edge_pick_target[line],from=actual.render_edges[line*2],to=actual.render_edges[line*2+1];
  if(kind===0){claim(target,0);requireValue(from===source.edges[target*2]&&to===source.edges[target*2+1],'original render endpoint mismatch');originals++;originalOrder.push(target);}
  else{requireValue(kind===1&&target<b&&!renderedBundles[target],'unknown/duplicate rendered bundle');renderedBundles[target]=1;requireValue(from===actual.bundle_pairs[target*2]&&to===actual.bundle_pairs[target*2+1],'bundle render endpoint mismatch');}
 }
 sameArray(actual.original_render_edge_ordinals,originalOrder,'original source ordinal order');
 const pairKeys=new BigUint64Array(b);
 for(let bundle=0;bundle<b;bundle++){
  const from=actual.bundle_pairs[bundle*2],to=actual.bundle_pairs[bundle*2+1];requireValue(from!==to&&(from>=n||to>=n)&&renderedBundles[bundle],'invalid/unrendered bundle pair');pairKeys[bundle]=(BigInt(from)<<32n)|BigInt(to);
  requireValue(actual.bundle_offsets[bundle+1]>actual.bundle_offsets[bundle],'empty source edge bundle');
  for(let at=actual.bundle_offsets[bundle];at<actual.bundle_offsets[bundle+1];at++){
   const edge=actual.bundle_edge_ordinals[at];claim(edge,1);
   requireValue(actual.representative_by_node[source.edges[edge*2]]===from&&actual.representative_by_node[source.edges[edge*2+1]]===to,'bundle direction/endpoint mismatch');bundled++;
  }
 }
 pairKeys.sort();for(let i=1;i<pairKeys.length;i++)requireValue(pairKeys[i]!==pairKeys[i-1],'duplicate directed representative pair');
 for(let proxy=0;proxy<p;proxy++)for(let at=actual.proxy_internal_offsets[proxy];at<actual.proxy_internal_offsets[proxy+1];at++){
  const edge=actual.proxy_internal_edge_ordinals[at];claim(edge,2);requireValue(actual.representative_by_node[source.edges[edge*2]]===n+proxy&&actual.representative_by_node[source.edges[edge*2+1]]===n+proxy,'internal source endpoint mismatch');internal++;
 }
 let seen=0;for(let edge=0;edge<m;edge++){requireValue(Boolean(seenEdges[edge])===(expected.edgeClass[edge]!==255),'missing/ineligible source edge ordinal '+edge);seen+=seenEdges[edge];}
 const counts={visible_nodes:expected.coverage.visibleNodes,canonical_nodes:expected.coverage.canonicalNodes,represented_nodes:expected.coverage.representedNodes,visible_edges:expected.coverage.visibleEdges,original_edges:originals,bundled_edges:bundled,internal_edges:internal};
 for(const[key,value]of Object.entries(counts))requireValue(actual.coverage[key]===value,'declared coverage '+key+' mismatch');
 requireValue(renderCount===originals+b,'render line count mismatch');
 return{nodeOwnersChecked:expected.coverage.visibleNodes,visibleEdgesChecked:seen,duplicateEdges:0,proxyCount:p,bundleCount:b,renderedLines:renderCount,counts,allVisibleSourceOrdinalsExactlyOnce:true};
}
