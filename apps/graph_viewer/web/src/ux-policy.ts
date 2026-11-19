export type Rect=readonly[number,number,number,number];
type Vec3=readonly[number,number,number];
export interface SelectableShell {topic_id:string;topic_index:number;center:Vec3;radius:number;screen:{x:number;y:number;radius:number}}
export type ShellChoice={kind:'shell';topic_id:string;topic_index:number}|{kind:'ambiguous';topic_ids:string[]};
function intersectsShell(origin:Vec3,direction:Vec3,shell:SelectableShell):boolean{const x=origin[0]-shell.center[0],y=origin[1]-shell.center[1],z=origin[2]-shell.center[2];const b=x*direction[0]+y*direction[1]+z*direction[2];const discriminant=b*b-(x*x+y*y+z*z-shell.radius*shell.radius);return discriminant>=0&&-b+Math.sqrt(discriminant)>0;}
export function pickShell(shells:readonly SelectableShell[],origin:Vec3,direction:Vec3,point:readonly[number,number],viewportMinimum:number):ShellChoice|null{
  const hits:{shell:SelectableShell;rim:number;tier:number}[]=[];
  for(const shell of shells){if(shell.radius<=0||!intersectsShell(origin,direction,shell))continue;const distance=Math.hypot(point[0]-shell.screen.x,point[1]-shell.screen.y);const rim=Math.abs(distance-shell.screen.radius);if(distance>shell.screen.radius+12)continue;const tier=rim<=12?0:1;if(tier&&shell.screen.radius>viewportMinimum*.65)continue;hits.push({shell,rim,tier});}
  hits.sort((a,b)=>a.tier-b.tier||(a.tier===0?a.rim-b.rim:a.shell.screen.radius-b.shell.screen.radius)||a.shell.topic_id.localeCompare(b.shell.topic_id));const first=hits[0];if(!first)return null;
  const indistinguishable=hits.filter(hit=>hit.tier===first.tier&&Math.abs(hit.rim-first.rim)<=3&&Math.abs(hit.shell.screen.radius-first.shell.screen.radius)<=Math.max(3,first.shell.screen.radius*.06));
  if(indistinguishable.length>1)return{kind:'ambiguous',topic_ids:indistinguishable.map(hit=>hit.shell.topic_id).toSorted()};return{kind:'shell',topic_id:first.shell.topic_id,topic_index:first.shell.topic_index};
}
export function boundedShellIndices(candidates:Uint32Array,selected:number|null,budget:number):Uint32Array{const result:number[]=[];const seen=new Set<number>();for(const index of candidates){if(seen.has(index))continue;seen.add(index);if(result.length<budget)result.push(index);}if(selected!==null&&!result.includes(selected))result.push(selected);return new Uint32Array(result);}
export function readingPolicy(readerOpen:boolean,width:number):{near_previews:number;compact_labels:number;unselected_shells:number}{return{near_previews:readerOpen?0:1,compact_labels:width<700?3:6,unselected_shells:width<700?3:6};}
export class OccupiedRects{
  private occupied:Rect[];
  constructor(rectangles:readonly Rect[]=[]){this.occupied=[...rectangles];}
  reserve(rectangle:Rect):void{this.occupied.push(rectangle);}
  place(candidates:readonly Rect[]):Rect|null{for(const rect of candidates){if(this.occupied.some(other=>rect[0]<other[2]+2&&rect[2]>other[0]-2&&rect[1]<other[3]+2&&rect[3]>other[1]-2))continue;this.occupied.push(rect);return rect;}return null;}
}
