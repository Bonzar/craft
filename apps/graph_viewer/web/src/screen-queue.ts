import type { ScreenConnectivityResult } from './viewport-connections.ts';
import type { ViewBinding } from './focus.ts';
export interface ScreenMetricRequest extends ViewBinding {view_projection:Float32Array;viewport:readonly[number,number]}
export interface ScreenMetricReply extends ScreenConnectivityResult {view_projection:Float32Array;viewport:readonly[number,number]}
type Query=(request:ScreenMetricRequest)=>Promise<ScreenMetricReply>;
interface Task {query:Query;request:ScreenMetricRequest}
/** One worker computation plus the newest queued camera. Old-camera results never reach GPU attributes. */
export class ScreenMetricQueue {
  private revision:string|null=null;private lod=-1;private latest=-1;private running=false;private pending:Task|null=null;
  private apply:(result:ScreenMetricReply)=>void;private error:(reason:unknown)=>void;
  constructor(apply:(result:ScreenMetricReply)=>void,error:(reason:unknown)=>void){this.apply=apply;this.error=error;}
  bind(revision:string,lod:number):void{if(this.revision===revision&&this.lod===lod)return;this.revision=revision;this.lod=lod;this.latest=-1;this.pending=null;}
  request(query:Query,request:ScreenMetricRequest):void{
    if(request.revision!==this.revision||request.lod_generation!==this.lod||request.camera_generation<=this.latest)return;
    this.latest=request.camera_generation;this.pending={query,request};this.pump();
  }
  private current(task:Task,result?:ScreenMetricReply):boolean{
    return this.revision===task.request.revision&&this.lod===task.request.lod_generation&&this.latest===task.request.camera_generation&&(!result||(result.revision===this.revision&&result.lod_generation===this.lod&&result.camera_generation===this.latest));
  }
  private pump():void{
    if(this.running||!this.pending)return;const task=this.pending;this.pending=null;this.running=true;
    let computation:Promise<ScreenMetricReply>;try{computation=task.query(task.request);}catch(error){this.running=false;if(this.current(task))this.error(error);if(this.pending)this.pump();return;}
    computation.then(result=>{if(this.current(task,result))this.apply(result);}).catch(error=>{if(this.current(task))this.error(error);}).finally(()=>{this.running=false;if(this.pending)this.pump();});
  }
  dispose():void{this.revision=null;this.lod=-1;this.latest=-1;this.pending=null;}
}
