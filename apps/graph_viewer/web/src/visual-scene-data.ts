import type { GraphData } from './binary.ts';
import {filterVisibility} from './binary.ts';
import type { Topics } from './contracts.ts';
import { layoutVisual3D } from './visual-layout-3d.ts';
import {eligibleSourceViewSlots,sourceViewIdentity,verifiedSessionResult} from './lod.ts';
import {worldGlyphCalibration,ASSERTION_MAX_RADIUS_FACTOR} from './world-glyphs.ts';
import type {WorldGlyphCalibration} from './world-glyphs.ts';
import {sourceDegrees} from './visual-transition.ts';
import type { VisualLayout3D, VisualLayout3DInput, VisualTheme3D } from './visual-layout-3d.ts';

export type VisualSceneDataOptions = Pick<VisualLayout3DInput, 'nodeIds' | 'previous' | 'unit' | 'fixedPositions'> & {history?:boolean;sessionId?:string|null;mode?:'topics'|'edges'};
export interface VisualSceneData {
  graph: GraphData;
  topics: Topics;
  layout: VisualLayout3D;
  calibration: WorldGlyphCalibration;
  /** No actual members means no measured extent, even if Source supplied a box. */
  unboundedTopicIndices: number[];
}

/**
 * Adapt a canonical Source snapshot for Scene and its spatial worker. Keep the
 * canonical graph/topics separately for deltas, cards and Source metadata.
 * Every Source view with visible actual members drives coordinates equally; every topic
 * receives bounds in the same new frame. All typed buffers are independent so
 * transferring a worker buffer cannot detach a canonical Source buffer.
 */
export function deriveVisualSceneData(sourceGraph: GraphData, sourceTopics: Topics, options: VisualSceneDataOptions = {}): VisualSceneData {
  if (sourceGraph.positions.length !== sourceGraph.flags.length * 3) throw new Error('Visual scene Source node count mismatch');
  const {history=false,sessionId=null,mode='topics',...layoutOptions}=options;
  const hasLayout=sourceTopics.layout_topics!==null&&sourceTopics.layout_topics!==undefined,hasLayoutMembers=sourceTopics.layout_memberships!==null&&sourceTopics.layout_memberships!==undefined;
  if(hasLayout!==hasLayoutMembers)throw new Error('Historical result layout family is incomplete');
  const hasResults=sourceTopics.topics.some(topic=>topic.session_result!==null&&topic.session_result!==undefined);
  if(hasResults){if(!sessionId||!hasLayout)throw new Error('Historical result requires its selected session and layout family');for(const topic of sourceTopics.topics)if(!verifiedSessionResult(topic)||topic.session_result!.session_id!==sessionId)throw new Error('Historical Source result does not match selected session/version');}
  const family=sourceGraph.flags.some(flags=>(flags&4)!==0)?{...sourceGraph,flags:Uint32Array.from(sourceGraph.flags,flags=>(flags&~4)>>>0)}:sourceGraph;
  if(family!==sourceGraph)filterVisibility(family,history);
  const membersByTopic = new Map<number, number[]>();
  // Concatenate actual rows instead of silently dropping a repeated topic row.
  for (const membership of sourceTopics.memberships) {
    let members = membersByTopic.get(membership.topic_index);
    if (!members) { members = []; membersByTopic.set(membership.topic_index, members); }
    for (const node of membership.node_indices) members.push(node);
  }
  const familyTopics=sourceTopics.layout_topics??sourceTopics.topics,familyRows=sourceTopics.layout_memberships??sourceTopics.memberships,familyMembers=new Map<number,number[]>();
  for(const row of familyRows){const members=familyMembers.get(row.topic_index)??[];members.push(...row.node_indices);familyMembers.set(row.topic_index,members);}
  const familyThemes:VisualTheme3D[]=familyTopics.map(topic=>({id:sourceViewIdentity(topic),index:topic.index,members:(familyMembers.get(topic.index)??[]).filter(node=>Boolean(family.visible[node]))}));
  const visibleTopics=new Set(familyThemes.filter(theme=>theme.members.length).map(theme=>theme.index));
  const themes=eligibleSourceViewSlots(familyTopics,visibleTopics).map(slot=>familyThemes[slot]!);
  // Only the current family determines canonical XYZ. Historical result indices
  // and memberships determine their bounds in that already fixed world frame.
  const boundThemes:VisualTheme3D[]=sourceTopics.topics.map(topic=>({id:sourceViewIdentity(topic),index:topic.index,members:(membersByTopic.get(topic.index)??[]).filter(node=>verifiedSessionResult(topic)||Boolean(family.visible[node]))}));
  const layout = layoutVisual3D({ ...layoutOptions, sourcePositions: sourceGraph.positions, edges: sourceGraph.edges, edgeActive: sourceGraph.edgeActive, themes:mode==='edges'?[]:themes, boundThemes });
  const calibration=worldGlyphCalibration(layout.positions),degrees=sourceDegrees(sourceGraph.flags.length,sourceGraph.edges,family.visibleEdgeOrdinals);
  const bounds = new Map(layout.topicBounds.map(bound => [bound.index, bound]));
  const unboundedTopicIndices: number[] = [];
  const topics: Topics = {
    ...sourceTopics,
    layout_topics:null,layout_memberships:null,
    topics: sourceTopics.topics.map(topic => {
      const bound = bounds.get(topic.index);
      if (!bound) unboundedTopicIndices.push(topic.index);
      let radius=bound?.radius??0;
      if(bound)for(const node of membersByTopic.get(topic.index)??[]){if(!verifiedSessionResult(topic)&&!family.visible[node])continue;const at=node*3,body=calibration.assertionRadius*(1+(ASSERTION_MAX_RADIUS_FACTOR-1)*(degrees[node]??0));radius=Math.max(radius,Math.hypot(layout.positions[at]!-bound.center[0],layout.positions[at+1]!-bound.center[1],layout.positions[at+2]!-bound.center[2])+body);}
      const sourceAnchor=!bound&&verifiedSessionResult(topic)&&topic.position.length===3&&topic.position.every(Number.isFinite)&&Number.isFinite(topic.radius)&&topic.radius>0;
      return { ...topic, position: bound ? [...bound.center] : sourceAnchor?[...topic.position]:[0, 0, 0], radius:sourceAnchor?topic.radius:radius };
    }),
    memberships: sourceTopics.memberships.map(membership => ({ ...membership, node_indices: [...membership.node_indices] })),
    links: sourceTopics.links.map(link => ({ ...link })),
  };
  const graph: GraphData = {
    ...sourceGraph,
    positions: layout.positions,
    flags: sourceGraph.flags.slice(), edges: sourceGraph.edges.slice(), edgeActive: sourceGraph.edgeActive.slice(),
    visible: sourceGraph.visible.slice(), visibleEdges: sourceGraph.visibleEdges.slice(), visibleEdgeOrdinals: sourceGraph.visibleEdgeOrdinals.slice(),
  };
  return { graph, topics, layout, calibration, unboundedTopicIndices };
}
