import type {Topic} from './contracts.ts';
const placeholderCaptions = new Set(['Shared scope overview','Untitled','[Untitled]','Unnamed','Без названия','Обзор']);
function wholeCaption(name:string|null|undefined):name is string{
  return !!name&&name.trim()===name&&Array.from(name).length<=80&&name.split(/\s+/u).length<=12&&!/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(name);
}
/** Display budget only. Preserve whole Source names or use neutral UI copy. */
export function sourceTopicCaption(topic:Pick<Topic,'display_title'|'naming_status'>&Partial<Pick<Topic,'has_source_naming_fields'|'label'|'full_title'>>):string{
  if(topic.naming_status==='accepted'&&wholeCaption(topic.display_title))return topic.display_title;
  if(topic.naming_status==='fallback'&&wholeCaption(topic.display_title)&&!placeholderCaptions.has(topic.display_title))return topic.display_title;
  if(topic.has_source_naming_fields===false&&topic.label===topic.full_title&&wholeCaption(topic.label)&&!placeholderCaptions.has(topic.label))return topic.label;
  return topic.naming_status==='naming_pending'?'Название готовится':'Обзор';
}

export interface CaptionBox {x:number;y:number;width:number;height:number}
export function captionBox(preferredX:number,preferredY:number,width:number,height:number,viewportWidth:number,viewportHeight:number,topInset:number,bottomInset=75):CaptionBox{
  const availableWidth=Math.max(1,viewportWidth-24);const availableHeight=Math.max(1,viewportHeight-topInset-bottomInset);
  const fittedWidth=Math.min(width,availableWidth);const fittedHeight=Math.min(height,availableHeight);
  return{x:Math.max(12,Math.min(viewportWidth-fittedWidth-12,preferredX)),y:Math.max(topInset,Math.min(viewportHeight-fittedHeight-bottomInset,preferredY)),width:fittedWidth,height:fittedHeight};
}

interface CaptionScrollMetrics {scrollHeight:number;clientHeight:number;scrollWidth:number;clientWidth:number;overflowY:string;overflowX:string}
export function captionHasNativeOverflow(box:CaptionScrollMetrics):boolean{
  return ((box.overflowY==='auto'||box.overflowY==='scroll')&&box.scrollHeight>box.clientHeight+1)||((box.overflowX==='auto'||box.overflowX==='scroll')&&box.scrollWidth>box.clientWidth+1);
}

export interface CaptionAnchor {center:[number,number,number];radius:number}
/** The same indexed worker bounds drive the visible shell and its caption. */
export function envelopeCaptionAnchor(slot:number|undefined,centers:ArrayLike<number>,radii:ArrayLike<number>):CaptionAnchor|null{
  if(slot===undefined||!Number.isInteger(slot)||slot<0)return null;
  const center:[number,number,number]=[centers[slot*3]??NaN,centers[slot*3+1]??NaN,centers[slot*3+2]??NaN];const radius=radii[slot]??NaN;
  return center.every(Number.isFinite)&&Number.isFinite(radius)&&radius>0?{center,radius}:null;
}
export interface CaptionLeader {from:{x:number;y:number};to:{x:number;y:number};length:number;angle:number}
/** A UI connector ends on the placed caption, never on an assertion or edge. */
export function captionLeader(anchor:{x:number;y:number},box:CaptionBox):CaptionLeader|null{
  if(![anchor.x,anchor.y,box.x,box.y,box.width,box.height].every(Number.isFinite)||box.width<=0||box.height<=0)return null;
  const x=Math.max(box.x,Math.min(box.x+box.width,anchor.x)),y=Math.max(box.y,Math.min(box.y+box.height,anchor.y));
  const dx=x-anchor.x,dy=y-anchor.y,length=Math.hypot(dx,dy);return length>.5?{from:{...anchor},to:{x,y},length,angle:Math.atan2(dy,dx)}:null;
}

export type CaptionSide='left'|'right';
export interface CaptionSideRegions {protected:CaptionBox;left:CaptionBox;right:CaptionBox;maxPerSide:number;leaderProtected:CaptionBox;maxLeaderLength:number}
/** Side cards never borrow the central reading window, including on narrow phones. */
export function captionSideRegions(width:number,height:number,top:number,bottom=120):CaptionSideRegions{const fraction=width<700?.30:.25,first=width*fraction,last=width*(1-fraction),available=Math.max(0,height-top-bottom),cardWidth=Math.max(0,Math.min(220,first-16));return{protected:{x:first,y:top,width:last-first,height:available},left:{x:Math.max(8,first-8-cardWidth),y:top,width:cardWidth,height:available},right:{x:last+8,y:top,width:cardWidth,height:available},maxPerSide:3,leaderProtected:{x:first+Math.min(36,(last-first)/4),y:top,width:last-first-2*Math.min(36,(last-first)/4),height:available},maxLeaderLength:width<700?120:160};}
export function captionPointSide(point:{x:number;y:number},regions:CaptionSideRegions):CaptionSide|null{return point.x<regions.leaderProtected.x?'left':point.x>regions.leaderProtected.x+regions.leaderProtected.width?'right':null;}
export function captionBoxesOverlap(a:CaptionBox,b:CaptionBox):boolean{return a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y;}
function segmentTouchesBox(from:{x:number;y:number},to:{x:number;y:number},box:CaptionBox):boolean{let low=0,high=1;for(const [start,delta,min,max]of [[from.x,to.x-from.x,box.x,box.x+box.width],[from.y,to.y-from.y,box.y,box.y+box.height]]){if(Math.abs(delta!)<1e-9){if(start!<min!||start!>max!)return false;continue;}let a=(min!-start!)/delta!,b=(max!-start!)/delta!;if(a>b)[a,b]=[b,a];low=Math.max(low,a);high=Math.min(high,b);if(low>high)return false;}return true;}
/** A short oblique association to an actually visible sphere surface point. */
export function diagonalCaptionLeader(anchor:{x:number;y:number},box:CaptionBox,maxLength=120):CaptionLeader|null{const targets:{x:number;y:number}[]=[];for(const fraction of[0,.25,.5,.75,1]){if(anchor.y<box.y)targets.push({x:box.x+box.width*fraction,y:box.y});if(anchor.y>box.y+box.height)targets.push({x:box.x+box.width*fraction,y:box.y+box.height});if(anchor.x<box.x)targets.push({x:box.x,y:box.y+box.height*fraction});if(anchor.x>box.x+box.width)targets.push({x:box.x+box.width,y:box.y+box.height*fraction});}let best:CaptionLeader|null=null;for(const to of targets){const dx=to.x-anchor.x,dy=to.y-anchor.y,length=Math.hypot(dx,dy),slope=Math.atan2(Math.abs(dy),Math.abs(dx))*180/Math.PI;if(length<12||length>maxLength||slope<20||slope>70)continue;if(!best||length<best.length)best={from:{...anchor},to,length,angle:Math.atan2(dy,dx)};}return best;}
export function captionSideSafe(point:{x:number;y:number},box:CaptionBox,side:CaptionSide,regions:CaptionSideRegions,blocked:readonly (readonly[number,number,number,number])[]=[]):boolean{const lane=regions[side];if(captionPointSide(point,regions)!==side||box.width<=0||box.height<=0||box.x<lane.x-.01||box.x+box.width>lane.x+lane.width+.01||box.y<lane.y-.01||box.y+box.height>lane.y+lane.height+.01||captionBoxesOverlap(box,regions.protected))return false;const leader=diagonalCaptionLeader(point,box,regions.maxLeaderLength);if(!leader||segmentTouchesBox(leader.from,leader.to,regions.leaderProtected))return false;for(const rect of blocked){const obstruction={x:rect[0],y:rect[1],width:rect[2]-rect[0],height:rect[3]-rect[1]};if(captionBoxesOverlap(box,obstruction)||leader&&segmentTouchesBox(leader.from,leader.to,obstruction))return false;}return true;}
export function sideCaptionBoxes(regions:CaptionSideRegions,side:CaptionSide,height:number,anchorY:number,previous?:CaptionBox,blocked:readonly (readonly[number,number,number,number])[]=[]):CaptionBox[]{const lane=regions[side];if(height>lane.height||lane.width<44)return[];const ys=[...(previous?[previous.y]:[]),anchorY-height-36,anchorY+36,anchorY-height-72,anchorY+72,lane.y,lane.y+(lane.height-height)/2,lane.y+lane.height-height];for(const rect of blocked)ys.push(rect[3]+8,rect[1]-height-8);const result:CaptionBox[]=[];for(const y of ys){const box={x:lane.x,y:Math.max(lane.y,Math.min(lane.y+lane.height-height,y)),width:lane.width,height};if(!result.some(old=>Math.abs(old.y-box.y)<1))result.push(box);}return result;}
