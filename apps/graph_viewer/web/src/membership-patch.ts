import {manifestSchema,topicSchema} from './contracts.ts';
import type {Delta,Manifest,Topics,Topic} from './contracts.ts';
import {array,object,oneOf,str,uint} from './schema.ts';
const patchSchema=object({schema:oneOf(['source-membership/v1']),topics:array(topicSchema),memberships:array(object({topic_id:str,topic_index:uint,added_node_ordinals:array(uint),removed_node_ordinals:array(uint)})),completeness:oneOf(['complete','partial']),unknowns:array(str)});
export function checkedDeltaManifest(delta:Delta,base:Manifest):Manifest|null {
  if(!delta.manifest)return null;
  try{
    const next=manifestSchema.parse(delta.manifest);
    if(next.schema!==1||!next.complete_graph||delta.base_revision!==base.revision||next.revision!==delta.revision||next.source_revision!==delta.source_revision||next.captured_at!==delta.captured_at||next.live_cursor!==delta.cursor||next.provider_id!==base.provider_id||JSON.stringify(next.selector)!==JSON.stringify(base.selector))return null;
    const c=next.counts;
    if(c.nodes<base.counts.nodes||c.edges<base.counts.edges||c.visible_nodes>c.nodes||c.visible_edges>c.edges||c.eligible_before_status_filter>c.nodes||(c.source_eligible_edges!==null&&c.source_eligible_edges>c.edges))return null;
    for(const[kind,stride]of[['nodes',16],['edges',8]]as const){const info=next[kind];if(info.stride!==stride||info.byte_length!==c[kind]*stride||info.format!==`gv-${kind}-le-v1`||info.url!==`/api/v1/graph/${next.revision}/${kind}.bin`)return null;}
    if(next.topics_url!==`/api/v1/graph/${next.revision}/topics`)return null;
    return next;
  }catch{return null;}
}
export function applyMembershipPatch(delta:Delta,base:Manifest,current:Topics):{manifest:Manifest;topics:Topics;changedTopicIds:string[]}|null {
  if(delta.topic_changes.length!==1)return null;
  try{
    const patch=patchSchema.parse(delta.topic_changes[0]),manifest=checkedDeltaManifest(delta,base);
    if(!manifest||!manifest.delta_capabilities.includes('source-membership/v1')||manifest.counts.topics!==current.topics.length||current.revision!==base.revision)return null;
    const topics:Topic[]=current.topics.map(t=>({...t})),memberships=current.memberships.map(m=>({topic_index:m.topic_index,node_indices:[...m.node_indices]})),seenTopics=new Set<number>(),seenMembership=new Set<number>();
    for(const topic of patch.topics){if(seenTopics.has(topic.index)||topics[topic.index]?.id!==topic.id)return null;seenTopics.add(topic.index);topics[topic.index]=topic;}
    for(const change of patch.memberships){
      if(seenMembership.has(change.topic_index)||topics[change.topic_index]?.id!==change.topic_id)return null;seenMembership.add(change.topic_index);
      const row=memberships.find(m=>m.topic_index===change.topic_index);if(!row)return null;
      const values=new Set(row.node_indices),added=new Set(change.added_node_ordinals),removed=new Set(change.removed_node_ordinals);
      if(added.size!==change.added_node_ordinals.length||removed.size!==change.removed_node_ordinals.length)return null;
      for(const ordinal of removed){if(ordinal>=manifest.counts.nodes||!values.has(ordinal)||added.has(ordinal))return null;values.delete(ordinal);}
      for(const ordinal of added){if(ordinal>=manifest.counts.nodes||values.has(ordinal))return null;values.add(ordinal);}
      row.node_indices=[...values].sort((a,b)=>a-b);
    }
    for(const topic of topics){const row=memberships.find(m=>m.topic_index===topic.index);if(!row||row.node_indices.length!==topic.member_count)return null;}
    return{manifest,topics:{...current,revision:delta.revision,topics,memberships,completeness:patch.completeness,unknowns:patch.unknowns},changedTopicIds:[...new Set([...patch.topics.map(topic=>topic.id),...patch.memberships.map(change=>change.topic_id)])]};
  }catch{return null;}
}
