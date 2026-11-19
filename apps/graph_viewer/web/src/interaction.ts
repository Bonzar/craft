export interface InteractionStamp {revision:string;generation:number}
export interface IncidentGroups {endpoints:Uint32Array;multiplicities:Uint32Array;edgeCount:number;internalEdgeCount:number}
export function interactionIsCurrent(pressed:InteractionStamp|null,revision:string,generation:number):boolean{return pressed!==null&&pressed.revision===revision&&pressed.generation===generation;}
export function pinRevisionCallback(boundRevision:string,currentRevision:()=>string|null,open:(ordinal:number)=>void,stale:()=>void):(ordinal:number)=>void{
  return ordinal=>{if(currentRevision()!==boundRevision){stale();return;}open(ordinal);};
}
interface SnapshotIdentity {provider_id:string;revision:string}
export function sourceResponseIsCurrent(bound:SnapshotIdentity,current:SnapshotIdentity|null,aborted:boolean):boolean{
  return !aborted&&current!==null&&bound.provider_id===current.provider_id&&bound.revision===current.revision;
}
export function pinSnapshotCallback(bound:SnapshotIdentity,current:()=>SnapshotIdentity|null,open:(id:string)=>void,stale:()=>void):(id:string)=>void{
  const provider=bound.provider_id,revision=bound.revision;
  return id=>{const active=current();if(!active||active.provider_id!==provider||active.revision!==revision){stale();return;}open(id);};
}
export function memberVisibilityLabel(index:number,visible:boolean):string{return `Утверждение ${index+1} · ${visible?'в срезе':'скрыто в срезе'}`;}
export function groupIncidentRepresentatives(indices:Uint32Array,representatives:Int32Array):IncidentGroups{
  const groups=new Map<string,{from:number;to:number;count:number}>();let edgeCount=0;let internalEdgeCount=0;
  for(let index=0;index<indices.length;index+=2){const from=representatives[indices[index]??-1]??-1;const to=representatives[indices[index+1]??-1]??-1;if(from<0||to<0)continue;edgeCount++;if(from===to){internalEdgeCount++;continue;}const key=`${from}:${to}`;const found=groups.get(key);if(found)found.count++;else groups.set(key,{from,to,count:1});}
  const endpoints=new Uint32Array(groups.size*2);const multiplicities=new Uint32Array(groups.size);let cursor=0;
  for(const group of groups.values()){endpoints[cursor*2]=group.from;endpoints[cursor*2+1]=group.to;multiplicities[cursor]=group.count;cursor++;}
  return{endpoints,multiplicities,edgeCount,internalEdgeCount};
}

export interface MapGestureStamp extends InteractionStamp {provider_id:string}
export interface MapTap extends MapGestureStamp {x:number;y:number;maxDistance:number;cancelled:boolean}
export function createMapTap(stamp:MapGestureStamp,x:number,y:number):MapTap{return{...stamp,x,y,maxDistance:0,cancelled:false};}
export function moveMapTap(tap:MapTap,x:number,y:number):void{tap.maxDistance=Math.max(tap.maxDistance,Math.hypot(x-tap.x,y-tap.y));if(tap.maxDistance>6)tap.cancelled=true;}
export function mapTapIsCurrent(tap:MapTap|null,current:MapGestureStamp):boolean{return tap!==null&&!tap.cancelled&&tap.maxDistance<=6&&tap.provider_id===current.provider_id&&tap.revision===current.revision&&tap.generation===current.generation;}
