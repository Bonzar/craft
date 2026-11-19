import { temporalSelector } from './logic.ts';
import type { Selector } from './logic.ts';
export interface SliceContext {selector:Selector;day:string;zone:string}
function instant(value:string|null):string|null{if(value===null)return null;const time=Date.parse(value);return Number.isFinite(time)?new Date(time).toISOString():value;}
export function sameSliceContext(left:SliceContext,right:SliceContext):boolean{
  const a=left.selector,b=right.selector;
  return a.mode===b.mode&&instant(a.at)===instant(b.at)&&a.history===b.history&&a.undated===b.undated&&left.day===right.day&&left.zone===right.zone;
}
export function contextForSelector(previous:SliceContext,selector:Selector):SliceContext{
  if(selector.mode==='current'||!selector.at)return{selector:{...selector},day:previous.day,zone:previous.zone};
  const at=instant(selector.at);let zone=previous.zone;let day=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(selector.at));
  for(const candidate of new Set([previous.zone,'UTC','Europe/Moscow'])){
    const localDay=new Intl.DateTimeFormat('en-CA',{timeZone:candidate,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(selector.at));
    if(instant(temporalSelector(selector.mode,localDay,candidate,selector.history,selector.undated).at)===at){zone=candidate;day=localDay;break;}
  }
  return{selector:{...selector},day,zone};
}
