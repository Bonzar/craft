// One exact connection per call. The caller chooses if/when to reconnect.
const EVENT_TICKET='/api/v1/events-ticket';
const TEST_DIRECT_PATH='/__mvt/api/v1/events-direct';
const KINDS=new Set(['hello','heartbeat','changed','reset']);

export function directStreamTarget(value,allowedOrigin){
  const url=new URL(value);
  if(url.protocol!=='https:'||url.origin!==allowedOrigin||url.pathname!==TEST_DIRECT_PATH||
     url.username||url.password||url.search||url.hash)throw new Error('Unexpected stream target');
  return url.href;
}

export async function readDirectStream({fetcher=fetch,csrf,allowedOrigin,signal,onEvent}){
  const reply=await fetcher(EVENT_TICKET,{method:'POST',credentials:'same-origin',
    headers:{'X-CSRF-Token':csrf},cache:'no-store',redirect:'error',signal});
  if(!reply.ok)throw new Error('Unable to authorize stream');
  const value=await reply.json();
  if(typeof value?.ticket!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(value.ticket))
    throw new Error('Invalid stream authorization');
  const target=directStreamTarget(value.stream_url,allowedOrigin);
  const stream=await fetcher(target,{method:'GET',credentials:'omit',mode:'cors',redirect:'error',
    cache:'no-store',headers:{'Authorization':'Bearer '+value.ticket,'Accept':'text/event-stream'},signal});
  if(!stream.ok||!/^text\/event-stream(?:;|$)/i.test(stream.headers.get('Content-Type')??'')||!stream.body)
    throw new Error('Stream unavailable');
  const reader=stream.body.getReader();
  const decoder=new TextDecoder('utf-8',{fatal:true});
  let pending='',opened=false;
  try {
    while(!signal?.aborted){
      const chunk=await reader.read();
      if(chunk.done)break;
      pending+=decoder.decode(chunk.value,{stream:true});
      let match;
      while((match=/\r?\n\r?\n/.exec(pending))){
        if(match.index>64)throw new Error('Invalid stream frame size');
        const frame=pending.slice(0,match.index);
        pending=pending.slice(match.index+match[0].length);
        const fields=/^event: ([a-z]+)\r?\ndata: \{\}$/.exec(frame);
        if(!fields||!KINDS.has(fields[1])||((fields[1]==='hello')===opened))
          throw new Error('Invalid stream event');
        opened=true;onEvent(fields[1]);
      }
      if(pending.length>64)throw new Error('Invalid stream frame size');
    }
    if(!opened||pending.length)throw new Error('Stream ended during a frame');
  } finally {
    try{await reader.cancel()}catch{}
    reader.releaseLock();
  }
}
