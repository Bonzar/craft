/** Frontend routes are ancestors, never an action-history stack. */
export type NavigationCause='zoom'|'open'|'ancestor'|'reset';
export interface MapRoute {topic_path:readonly number[];expanded_cell_ids:readonly string[]}
export function routeKey(route:MapRoute,all=false):string{return JSON.stringify([route.topic_path,route.expanded_cell_ids,all]);}
export function parentRoute(route:MapRoute):MapRoute{return route.expanded_cell_ids.length?{topic_path:[...route.topic_path],expanded_cell_ids:route.expanded_cell_ids.slice(0,-1)}:{topic_path:route.topic_path.slice(0,-1),expanded_cell_ids:[]};}
export function cellRoute(chain:readonly string[],id:string,parent:string|null):string[]{
  if(parent===null)return[id];const at=chain.indexOf(parent);return at<0?[]:[...chain.slice(0,at+1),id];
}
export interface ZoomAnchor {kind:'topic'|'cell';id:string;position:readonly[number,number,number];radius:number}
/** Bounds under focus must identify a single immediate child. Overlap is ambiguous. */
export function uniqueZoomCandidate<T>(choices:readonly T[]):T|null{return choices.length===1?choices[0]!:null;}
export function zoomLevelAction(direction:'in'|'out',candidateSpan:number|null,parentSpan:number|null,viewportSpan:number):'enter'|'leave'|null{
  if(direction==='in'&&candidateSpan!==null&&candidateSpan>viewportSpan*.65)return'enter';
  if(direction==='out'&&parentSpan!==null&&parentSpan<viewportSpan*.42)return'leave';return null;
}

/** Rank actual landmarks; broad overlapping bounds never veto the closest marker. */
export function closestZoomCandidate<T extends {distance:number;depth:number}>(choices:readonly T[]):T|null{const sorted=choices.toSorted((a,b)=>a.distance-b.distance),first=sorted[0];if(!first)return null;const tied=sorted.filter(value=>value.distance-first.distance<=2).toSorted((a,b)=>a.depth-b.depth),front=tied[0]!;return tied.length===1||tied[1]!.depth-front.depth>1e-6?front:null;}
