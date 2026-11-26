/** Single-flight, authenticated manifest probe. No background-tab requests. */
export const POLL_INTERVAL_MS=3000,SSE_START_MS=4500,SSE_IDLE_MS=15000;

export class LiveFallback {
  generation=0;
  timer;
  request;
  listener;
  stop() {
    ++this.generation;
    if(this.timer!==undefined)clearTimeout(this.timer);
    this.timer=undefined;
    this.request?.abort();this.request=undefined;
    if(this.listener)document.removeEventListener('visibilitychange',this.listener);
    this.listener=undefined;
  }
  start(tick, failed, interval=POLL_INTERVAL_MS) {
    this.stop();
    const epoch=this.generation;
    const schedule=(delay)=>{
      if(epoch!==this.generation||document.hidden||this.request||this.timer!==undefined)return;
      this.timer=setTimeout(()=>{
        this.timer=undefined;
        if(epoch!==this.generation||document.hidden)return;
        const request=new AbortController();
        this.request=request;
        const deadline=setTimeout(()=>request.abort('manifest-probe-timeout'),5000);
        Promise.resolve().then(()=>tick(request.signal)).catch(error=>{
          if(epoch===this.generation&&!document.hidden&&(!request.signal.aborted||request.signal.reason==='manifest-probe-timeout'))
            failed(error);
        }).finally(()=>{
          clearTimeout(deadline);
          if(this.request===request)this.request=undefined;
          schedule(interval);
        });
      },delay);
    };
    this.listener=()=>{
      if(document.hidden){
        if(this.timer!==undefined)clearTimeout(this.timer);
        this.timer=undefined;this.request?.abort();
      }else schedule(0);
    };
    document.addEventListener('visibilitychange',this.listener);
    schedule(0);
  }
}
