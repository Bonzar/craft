export const POLL_INTERVAL_MS:number,SSE_START_MS:number,SSE_IDLE_MS:number;
export class LiveFallback {
  stop():void;
  start(tick:(signal:AbortSignal)=>Promise<void>,failed:(error:unknown)=>void,interval?:number):void;
}
