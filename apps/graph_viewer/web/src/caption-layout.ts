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
