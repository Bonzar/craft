export type LifecycleFields=Record<string,number|string|boolean|null>;
export interface LifecycleEvent {at:number;kind:string;fields:LifecycleFields}
export function opaqueTag(value:string):string{let hash=2166136261;for(const char of value)hash=Math.imul(hash^char.charCodeAt(0),16777619);return(hash>>>0).toString(16);}
export class LifecycleTrace {
  private events:LifecycleEvent[]=[];private totals:Record<string,number>={};
  record(kind:string,fields:LifecycleFields={}):void{this.totals[kind]=(this.totals[kind]??0)+1;this.events.push({at:performance.now(),kind,fields});if(this.events.length>256)this.events.shift();}
  snapshot():{timeOrigin:number;totals:Record<string,number>;events:LifecycleEvent[]}{return{timeOrigin:performance.timeOrigin,totals:{...this.totals},events:this.events.map(event=>({...event,fields:{...event.fields}}))};}
}
/** Diagnostic metadata only: never pass source text, source titles, URLs, cookies or tokens. */
export function lifecycle(kind:string,fields:LifecycleFields={}):void{window.dispatchEvent(new CustomEvent('graph-viewer-lifecycle',{detail:{kind,fields}}));}
