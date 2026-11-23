/** A genuine, completed wheel/pinch/button step consumes at most one route change. */
export class ZoomIntent {
  private pinchEntered=false;private pinchWasBusy=false;private pinchSpan=0;private startedDistance=0;private changedAt=0;private signature='';private armed=false;
  get pending():boolean{return this.armed;}
  get continuingPinch():boolean{return this.pinchSpan>0&&this.pinchEntered;}
  acceptPinchNavigation():void{if(this.pinchSpan>0)this.pinchEntered=true;}
  begin(distance:number,now:number):void{if(!this.armed)this.startedDistance=distance;this.armed=true;this.changedAt=now;}
  beginPinch(span:number,distance:number,now:number):void{this.begin(distance,now);this.pinchSpan=span;this.pinchEntered=false;this.pinchWasBusy=false;this.startedDistance=distance;}
  consumePinch(span:number,distance:number,busy:boolean):'in'|'out'|null{if(this.pinchSpan<=0)return null;if(busy||this.pinchWasBusy){this.pinchWasBusy=busy;this.pinchSpan=span;this.startedDistance=distance;return null;}const direction=span>this.pinchSpan*1.08&&distance<this.startedDistance*.94?'in':span<this.pinchSpan*.92&&distance>this.startedDistance*1.06?'out':null;if(direction){this.pinchSpan=span;this.startedDistance=distance;this.armed=false;}return direction;}
  endPinch():void{this.pinchSpan=0;this.pinchEntered=false;this.pinchWasBusy=false;}
  consume(signature:string,distance:number,now:number,busy:boolean):'in'|'out'|null{
    if(signature!==this.signature){this.signature=signature;this.changedAt=now;return null;}
    if(!this.armed||busy||now-this.changedAt<240)return null;this.armed=false;
    return distance<this.startedDistance*.94?'in':distance>this.startedDistance*1.06?'out':null;
  }
  settle(signature:string,distance:number,now:number,busy:boolean):boolean{return this.consume(signature,distance,now,busy)==='in';}
  cancel():void{this.armed=false;this.pinchSpan=0;this.pinchEntered=false;this.pinchWasBusy=false;}
}
