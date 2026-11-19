import type { LodResult } from './lod.ts';

/** Immutable accepted LOD inputs. Canonical slots are 0..N-1; proxies are N+i. */
export type ViewportLod = Pick<LodResult,'revision'|'generation'|'node_render_visible'|'proxies'|'render_positions'|'render_edges'|'edge_pick_kind'|'edge_pick_target'|'bundle_offsets'>;
export interface ScreenConnectivityResult {
  revision:string;lod_generation:number;camera_generation:number;onscreen:Uint8Array;
  external_visible:Uint32Array;canonical_self:Uint32Array;proxy_internal:Uint32Array;
  star_intensity:Float32Array;proxy_intensity:Float32Array;unique_external_edges:number;
  stats:{onscreen_stars:number;onscreen_proxies:number;star_scale_cap:number;proxy_scale_cap:number};
  diagnostics:{changed_representatives:number;visited_projected_edges:number;full_rebuilds:number};
}
interface EdgePreparation {weights:Uint32Array;rowCounts:Uint32Array;self:Uint32Array}
type Diagnostics=ScreenConnectivityResult['diagnostics'];

function prepareEdges(lod:ViewportLod,active:Uint8Array,nodeCount:number):EdgePreparation {
  const lines=lod.render_edges.length/2;
  if(!Number.isInteger(lines)||lod.edge_pick_kind.length!==lines||lod.edge_pick_target.length!==lines)throw new RangeError('Invalid LOD edge buffers');
  const weights=new Uint32Array(lines);const rowCounts=new Uint32Array(active.length);const self=new Uint32Array(active.length);
  for(let edge=0;edge<lines;edge++){
    const from=lod.render_edges[edge*2]??active.length;const to=lod.render_edges[edge*2+1]??active.length;
    if(!active[from]||!active[to])throw new RangeError('LOD edge endpoint is not an active representative');
    const kind=lod.edge_pick_kind[edge];let weight=1;
    if(kind===1){
      const bundle=lod.edge_pick_target[edge]??lod.bundle_offsets.length;
      const start=lod.bundle_offsets[bundle];const end=lod.bundle_offsets[bundle+1];
      if(start===undefined||end===undefined||end<=start)throw new RangeError('Invalid LOD bundle weight');
      weight=end-start;
    }else if(kind!==0)throw new RangeError('Invalid LOD edge category');
    weights[edge]=weight;
    if(from===to){
      if(from>=nodeCount)throw new RangeError('Proxy self edges must use prepared internal counts');
      self[from]=(self[from]??0)+weight;
    }else{rowCounts[from]=(rowCounts[from]??0)+1;rowCounts[to]=(rowCounts[to]??0)+1;}
  }
  return {weights,rowCounts,self};
}

function buildCsr(edges:Uint32Array,rowCounts:Uint32Array):{offsets:Uint32Array;incident:Uint32Array} {
  const offsets=new Uint32Array(rowCounts.length+1);let total=0;
  for(let row=0;row<rowCounts.length;row++){
    total+=rowCounts[row]??0;
    if(total>0xffffffff)throw new RangeError('Representative adjacency exceeds uint32 capacity');
    offsets[row+1]=total;
  }
  const incident=new Uint32Array(total);const cursors=offsets.slice(0,-1);
  for(let edge=0;edge<edges.length/2;edge++){
    const from=edges[edge*2]??0;const to=edges[edge*2+1]??0;if(from===to)continue;
    const fromAt=cursors[from]??0;const toAt=cursors[to]??0;
    incident[fromAt]=edge;incident[toAt]=edge;cursors[from]=fromAt+1;cursors[to]=toAt+1;
  }
  return {offsets,incident};
}

/** Positive-count robust cap. Classes are deliberately normalized separately. */
function scaleCap(values:Uint32Array,onscreen:Uint8Array,start:number,end:number):number {
  const positives=new Uint32Array(end-start);let count=0;
  for(let slot=start;slot<end;slot++)if(onscreen[slot]&&(values[slot]??0)>0)positives[count++]=values[slot]??0;
  if(!count)return 1;
  const sorted=positives.subarray(0,count).sort();const middle=Math.floor(count/2);
  const median=count%2?(sorted[middle]??1):((sorted[middle-1]??1)+(sorted[middle]??1))/2;
  const p95=sorted[Math.ceil(count*.95)-1]??1;
  return Math.max(1,Math.min(p95,Math.max(4,4*median)));
}
function intensity(count:number,cap:number):number{return Math.min(1,Math.max(0,Math.log1p(count)/Math.log1p(cap)));}

/** Build once per accepted LOD, then call update only for coalesced camera snapshots.
 * Returned arrays are owned snapshots: worker transfer cannot detach this CSR/index.
 * Internal proxy counts remain cached for offscreen proxies; onscreen controls display.
 */
export class ViewportConnections {
  readonly revision:string;readonly lod_generation:number;readonly representativeCount:number;readonly sourceNodeCount:number;
  readonly positions:Float32Array;readonly active:Uint8Array;
  private readonly edges:Uint32Array;private readonly weights:Uint32Array;private readonly offsets:Uint32Array;private readonly incident:Uint32Array;
  private readonly selfCounts:Uint32Array;private readonly internalCounts:Uint32Array;private readonly counts:Uint32Array;private readonly stamps:Uint32Array;
  private mask:Uint8Array;private epoch=0;private unique=0;private lastCamera=-1;
  constructor(lod:ViewportLod,sourceNodeCount:number){
    const count=sourceNodeCount+lod.proxies.length;
    if(!Number.isSafeInteger(sourceNodeCount)||sourceNodeCount<0||lod.node_render_visible.length!==sourceNodeCount||lod.render_positions.length!==count*3)throw new RangeError('Invalid LOD representative dimensions');
    this.sourceNodeCount=sourceNodeCount;this.revision=lod.revision;this.lod_generation=lod.generation;
    this.positions=lod.render_positions;this.representativeCount=count;this.active=new Uint8Array(count);
    for(let node=0;node<sourceNodeCount;node++)this.active[node]=lod.node_render_visible[node]?1:0;
    this.active.fill(1,sourceNodeCount);
    const prepared=prepareEdges(lod,this.active,sourceNodeCount);const csr=buildCsr(lod.render_edges,prepared.rowCounts);
    this.edges=lod.render_edges;this.weights=prepared.weights;this.selfCounts=prepared.self;
    this.offsets=csr.offsets;this.incident=csr.incident;this.counts=new Uint32Array(count);this.stamps=new Uint32Array(this.weights.length);
    this.mask=new Uint8Array(count);this.internalCounts=new Uint32Array(count);
    for(let proxy=0;proxy<lod.proxies.length;proxy++){
      const internal=lod.proxies[proxy]?.internal_edge_count??0;
      if(!Number.isSafeInteger(internal)||internal<0||internal>0xffffffff)throw new RangeError('Invalid proxy internal count');
      this.internalCounts[sourceNodeCount+proxy]=internal;
    }
  }
  update(onscreenRepresentatives:Uint32Array,cameraGeneration:number):ScreenConnectivityResult {
    if(!Number.isSafeInteger(cameraGeneration)||cameraGeneration<0||cameraGeneration<this.lastCamera)throw new RangeError('Stale or invalid camera generation');
    const next=new Uint8Array(this.representativeCount);
    for(const representative of onscreenRepresentatives){
      if(representative>=next.length)throw new RangeError('Invalid onscreen representative');
      if(this.active[representative])next[representative]=1;
    }
    const changed=new Uint32Array(next.length);let changedCount=0;let work=0;
    for(let slot=0;slot<next.length;slot++)if(next[slot]!==this.mask[slot]){
      changed[changedCount++]=slot;work+=(this.offsets[slot+1]??0)-(this.offsets[slot]??0);
    }
    const diagnostics=work>this.weights.length*1.25?this.recompute(next,changedCount):this.increment(next,changed.subarray(0,changedCount));
    this.mask=next;this.lastCamera=cameraGeneration;
    return this.result(cameraGeneration,diagnostics);
  }
  private add(edge:number,sign:number):void {
    const from=this.edges[edge*2]??0;const to=this.edges[edge*2+1]??0;const amount=(this.weights[edge]??0)*sign;
    this.counts[from]=(this.counts[from]??0)+amount;this.counts[to]=(this.counts[to]??0)+amount;this.unique+=amount;
  }
  private recompute(next:Uint8Array,changedCount:number):Diagnostics {
    this.counts.fill(0);this.unique=0;
    for(let edge=0;edge<this.weights.length;edge++){
      const from=this.edges[edge*2]??0;const to=this.edges[edge*2+1]??0;
      if(from!==to&&next[from]&&next[to])this.add(edge,1);
    }
    return {changed_representatives:changedCount,visited_projected_edges:this.weights.length,full_rebuilds:1};
  }
  private increment(next:Uint8Array,changed:Uint32Array):Diagnostics {
    this.epoch=(this.epoch+1)>>>0;if(!this.epoch){this.stamps.fill(0);this.epoch=1;}
    let visited=0;
    for(const slot of changed)for(let at=this.offsets[slot]??0;at<(this.offsets[slot+1]??0);at++){
      const edge=this.incident[at]??0;if(this.stamps[edge]===this.epoch)continue;
      this.stamps[edge]=this.epoch;visited++;
      const from=this.edges[edge*2]??0;const to=this.edges[edge*2+1]??0;
      const before=Boolean(this.mask[from]&&this.mask[to]);const after=Boolean(next[from]&&next[to]);
      if(before!==after)this.add(edge,after?1:-1);
    }
    return {changed_representatives:changed.length,visited_projected_edges:visited,full_rebuilds:0};
  }
  private result(cameraGeneration:number,diagnostics:Diagnostics):ScreenConnectivityResult {
    const count=this.representativeCount;const activity=new Uint32Array(count);const canonicalSelf=new Uint32Array(count);
    let stars=0;let proxies=0;
    for(let slot=0;slot<count;slot++){
      if(slot>=this.sourceNodeCount)activity[slot]=(this.counts[slot]??0)+(this.internalCounts[slot]??0);
      if(!this.mask[slot])continue;
      if(slot<this.sourceNodeCount){stars++;canonicalSelf[slot]=this.selfCounts[slot]??0;}else proxies++;
    }
    const starCap=scaleCap(this.counts,this.mask,0,this.sourceNodeCount);const proxyCap=scaleCap(activity,this.mask,this.sourceNodeCount,count);
    const starIntensity=new Float32Array(count);const proxyIntensity=new Float32Array(count);
    for(let slot=0;slot<count;slot++)if(this.mask[slot]){
      if(slot<this.sourceNodeCount)starIntensity[slot]=intensity(this.counts[slot]??0,starCap);
      else proxyIntensity[slot]=intensity(activity[slot]??0,proxyCap);
    }
    return {revision:this.revision,lod_generation:this.lod_generation,camera_generation:cameraGeneration,
      onscreen:this.mask.slice(),external_visible:this.counts.slice(),canonical_self:canonicalSelf,proxy_internal:this.internalCounts.slice(),
      star_intensity:starIntensity,proxy_intensity:proxyIntensity,unique_external_edges:this.unique,
      stats:{onscreen_stars:stars,onscreen_proxies:proxies,star_scale_cap:starCap,proxy_scale_cap:proxyCap},diagnostics};
  }
}
export function buildViewportConnections(lod:ViewportLod,sourceNodeCount:number):ViewportConnections{return new ViewportConnections(lod,sourceNodeCount);}
export function acceptsScreenConnectivity(revision:string,lodGeneration:number,lastCameraGeneration:number,result:ScreenConnectivityResult):boolean {
  return result.revision===revision&&result.lod_generation===lodGeneration&&Number.isSafeInteger(result.camera_generation)&&result.camera_generation>=lastCameraGeneration;
}
