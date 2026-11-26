import {readDirectStream,readSameOriginOwnerStream} from './direct-stream.mjs';

const MIN_LIVE_GAP=500;
const SILENCE_TIMEOUT=18_000;
const FIRST_HEARTBEAT_TIMEOUT=12_000;
const MAX_BACKOFF=20_000;

// A transport lifetime can refresh the same Graph slice without reminting.
// Starting a new slice/session closes the old transport first.
export function openOwnerLive({
  allowedOrigin,csrf,onState,onRefresh,onInvalidate=()=>{},initialEpoch=null,onBootstrap=()=>false,transport='direct',read=transport==='ordinary'?readSameOriginOwnerStream:readDirectStream,
  visibility=globalThis.document,page=globalThis.window,
  now=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout,
}){
  let closed=false, connection=null,watchdog=null,retry=null,resume=null,failures=0;
  let refreshing=false,pending=null,pendingEpoch=null,epoch=initialEpoch;
  let hidden=visibility?.visibilityState==='hidden';
  function foreground(){
    if(closed||resume!==null||visibility?.visibilityState==='hidden')return;
    resume=schedule(()=>{resume=null;if(!closed&&visibility?.visibilityState!=='hidden')refresh('changed');},0);
  }
  function visibilityChanged(){
    if(visibility.visibilityState==='hidden'){hidden=true;return;}
    if(hidden){hidden=false;foreground();}
  }
  function pageShown(event){if(event.persisted){hidden=false;foreground();}}
  visibility?.addEventListener('visibilitychange',visibilityChanged);
  page?.addEventListener('pageshow',pageShown);
  function refresh(kind,retiredEpoch=null){
    // Hard reset hides old epoch synchronously, even if catch-up is in flight.
    if(kind==='reset'&&pending!=='reset')onInvalidate(retiredEpoch);
    pending=pending==='reset'||kind==='reset'?'reset':'changed';pendingEpoch=epoch;
    if(refreshing)return;
    refreshing=true;
    void (async()=>{
      try{
        while(!closed&&pending){
          const next=pending,authority=pendingEpoch;
          pending=null;pendingEpoch=null;
          await onRefresh(next,authority);
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
    let hello=null,ready=false,firstReset=true,retiredEpoch=null;
    onState('connecting');
    const arm=(timeout=SILENCE_TIMEOUT)=>{
      cancel(watchdog);
      watchdog=schedule(()=>attempt.abort(),timeout);
    };
    arm();
    void (async()=>{
      try{
        await read({allowedOrigin,csrf,signal:attempt.signal,onEvent(kind,ticketEpoch){
          if(closed||connection!==attempt||attempt.signal.aborted)return;
          if(kind==='hello'){
            hello=now();
            if(ticketEpoch){
              retiredEpoch=epoch!==null&&epoch!==ticketEpoch?epoch:null;
              epoch=ticketEpoch;
            }
          }else if(kind==='heartbeat'){
            if(hello!==null&&now()-hello>=MIN_LIVE_GAP){
              arm();
              if(!ready){
                ready=true; failures=0; onState('live');
              }
            }
          }else if(kind==='reset'){
            // First reset is a catch-up hint, not a database clear. Every later
            // reset on this protocol is a hard epoch transition and closes it.
            if(firstReset){
              const accepted=!retiredEpoch&&epoch&&onBootstrap(epoch)===true;
              // The exact epoch-bound initial handshake is registration. This
              // runs before the snapshot loader microtask; heartbeat measures
              // continued freshness rather than delaying the first live badge.
              if(accepted){onState('registered');arm(FIRST_HEARTBEAT_TIMEOUT);}
              else refresh(retiredEpoch?'reset':'changed',retiredEpoch);
            }
            else{epoch=null;refresh('reset',ticketEpoch);}
            firstReset=false;
          }else if(kind==='changed'){
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
    visibility?.removeEventListener('visibilitychange',visibilityChanged);
    page?.removeEventListener('pageshow',pageShown);
    cancel(watchdog);cancel(retry);cancel(resume);
    connection?.abort();pending=null;
  }};
}

// First protected catch-up wins over fallback. Both routes use one snapshot
// loader; a late lease cannot start another render in the same bootstrap.
export function firstOwnerSnapshot(load,{waitMs=1500,schedule=setTimeout,cancel=clearTimeout}={}){
  let used=false,timer,resolve,reject;
  const ready=new Promise((yes,no)=>{resolve=yes;reject=no});
  const run=epoch=>{
    if(used)return false;
    used=true;cancel(timer);
    Promise.resolve().then(()=>load(epoch)).then(resolve,reject);
    return true;
  };
  timer=schedule(()=>run(null),waitMs);
  return {ready,accept:epoch=>typeof epoch==='string'&&/^[a-f0-9]{64}$/.test(epoch)&&run(epoch),
    fallback:()=>run(null),close(){if(!used){used=true;cancel(timer);resolve();}}};
}
