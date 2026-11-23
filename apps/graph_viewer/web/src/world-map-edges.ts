import type {GraphData} from './binary.ts';
import type {LodResult,LodView} from './lod.ts';
import {worldEdgeBoundary,worldEdgeBoundarySegment,worldEdgeReveal} from './world-map-edge-geometry.ts';
/** Immutable actual Source edge geometry plus the accepted coverage ledger.
 * Camera frames may bundle strokes without changing these coordinates or IDs. */
export interface WorldMapEdges {
  positions:Float32Array;
  endpoints:Uint32Array;
  source_ordinals:Uint32Array;
  source_slots:Int32Array;
  /** 0=original Source edge, 1=accepted bundle, 2=accepted proxy internal CSR. */
  accepted_kind:Uint8Array;
  accepted_target:Uint32Array;
  /** Immutable cover partition captured before camera/body admissions. */
  visual_lod:LodResult;
}
export type WorldMapEdgePick={kind:'edge';ordinal:number};
export function worldMapEdges(graph:GraphData,lod:LodResult,previous?:WorldMapEdges|null):WorldMapEdges{
  const ordinals=graph.visibleEdgeOrdinals,count=ordinals.length,slots=new Int32Array(graph.edgeActive.length).fill(-1),kind=new Uint8Array(count).fill(255),target=new Uint32Array(count);
  for(let line=0;line<count;line++)slots[ordinals[line]!]=line;
  const assign=(ordinal:number,category:number,value:number):void=>{const line=slots[ordinal]??-1;if(line<0||kind[line]!==255)throw new Error('Physical edge CSR is not an exact Source partition');kind[line]=category;target[line]=value;};
  for(let line=0;line<lod.edge_pick_kind.length;line++)if(lod.edge_pick_kind[line]===0){const ordinal=lod.edge_pick_target[line]!;assign(ordinal,0,ordinal);}
  for(let bundle=0;bundle<lod.bundle_offsets.length-1;bundle++)for(let at=lod.bundle_offsets[bundle]!;at<lod.bundle_offsets[bundle+1]!;at++)assign(lod.bundle_edge_ordinals[at]!,1,bundle);
  for(let proxy=0;proxy<lod.proxy_internal_offsets.length-1;proxy++)for(let at=lod.proxy_internal_offsets[proxy]!;at<lod.proxy_internal_offsets[proxy+1]!;at++)assign(lod.proxy_internal_edge_ordinals[at]!,2,proxy);
  if(kind.some(value=>value===255)||graph.visibleEdges.length!==count*2)throw new Error('Physical edge CSR omits a visible Source edge');
  return{positions:graph.positions,endpoints:graph.visibleEdges,source_ordinals:ordinals,source_slots:slots,accepted_kind:kind,accepted_target:target,visual_lod:previous&&previous.positions===graph.positions&&previous.visual_lod.revision===lod.revision&&previous.visual_lod.map_lod?.cover_topic_indices?.join(',')===lod.map_lod?.cover_topic_indices?.join(',')?previous.visual_lod:lod};
}
export function worldMapEdgePick(edges:WorldMapEdges,line:number):WorldMapEdgePick|null{
  if(!Number.isSafeInteger(line)||line<0||line>=edges.source_ordinals.length)return null;return{kind:'edge',ordinal:edges.source_ordinals[line]!};
}
/** Highlight actual Source incidence without routing through a sphere centre. */
export function worldMapRelatedEdges(edges:WorldMapEdges,members:Uint8Array):Uint32Array{
  const result:number[]=[];for(let line=0;line<edges.source_ordinals.length;line++){const from=edges.endpoints[line*2]!,to=edges.endpoints[line*2+1]!;if(members[from]||members[to])result.push(from,to);}return new Uint32Array(result);
}

/** Render strokes may crossfade, while the accepted Source partition stays exact. */
export interface WorldMapEdgeFrame {
  positions:Float32Array;endpoints:Uint32Array;opacity:Float32Array;
  pick_kind:Uint8Array;pick_target:Uint32Array;
  source_offsets:Uint32Array;source_edge_ordinals:Uint32Array;
}
/** Visibility of a real child boundary as its displayed ancestors disclose it. */
export function worldMapGroupReach(lod:LodResult):Map<number,number>{
  const phases=new Map(lod.map_lod?.group_reveals?.map(group=>[group.topic_index,group.reveal])??[]),parents=new Map(lod.map_lod?.visual_containment?.map(group=>[group.primary_topic_index,Array.from(group.parent_topic_indices??[])])??[]),cover=new Set(lod.map_lod?.cover_topic_indices??[]),shown=new Set([...lod.proxies.flatMap(proxy=>proxy.kind==='cell'?[]:[proxy.topic_index]),...lod.map_lod?.context_topic_indices??[]]),result=new Map<number,number>();
  const reach=(index:number):number=>{const known=result.get(index);if(known!==undefined)return known;if(cover.has(index)||!(parents.get(index)?.length)){result.set(index,1);return 1;}let value=0;for(const parent of parents.get(index)??[])if(shown.has(parent))value=Math.max(value,reach(parent)*(phases.get(parent)??0));result.set(index,value);return value;};for(const index of shown)reach(index);return result;
}
export function worldMapEdgeFrame(model:WorldMapEdges,accepted:LodResult,view:Pick<LodView,'view_projection'|'viewport'>):WorldMapEdgeFrame{
  const lod=model.visual_lod,n=lod.representative_by_node.length,positions:number[]=[],opacity:number[]=[],kinds:number[]=[],targets:number[]=[],ordinals:number[]=[],offsets:number[]=[0],reach=worldMapGroupReach(accepted),groups=new Map(accepted.map_lod?.group_reveals?.map(group=>[group.topic_index,group.reveal])??[]),scopes=new Map(accepted.proxies.flatMap(proxy=>proxy.kind==='cell'?[]:[[proxy.topic_index,proxy] as const]));
  type Anchor={key:string;point:[number,number,number];radius:number;weight:number};
  const at=(node:number):[number,number,number]=>[model.positions[node*3]!,model.positions[node*3+1]!,model.positions[node*3+2]!];
  const owner=(node:number)=>accepted.map_lod?.group_owner_by_node?.[node]??-1;
  const gate=(node:number)=>accepted.map_lod?.node_reveal?.[node]??(owner(node)>=0?(groups.get(owner(node))??1):1);
  const internalGate=(from:number,to:number):number=>{if(accepted.map_lod?.projection)return 1;const offsets=accepted.map_lod?.node_gate_offsets,topics=accepted.map_lod?.node_gate_topic_indices;if(!offsets||!topics)return 1;const left=new Set(Array.from(topics.subarray(offsets[from]!,offsets[from+1]!)));let value=1;for(let at=offsets[to]!;at<offsets[to+1]!;at++)if(left.has(topics[at]!))value=Math.min(value,groups.get(topics[at]!)??1);return value;};
  const pixels=(point:readonly number[]):number=>{const m=view.view_projection,w=m[3]!*point[0]!+m[7]!*point[1]!+m[11]!*point[2]!+m[15]!;return w>0?(accepted.map_lod?.assertion_radius??0)*view.viewport[1]*Math.hypot(m[1]!,m[5]!,m[9]!)/w:0;};
  const anchors=(node:number):Anchor[]=>{
    const rep=lod.representative_by_node[node]!,proxy=rep>=n?lod.proxies[rep-n]:undefined,base:Anchor=proxy&&proxy.kind!=='cell'?{key:`scope:${proxy.topic_index}`,point:[...proxy.position],radius:proxy.radius??0,weight:1}:{key:`node:${node}`,point:at(node),radius:0,weight:1},offsets=accepted.map_lod?.node_gate_offsets,topics=accepted.map_lod?.node_gate_topic_indices;
    // The current pure projection selects the only applicable open context (or
    // closed owner). A closed overlapping peer must never mask that endpoint.
    if(accepted.map_lod?.projection){
      const primary=accepted.map_lod.projection.node_primary_topic_indices[node]!,metadata=accepted.map_lod.group_reveals?.find(group=>group.topic_index===primary),current=scopes.get(primary),point=current?.position??metadata?.position,radius=current?.radius??metadata?.radius;
      if(!point||radius===undefined)return[{key:`node:${node}`,point:at(node),radius:0,weight:1}];
      const handover=accepted.node_render_visible[node]&&base.key!==`scope:${primary}`?gate(node):1,weight=(reach.get(primary)??1)*handover,anchor:Anchor={key:`scope:${primary}`,point:[point[0]!,point[1]!,point[2]!],radius,weight};
      if(weight>=1||base.key===anchor.key)return[{...anchor,weight:1}];
      return[...(weight>0?[anchor]:[]),{...base,weight:1-weight}];
    }
    const candidates=offsets&&topics?Array.from(topics.subarray(offsets[node]!,offsets[node+1]!)).filter(index=>scopes.has(index)).toSorted((a,b)=>(scopes.get(a)!.visible_source_member_count-scopes.get(b)!.visible_source_member_count)||(a-b)):[owner(node)].filter(index=>index>=0&&scopes.has(index)),result:Anchor[]=[];let remaining=1;
    for(const index of candidates){const nested=scopes.get(index)!,closed=(1-(groups.get(index)??1))*(reach.get(index)??1),weight=remaining*closed;if(weight>0)result.push({key:`scope:${index}`,point:[...nested.position],radius:nested.radius??0,weight});remaining*=1-closed;if(remaining<=1e-7)break;}
    if(remaining>1e-7)result.push({...base,weight:remaining});const merged=new Map<string,Anchor>();for(const anchor of result){const old=merged.get(anchor.key);if(old)old.weight+=anchor.weight;else merged.set(anchor.key,{...anchor});}return[...merged.values()];
  };
  const stroke=(a:readonly number[],b:readonly number[],alpha:number,kind:number,target:number,source:ArrayLike<number>):void=>{if(alpha<=0)return;positions.push(...a,...b);opacity.push(alpha,alpha);kinds.push(kind);targets.push(target);for(let i=0;i<source.length;i++)ordinals.push(source[i]!);offsets.push(ordinals.length);};
  const bundles=new Map<string,{a:Anchor;b:Anchor;weights:number[];source:number[]}>();
  for(let line=0;line<model.source_ordinals.length;line++){
    const ordinal=model.source_ordinals[line]!,from=model.endpoints[line*2]!,to=model.endpoints[line*2+1]!,a=at(from),b=at(to),aa=anchors(from),bb=anchors(to),ga=gate(from),gb=gate(to),inside=internalGate(from,to),ta=worldEdgeReveal(pixels(a),3.5,10)*ga,tb=worldEdgeReveal(pixels(b),3.5,10)*gb,shared=!accepted.map_lod?.projection&&owner(from)>=0&&owner(from)===owner(to)||aa.length===1&&bb.length===1&&aa[0]!.key===bb[0]!.key;
    if(from===to||shared){stroke(a,b,Math.min(worldEdgeReveal(pixels(a),4.5,12)*ga,worldEdgeReveal(pixels(b),4.5,12)*gb),0,ordinal,[ordinal]);continue;}
    for(const x of aa)for(const y of bb){const boundary=worldEdgeBoundarySegment(x.point,x.radius,y.point,y.radius),wa=x.radius?1-ta:ta,wb=y.radius?1-tb:tb,weight=x.weight*y.weight;
      if(boundary&&wa*wb*inside>0){const key=`${x.key}>${y.key}`,bundle=bundles.get(key)??{a:x,b:y,weights:[],source:[]};bundle.weights.push(wa*wb*weight*inside);bundle.source.push(ordinal);bundles.set(key,bundle);}
      if(boundary&&x.radius&&y.radius){stroke(a,worldEdgeBoundary(y.point,y.radius,a),ta*(1-tb)*weight*inside,0,ordinal,[ordinal]);stroke(worldEdgeBoundary(x.point,x.radius,b),b,(1-ta)*tb*weight*inside,0,ordinal,[ordinal]);}
    }
    stroke(a,b,ta*tb,0,ordinal,[ordinal]);
  }
  let bundleIndex=0;for(const bundle of bundles.values()){const boundary=worldEdgeBoundarySegment(bundle.a.point,bundle.a.radius,bundle.b.point,bundle.b.radius);if(boundary)stroke(boundary[0],boundary[1],bundle.weights.reduce((maximum,value)=>Math.max(maximum,value),0),1,bundleIndex++,bundle.source);}
  return{positions:new Float32Array(positions),endpoints:Uint32Array.from({length:positions.length/3},(_,i)=>i),opacity:new Float32Array(opacity),pick_kind:new Uint8Array(kinds),pick_target:new Uint32Array(targets),source_offsets:new Uint32Array(offsets),source_edge_ordinals:new Uint32Array(ordinals)};
}
const pickLevels=new WeakMap<WorldMapEdgeFrame,Float32Array>();
/** Pick the strongest visible contribution per Source ID, including mid-fade
 * where no one of the boundary/hybrid/original strokes can exceed 0.5. */
export function worldMapFramePickLevels(frame:WorldMapEdgeFrame):Float32Array{
  const cached=pickLevels.get(frame);if(cached)return cached;const maximum=new Map<number,number>();for(let line=0;line<frame.pick_kind.length;line++)for(let at=frame.source_offsets[line]!;at<frame.source_offsets[line+1]!;at++){const id=frame.source_edge_ordinals[at]!;maximum.set(id,Math.max(maximum.get(id)??0,frame.opacity[line*2]!));}
  const levels=new Float32Array(frame.pick_kind.length);for(let line=0;line<levels.length;line++)if(frame.opacity[line*2]!>=.03)for(let at=frame.source_offsets[line]!;at<frame.source_offsets[line+1]!;at++)if(frame.opacity[line*2]!>=maximum.get(frame.source_edge_ordinals[at]!)!-1e-6){levels[line]=1;break;}pickLevels.set(frame,levels);return levels;
}
export function worldMapFramePick(frame:WorldMapEdgeFrame,line:number):{kind:'edge';ordinal:number}|{kind:'edge_bundle';bundleIndex:number;sourceOrdinals?:Uint32Array}|null{
  if(!Number.isSafeInteger(line)||line<0||line>=frame.pick_kind.length||!worldMapFramePickLevels(frame)[line])return null;
  return frame.pick_kind[line]===1?{kind:'edge_bundle',bundleIndex:frame.pick_target[line]!,sourceOrdinals:frame.source_edge_ordinals.slice(frame.source_offsets[line],frame.source_offsets[line+1])}:{kind:'edge',ordinal:frame.pick_target[line]!};
}
export function worldMapFrameRelatedEdges(frame:WorldMapEdgeFrame,model:WorldMapEdges,members:Uint8Array):Uint32Array{
  const relevant=new Set<number>();for(let line=0;line<model.source_ordinals.length;line++)if(members[model.endpoints[line*2]!]||members[model.endpoints[line*2+1]!])relevant.add(model.source_ordinals[line]!);
  const lines:number[]=[];for(let line=0;line<frame.pick_kind.length;line++)for(let at=frame.source_offsets[line]!;at<frame.source_offsets[line+1]!;at++)if(relevant.has(frame.source_edge_ordinals[at]!)){lines.push(line*2,line*2+1);break;}return new Uint32Array(lines);
}
export function worldMapFrameIncidentEdges(frame:WorldMapEdgeFrame,model:WorldMapEdges,indices:Uint32Array):Uint32Array{
  const pairs=new Set<string>();for(let i=0;i<indices.length;i+=2)pairs.add(`${indices[i]},${indices[i+1]}`);const relevant=new Set<number>();for(let line=0;line<model.source_ordinals.length;line++)if(pairs.has(`${model.endpoints[line*2]},${model.endpoints[line*2+1]}`))relevant.add(model.source_ordinals[line]!);const result:number[]=[];for(let line=0;line<frame.pick_kind.length;line++)for(let at=frame.source_offsets[line]!;at<frame.source_offsets[line+1]!;at++)if(relevant.has(frame.source_edge_ordinals[at]!)){result.push(line*2,line*2+1);break;}return new Uint32Array(result);
}
