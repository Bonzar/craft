// Owner graph-v1 is a single same-origin data path; ordinary legacy Live stays separate.
const NAMES=['nodes.bin','edges.bin','topics.json','identities.json'];
const EPOCH=/^[a-f0-9]{64}$/;
const REV=/^[a-f0-9]{32}$/;
const CURSOR=/^(0|[1-9][0-9]{0,19})$/;
const FRAME=96*1024,CHUNK=48*1024,MAX_SNAPSHOT=64*1024*1024;
const MAX_SEGMENT={'nodes.bin':MAX_SNAPSHOT,'edges.bin':MAX_SNAPSHOT,'topics.json':16*1024*1024,'identities.json':16*1024*1024};
const bytesFromBase64=(text)=>Uint8Array.from(atob(text),c=>c.charCodeAt(0));
const digest=async bytes=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');
const fail=(why)=>{throw new Error('Graph Live: '+why)};
const has=(object,keys)=>object!==null&&typeof object==='object'&&!Array.isArray(object)&&
  Object.keys(object).length===keys.length&&keys.every(k=>Object.hasOwn(object,k));
const isStamp=v=>typeof v?.epoch==='string'&&EPOCH.test(v.epoch)&&typeof v.revision==='string'&&REV.test(v.revision)&&typeof v.cursor==='string'&&CURSOR.test(v.cursor);
const cursorLess=(a,b)=>BigInt(a)<BigInt(b);

export async function readOwnerGraphWire({selector,checkpoint=null,fetcher=fetch,signal,onEvent,onHeaders}){
  const q=new URLSearchParams({wire:'graph-v1'});
  for(const k of ['mode','at','history','undated','session_id','run_id','output_snapshot_id']){
    const value=selector[k];if(value===null||value===undefined)continue;
    q.set(k,typeof value==='boolean'?(value?'1':'0'):String(value));
  }
  if(checkpoint){
    if(!isStamp(checkpoint))fail('invalid local checkpoint');
    q.set('epoch',checkpoint.epoch);q.set('revision',checkpoint.revision);q.set('after',checkpoint.cursor);
  }
  const response=await fetcher('/api/v1/events?'+q,{method:'GET',credentials:'same-origin',
    redirect:'error',cache:'no-store',headers:{Accept:'text/event-stream'},signal});
  if(!response.ok||!/^text\/event-stream(?:;|$)/i.test(response.headers.get('Content-Type')??'')||!response.body){
    const error=new Error('Graph Live: invalid response');error.status=response.status;throw error;
  }
  onHeaders?.();
  const decoder=new TextDecoder('utf-8',{fatal:true});
  const reader=response.body.getReader();
  let pending='';
  try {
    while(!signal?.aborted){
      const chunk=await reader.read();if(chunk.done)break;
      pending+=decoder.decode(chunk.value,{stream:true});
      let match;
      while((match=/\r?\n\r?\n/.exec(pending))){
        if(match.index>FRAME)fail('frame bound exceeded');
        const frame=pending.slice(0,match.index);pending=pending.slice(match.index+match[0].length);
        const fields=/^event: ([a-z-]+)\r?\ndata: (\{[^\r\n]*\})$/.exec(frame);
        if(!fields)fail('malformed frame');
        if(signal?.aborted)return;
        await onEvent(fields[1],JSON.parse(fields[2]));
      }
      if(pending.length>FRAME)fail('partial frame bound exceeded');
    }
    if(pending&&!signal?.aborted)fail('ended inside a frame');
  }finally{try{await reader.cancel()}catch{}reader.releaseLock();}
}

// All callbacks finish acceptance before the cursor moves; only an accepted
// baseline or delta is eligible to become an atomic owner checkpoint.
export function ownerGraphReconciler({checkpoint=null,onSnapshot,onDelta,onReset=()=>{},onCaughtUp=()=>{},onHeartbeat=()=>{},sha256=digest}){
  if(checkpoint&&!isStamp(checkpoint))fail('invalid local checkpoint');
  let epoch=null,cursor=checkpoint?.cursor??null,revision=checkpoint?.revision??null,stage=null,hello=false;
  let activeEpoch=checkpoint?.epoch??null,sameEpochCursor=checkpoint?.cursor??null;
  function stamp(v){
    if(!isStamp(v)||v.epoch!==epoch)fail('unverified epoch or revision');
  }
  function sameStage(v){
    stamp(v);
    if(!stage||v.revision!==stage.revision||v.cursor!==stage.cursor)fail('baseline fence mismatch');
  }
  async function accept(kind,v){
    if(kind==='hello'){
      if(hello||!has(v,['protocol','epoch'])||v.protocol!=='graph-v1'||typeof v.epoch!=='string'||!EPOCH.test(v.epoch))
        fail('invalid hello');
      hello=true;epoch=v.epoch;
      if(activeEpoch!==null&&activeEpoch!==epoch){
        await onReset({epoch,reason:'epoch_changed'});revision=cursor=activeEpoch=sameEpochCursor=null;
      }
      return;
    }
    if(!hello)fail('no graph greeting');
    if(kind==='reset'){
      if(!has(v,['epoch','reason'])||typeof v.reason!=='string'||!v.reason||typeof v.epoch!=='string'||!EPOCH.test(v.epoch))fail('invalid reset');
      stage=null;if(epoch!==v.epoch)sameEpochCursor=null;epoch=v.epoch;revision=cursor=activeEpoch=null;await onReset(v);return;
    }
    if(kind==='heartbeat'){
      if(stage)fail('interleaved baseline');
      await onHeartbeat(v);return;
    }
    if(kind==='snapshot-start'){
      if(stage||!has(v,['epoch','revision','cursor','manifest']))fail('nested baseline');
      stamp(v);
      if(sameEpochCursor!==null&&cursorLess(v.cursor,sameEpochCursor))fail('older baseline');
      const {manifest}=v;
      if(typeof manifest!=='object'||manifest===null||manifest.revision!==v.revision||!manifest.complete_graph||
          manifest.owner_live_epoch!==epoch||typeof manifest.live_cursor!=='string'||manifest.live_cursor!==v.cursor)
        fail('manifest fence mismatch');
      stage={...v,total:0,segments:Object.fromEntries(NAMES.map(name=>[name,{length:0,chunks:[]}]))};
      return;
    }
    if(kind==='snapshot-chunk'){
      if(!stage||!has(v,['revision','name','offset','base64'])||v.revision!==stage.revision||
          !NAMES.includes(v.name)||typeof v.base64!=='string'||v.base64.length===0||v.base64.length>CHUNK*4/3||
          !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(v.base64))fail('invalid baseline chunk');
      const segment=stage.segments[v.name],data=bytesFromBase64(v.base64);
      if(v.offset!==segment.length||data.length===0||data.length>CHUNK||
          segment.length+data.length>MAX_SEGMENT[v.name]||stage.total+data.length>MAX_SNAPSHOT)fail('baseline bounds or offset mismatch');
      segment.length+=data.length;stage.total+=data.length;segment.chunks.push(data);return;
    }
    if(kind==='snapshot-end'){
      if(!has(v,['epoch','revision','cursor','segments'])||!has(v.segments,NAMES))fail('invalid baseline receipt');
      sameStage(v);
      const finished={};
      for(const name of NAMES){
        const segment=stage.segments[name],proof=v.segments[name];
        if(!has(proof,['bytes','sha256'])||proof.bytes!==segment.length||typeof proof.sha256!=='string'||!EPOCH.test(proof.sha256))
          fail('baseline size mismatch');
        const data=new Uint8Array(segment.length);let end=0;
        for(const chunk of segment.chunks){data.set(chunk,end);end+=chunk.length;}
        if(await sha256(data)!==proof.sha256)fail('baseline checksum mismatch');
        finished[name]=data;
      }
      const manifest=stage.manifest, decode=new TextDecoder('utf-8',{fatal:true});
      if(finished['nodes.bin'].length!==manifest.nodes?.byte_length||finished['edges.bin'].length!==manifest.edges?.byte_length)
        fail('baseline geometry length mismatch');
      const ids=JSON.parse(decode.decode(finished['identities.json']));
      if(!Array.isArray(ids)||ids.length!==manifest.counts?.nodes||ids.some(i=>typeof i!=='string'||!i||i.length>1024)||new Set(ids).size!==ids.length)
        fail('baseline identities mismatch');
      const topics=JSON.parse(decode.decode(finished['topics.json']));
      if(topics?.revision!==v.revision)fail('baseline topics revision mismatch');
      await onSnapshot({epoch:v.epoch,revision:v.revision,cursor:v.cursor,manifest,topics,ids,
        nodes:finished['nodes.bin'].buffer,edges:finished['edges.bin'].buffer});
      stage=null;revision=v.revision;cursor=sameEpochCursor=v.cursor;activeEpoch=epoch;return;
    }
    if(stage)fail('interleaved baseline');
    if(kind==='delta'){
      stamp(v);
      if(activeEpoch!==epoch||v.base_revision!==revision||v.revision===revision||cursor===null||!cursorLess(cursor,v.cursor))
        fail('noncontiguous delta');
      await onDelta(v);revision=v.revision;cursor=sameEpochCursor=v.cursor;return;
    }
    if(kind==='caught-up'){
      if(!has(v,['epoch','revision','cursor']))fail('invalid caught-up watermark');
      stamp(v);
      if(activeEpoch!==epoch||v.revision!==revision||cursor===null||cursorLess(v.cursor,cursor))fail('inconsistent caught-up watermark');
      await onCaughtUp(v);cursor=sameEpochCursor=v.cursor;return;
    }
    fail('unsupported event');
  }
  return {accept,checkpoint:()=>activeEpoch&&revision!==null&&cursor!==null?{epoch:activeEpoch,revision,cursor}:null};
}
