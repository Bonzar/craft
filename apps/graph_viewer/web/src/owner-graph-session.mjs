import {ownerGraphReconciler,readOwnerGraphWire} from './owner-graph-wire.mjs';

// One active data connection per graph view. The cached frame may paint as
// the request starts, but fresh frames are applied strictly after that paint.
export function openOwnerGraphSession({
  selector,scope=null,verifiedEpoch=null,cache,onCached,onSnapshot,onDelta,onReset,onState=()=>{},
  read=readOwnerGraphWire,schedule=setTimeout,cancel=clearTimeout,
}){
  let closed=false,attempt=null,timer=null,failures=0,accepted=null;
  let initialResolve,initialReject,initialSettled=false;
  const initial=new Promise((resolve,reject)=>{initialResolve=resolve;initialReject=reject});
  function finishInitial(error){
    if(initialSettled)return;
    initialSettled=true;
    if(error)initialReject(error);else initialResolve();
  }
  function current(signal){return !closed&&!signal.aborted;}
  async function saved(value,signal){
    if(!current(signal))return;
    accepted=value;
    finishInitial();
    // Only authorized, fully accepted renderer state is eligible for storage.
    if(scope&&value.epoch===verifiedEpoch)await cache?.save(scope,value);
  }
  function retry(){
    if(closed||timer!==null)return;
    const delay=Math.min(1000*2**Math.min(failures++,5),20_000);
    timer=schedule(()=>{timer=null;void connect();},delay);
  }
  async function connect(warm=null){
    if(closed)return;
    const ac=new AbortController();attempt=ac;const {signal}=ac;
    onState(accepted||warm?'snapshot-connecting':'connecting');
    let rejectedApply=false,live=false;
    const apply=async(action,value)=>{
      if(!current(signal))throw new DOMException('Closed','AbortError');
      try{return await action(value,signal);}
      catch(error){rejectedApply=true;throw error;}
    };
    const checkpoint=accepted||warm;
    const warmView=warm?Promise.resolve().then(()=>apply(onCached,warm)).then(value=>{
      if(current(signal)){accepted=value;finishInitial();}
    }):Promise.resolve();
    // Do not create an unhandled failure if no stream frames have arrived yet.
    void warmView.catch(()=>ac.abort());
    const reader=ownerGraphReconciler({
      checkpoint,
      onSnapshot:async value=>saved(await apply(onSnapshot,value),signal),
      onDelta:async value=>saved(await apply(onDelta,value),signal),
      onReset:async value=>{
        if(current(signal)){
          accepted=null;live=false;
          await apply(onReset,value);
          await cache?.clear();
          if(current(signal))onState('connecting');
        }
      },
      onCaughtUp:async value=>{
        if(current(signal)&&accepted){
          if(accepted.epoch!==value.epoch||accepted.revision!==value.revision)
            throw new Error('Accepted graph does not match caught-up stream');
          if(accepted.cursor!==value.cursor)await saved({...accepted,cursor:value.cursor},signal);
          if(current(signal)){live=true;failures=0;onState('live');}
        }
      },
      onHeartbeat:()=>{if(current(signal)&&live&&accepted&&reader.checkpoint())onState('live');},
    });
    try{
      await read({selector,checkpoint,signal,onEvent:async(...args)=>{
        await warmView;
        if(current(signal))await reader.accept(...args);
      }});
      await warmView;
    }catch(error){
      if(closed)return;
      if(error?.status===401||error?.status===403){
        accepted=null;closed=true;
        // Hide an already painted warm picture before asynchronous local I/O.
        onState('unauthorized');finishInitial(error);
        await cache?.clear();return;
      }
      // A rejected cached picture/delta is never an unbounded replay loop.
      if(rejectedApply){accepted=null;await cache?.clear();}
    }finally{
      if(attempt===ac)attempt=null;
    }
    if(closed)return;
    onState(accepted?'snapshot-offline':'offline');
    retry();
  }
  // An unavailable/mismatched checkpoint falls back cold on this same path.
  void (async()=>{
    const warm=scope&&typeof verifiedEpoch==='string'&&/^[a-f0-9]{64}$/.test(verifiedEpoch)?await cache?.take(scope):null;
    if(closed)return;
    if(warm?.epoch!==verifiedEpoch&&warm)void cache?.clear();
    // Authenticated session epoch is the fast local display fence, not stream
    // readiness. A newer hello/reset clears any warm paint immediately.
    void connect(warm?.epoch===verifiedEpoch?warm??null:null);
  })();
  return {initial,close(){
    if(closed)return;
    closed=true;attempt?.abort();if(timer!==null)cancel(timer);
    finishInitial();
  }};
}
