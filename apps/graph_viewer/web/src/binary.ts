export interface GraphData {
  positions: Float32Array; flags: Uint32Array; edges: Uint32Array;
  edgeActive: Uint8Array; visible: Float32Array; visibleEdges: Uint32Array; visibleEdgeOrdinals: Uint32Array;
}
export interface NodePatch { id: string; ordinal: number; position: [number, number, number]; flags: number }
export interface EdgePatch { id: string; ordinal: number; from: number; to: number }
export interface GraphPatch {
  upsert_nodes: NodePatch[]; upsert_edges: EdgePatch[]; remove_node_ordinals: number[];
  remove_edge_ordinals: number[]; status_changes: { ordinal: number; flags: number }[];
}
const DELETED = 0x80000000;

export function filterVisibility(graph: GraphData, history: boolean): void {
  const count = graph.flags.length;
  graph.visible = new Float32Array(count);
  for (let index = 0; index < count; index++) {
    const flags = graph.flags[index] ?? 0;
    graph.visible[index] = (flags & DELETED) === 0 && (history || (flags & 3) === 0) ? 1 : 0;
  }
  const endpoints = new Uint32Array(graph.edges.length);
  const ordinals = new Uint32Array(graph.edges.length / 2);
  let length = 0;
  for (let index = 0; index < graph.edgeActive.length; index++) {
    const from = graph.edges[index * 2] ?? count;
    const to = graph.edges[index * 2 + 1] ?? count;
    if (from >= count || to >= count) throw new Error('Invalid graph edge endpoint');
    if (graph.edgeActive[index] && graph.visible[from] && graph.visible[to]) {
      endpoints[length * 2] = from; endpoints[length * 2 + 1] = to;
      ordinals[length++] = index;
    }
  }
  graph.visibleEdges = endpoints.subarray(0, length * 2);
  graph.visibleEdgeOrdinals = ordinals.subarray(0, length);
}

export function decodeGraph(nodes: ArrayBuffer, edges: ArrayBuffer, nodeCount: number, edgeCount: number, history: boolean): GraphData {
  if (nodes.byteLength !== nodeCount * 16 || edges.byteLength !== edgeCount * 8) throw new Error('Graph binary length mismatch');
  const positions = new Float32Array(nodeCount * 3);
  const flags = new Uint32Array(nodeCount);
  const nodeView = new DataView(nodes);
  const edgeView = new DataView(edges);
  const endpoints = new Uint32Array(edgeCount * 2);
  for (let index = 0; index < nodeCount; index++) {
    for (let axis = 0; axis < 3; axis++) {
      const value = nodeView.getFloat32(index * 16 + axis * 4, true);
      if (!Number.isFinite(value)) throw new Error('Nonfinite node position');
      positions[index * 3 + axis] = value;
    }
    flags[index] = nodeView.getUint32(index * 16 + 12, true);
  }
  for (let index = 0; index < endpoints.length; index++) endpoints[index] = edgeView.getUint32(index * 4, true);
  const graph: GraphData = { positions, flags, edges: endpoints, edgeActive: new Uint8Array(edgeCount).fill(1), visible: new Float32Array(), visibleEdges: new Uint32Array(), visibleEdgeOrdinals: new Uint32Array() };
  filterVisibility(graph, history);
  return graph;
}

export function incidentIndices(graph: GraphData, ordinal: number): Uint32Array {
  const result = new Uint32Array(graph.visibleEdges.length);
  let count = 0;
  for (let index = 0; index < graph.visibleEdges.length; index += 2) {
    const from = graph.visibleEdges[index]; const to = graph.visibleEdges[index + 1];
    if (from === undefined || to === undefined) continue;
    if (from === ordinal || to === ordinal) { result[count++] = from; result[count++] = to; }
  }
  return result.subarray(0, count);
}

function appendSlotCount(items:readonly {ordinal:number}[],base:number):number {
  const seen=new Set<number>();let end=base;
  for(const item of items){
    if(!Number.isSafeInteger(item.ordinal)||item.ordinal<0||item.ordinal>=0xffffffff)throw new Error('Invalid live ordinal');
    if(seen.has(item.ordinal))throw new Error('Duplicate live slot');
    seen.add(item.ordinal);end=Math.max(end,item.ordinal+1);
  }
  let additions=0;for(const ordinal of seen)if(ordinal>=base)additions++;
  if(end-base!==additions)throw new Error('Noncontiguous live graph slots');
  return end;
}
function checkedOrdinal(ordinal:number,count:number):void {
  if(!Number.isSafeInteger(ordinal)||ordinal<0||ordinal>=count)throw new Error('Invalid live ordinal');
}
function checkedFlags(flags:number):void {
  if(!Number.isInteger(flags)||flags<0||flags>0xffffffff)throw new Error('Invalid live flags');
}
export function patchGraph(current:GraphData,patch:GraphPatch,history:boolean):GraphData {
  const nodes=appendSlotCount(patch.upsert_nodes,current.flags.length);
  const edges=appendSlotCount(patch.upsert_edges,current.edgeActive.length);
  for(const item of patch.upsert_nodes){if(item.position.length!==3||!item.position.every(Number.isFinite))throw new Error('Invalid live node position');checkedFlags(item.flags);}
  for(const item of patch.upsert_edges){checkedOrdinal(item.from,nodes);checkedOrdinal(item.to,nodes);}
  for(const ordinal of patch.remove_node_ordinals)checkedOrdinal(ordinal,nodes);
  for(const ordinal of patch.remove_edge_ordinals)checkedOrdinal(ordinal,edges);
  for(const item of patch.status_changes){checkedOrdinal(item.ordinal,nodes);checkedFlags(item.flags);}
  const graph:GraphData={positions:new Float32Array(nodes*3),flags:new Uint32Array(nodes),edges:new Uint32Array(edges*2),edgeActive:new Uint8Array(edges),visible:new Float32Array(),visibleEdges:new Uint32Array(),visibleEdgeOrdinals:new Uint32Array()};
  graph.positions.set(current.positions);graph.flags.set(current.flags);graph.edges.set(current.edges);graph.edgeActive.set(current.edgeActive);
  for(const item of patch.upsert_nodes){graph.positions.set(item.position,item.ordinal*3);graph.flags[item.ordinal]=item.flags;}
  for(const item of patch.upsert_edges){graph.edges[item.ordinal*2]=item.from;graph.edges[item.ordinal*2+1]=item.to;graph.edgeActive[item.ordinal]=1;}
  for(const ordinal of patch.remove_node_ordinals)graph.flags[ordinal]=DELETED;
  for(const ordinal of patch.remove_edge_ordinals)graph.edgeActive[ordinal]=0;
  for(const item of patch.status_changes)graph.flags[item.ordinal]=item.flags;
  filterVisibility(graph,history);
  return graph;
}
