import {readDirectStream} from './direct-stream.mjs';

const MIN_LIVE_GAP=500;
const SILENCE_TIMEOUT=18_000;
const MAX_BACKOFF=20_000;

// A transport lifetime can refresh the same Graph slice without reminting.
// Starting a new slice/session closes the old transport first.
export function openOwnerLive({
  allowedOrigin,csrf,onState,onRefresh,read=readDirectStream,
  now=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout,
}){
  let closed=false, connection=null,watchdog=null,retry=null,failures=0;
  let refreshing=false,pending=null;
  function refresh(kind){
    // Reset subsumes a concurrent changed notification.
    pending=pending==='reset'||kind==='reset'?'reset':'changed';
    if(refreshing)return;
    refreshing=true;
    void (async()=>{
      try{
        while(!closed&&pending){
          const next=pending;
          pending=null;
          await onRefresh(next);
        }
      }catch{
        if(!closed)connection?.abort();
      }finally{refreshing=false;}
    })();
  }
  function connect(){
    if(closed)return;
    retry=null;
    const attempt=new AbortController();
    connection=attempt;
    let hello=null,ready=false;
    onState('connecting');
    const arm=()=>{
      cancel(watchdog);
      watchdog=schedule(()=>attempt.abort(),SILENCE_TIMEOUT);
    };
    arm();
    void (async()=>{
      try{
        await read({allowedOrigin,csrf,signal:attempt.signal,onEvent(kind){
          if(closed||connection!==attempt||attempt.signal.aborted)return;
          if(kind==='hello'){
            hello=now();
          }else if(kind==='heartbeat'){
            if(hello!==null&&now()-hello>=MIN_LIVE_GAP){
              arm();
              if(!ready){
                ready=true; failures=0; onState('live');
              }
            }
          }else if(kind==='changed'||kind==='reset'){
            refresh(kind);
          }
        }});
      }catch{
        // Do not retain ticket, server response, or error details.
      }finally{
        if(connection===attempt){
          cancel(watchdog);watchdog=null;connection=null;
          if(!closed){
            onState('disconnected');
            const delay=Math.min(1000*2**Math.min(failures++,5),MAX_BACKOFF);
            retry=schedule(connect,delay);
          }
        }
      }
    })();
  }
  connect();
  return {close(){
    if(closed)return;
    closed=true;
    cancel(watchdog);cancel(retry);
    connection?.abort();pending=null;
  }};
}
