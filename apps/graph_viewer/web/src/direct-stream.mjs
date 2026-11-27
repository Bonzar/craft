// One exact connection per call. The caller chooses if/when to reconnect.
const EVENT_TICKET='/api/v1/events-ticket';
const TEST_DIRECT_PATH='/__mvt/api/v1/events-direct';
const KINDS=new Set(['hello','heartbeat','changed','reset']);
const denied=status=>status===401||status===403;
function terminalStreamError(message,authorizationDenied=false){
  const error=new Error(message);error.name='OwnerStreamTerminalError';error.authorizationDenied=authorizationDenied;return error;
}

export function directStreamTarget(value,allowedOrigin){
  const url=new URL(value);
  if(url.protocol!=='https:'||url.origin!==allowedOrigin||url.pathname!==TEST_DIRECT_PATH||
     url.username||url.password||url.search||url.hash)throw terminalStreamError('Unexpected stream target');
  return url.href;
}

export async function readDirectStream({fetcher=fetch,csrf,allowedOrigin,signal,onEvent}){
  const reply=await fetcher(EVENT_TICKET,{method:'POST',credentials:'same-origin',
    headers:{'X-CSRF-Token':csrf},cache:'no-store',redirect:'error',signal});
  if(!reply.ok)throw (denied(reply.status)?terminalStreamError('Unable to authorize stream',true):new Error('Unable to authorize stream'));
  const value=await reply.json();
  if(typeof value?.ticket!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(value.ticket))
    throw terminalStreamError('Invalid stream authorization');
  const epoch=value.epoch??null;
  if(epoch!==null&&(typeof epoch!=='string'||!/^[a-f0-9]{64}$/.test(epoch)))throw terminalStreamError('Invalid stream epoch');
  const target=directStreamTarget(value.stream_url,allowedOrigin);
  const stream=await fetcher(target,{method:'GET',credentials:'omit',mode:'cors',redirect:'error',
    cache:'no-store',headers:{'Authorization':'Bearer '+value.ticket,'Accept':'text/event-stream'},signal});
  return parseOwnerFrames(stream,{epoch,signal,onEvent});
}

export async function readSameOriginOwnerStream({fetcher=fetch,signal,onEvent}){
  const stream=await fetcher('/api/v1/events',{method:'GET',credentials:'same-origin',
    redirect:'error',cache:'no-store',headers:{Accept:'text/event-stream'},signal});
  return parseOwnerFrames(stream,{sameOrigin:true,signal,onEvent});
}

async function parseOwnerFrames(stream,{epoch=null,sameOrigin=false,signal,onEvent}){
  if(!stream.ok)throw (denied(stream.status)?terminalStreamError('Stream unavailable',true):new Error('Stream unavailable'));
  if(!/^text\/event-stream(?:;|$)/i.test(stream.headers.get('Content-Type')??'')||!stream.body)
    throw terminalStreamError('Invalid stream response');
  const reader=stream.body.getReader();
  const decoder=new TextDecoder('utf-8',{fatal:true});
  let pending='',opened=false;
  try {
    while(!signal?.aborted){
      const chunk=await reader.read();
      if(chunk.done)break;
      pending+=decoder.decode(chunk.value,{stream:true});
      let match;
      const maxFrame=sameOrigin?128:64;
      while((match=/\r?\n\r?\n/.exec(pending))){
        if(match.index>maxFrame)throw terminalStreamError('Invalid stream frame size');
        const frame=pending.slice(0,match.index);
        pending=pending.slice(match.index+match[0].length);
        const ordinaryHello=sameOrigin&&!opened&&/^event: hello\r?\ndata: \{"epoch":"([a-f0-9]{64})"\}$/.exec(frame);
        if(ordinaryHello){epoch=ordinaryHello[1];opened=true;onEvent('hello',epoch);continue;}
        const fields=/^event: ([a-z]+)\r?\ndata: \{\}$/.exec(frame);
        if(!fields||!KINDS.has(fields[1])||((fields[1]==='hello')===opened)||sameOrigin&&!opened)
          throw terminalStreamError('Invalid stream event');
        opened=true;onEvent(fields[1],epoch);
      }
      if(pending.length>maxFrame)throw terminalStreamError('Invalid stream frame size');
    }
    if(!opened||pending.length)throw new Error('Stream ended during a frame');
  } finally {
    try{await reader.cancel()}catch{}
    reader.releaseLock();
  }
}
