import {readDirectStream} from './direct-stream.mjs';

// Diagnostic state is limited to timing and fixed labels, and yields no token.
export async function proveDirectStream({allowedOrigin,csrf,fetcher=fetch,
  now=()=>performance.now(),timeoutMs=45_000,update=()=>{}}){
  const controller=new AbortController();
  const start=now();
  let helloMs,heartbeatMs,initialReset=false;
  const timer=setTimeout(()=>controller.abort(),timeoutMs);
  try{
    update({phase:'connecting'});
    await readDirectStream({fetcher,csrf,allowedOrigin,signal:controller.signal,onEvent(kind){
      const elapsed=Math.round(now()-start);
      if(kind==='hello'){
        helloMs=elapsed;
        update({phase:'hello',helloMs});
      }else if(kind==='heartbeat'&&helloMs!==undefined&&elapsed-helloMs>=500){
        heartbeatMs=elapsed;
        controller.abort();
      }else if(kind==='reset'){
        if(initialReset)controller.abort();
        initialReset=true;
      }
    }});
    if(heartbeatMs!==undefined){
      const result={phase:'proven',helloMs,heartbeatMs};
      update(result);
      return result;
    }
  }catch{
    // Do not surface URLs, server responses, headers or raw errors.
  }finally{
    clearTimeout(timer);
  }
  const result={phase:'not_proven'};
  update(result);
  return result;
}
