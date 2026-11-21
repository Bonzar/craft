// Sites MCP transport for two read-only tools, gated to exact v1 fixtures.
import {createRawSessionReads} from './mcp_raw_sessions.mjs';

const version='2026-07-28';
const path='/mcp';
const maxBytes=8192;
const marker='private-synthetic-assertion-smoke/v1';
const headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'};
const json=(data,status=200)=>Response.json(data,{status,headers});
const own=(value,key)=>Object.hasOwn(value,key);
const object=value=>value!==null&&typeof value==='object'&&Object.getPrototypeOf(value)===Object.prototype;
const int=(v,lo,hi)=>Number.isSafeInteger(v)&&v>=lo&&v<=hi;
const field=(type,title,extra={})=>({type,title,...extra});
const nullable=(type,title)=>({anyOf:[{type},{type:'null'}],default:null,title});
const pageProps=()=>({offset:field('integer','Offset',{default:0}),limit:field('integer','Limit',{default:50}),
  snapshot_id:nullable('integer','Snapshot Id')});
const publicTools=()=>{
  const tools=[
    {name:'memory_sessions',description:'List only the two fixed synthetic preview capture sessions. No message bodies, real data, or model calls.',
      inputSchema:{type:'object',title:'memory_sessionsArguments',properties:pageProps()}},
    {name:'memory_session_messages',description:'Read only exact capture metadata for the two fixed synthetic preview sessions. No bodies or model calls.',
      inputSchema:{type:'object',title:'memory_session_messagesArguments',required:['session_id'],
        properties:{session_id:field('string','Session Id'),turn_id:nullable('string','Turn Id'),
          message_id:nullable('string','Message Id'),...pageProps()}}},
  ];
  return tools;
};
export const configuredMemoryOwner=(request,env)=>typeof env?.MEMORY_OWNER_ID==='string'&&
  env.MEMORY_OWNER_ID.length>0&&request.headers.get('oai-authenticated-user-id')===env.MEMORY_OWNER_ID;

async function readJson(req){
  const len=req.headers.get('content-length');
  if(len!==null&&(!/^\d+$/.test(len)||Number(len)>maxBytes)) throw new Error();
  const reader=req.body?.getReader();
  if(!reader)throw new Error();
  const decode=new TextDecoder('utf-8',{fatal:true});
  let byteLength=0, body='';
  try{
    while(true){
      const {done,value}=await reader.read();
      if(done)break;
      byteLength+=value.byteLength;
      if(byteLength>maxBytes)throw new Error();
      body+=decode.decode(value,{stream:true});
    }
    return JSON.parse(body+decode.decode());
  }finally{
    await reader.cancel().catch(()=>{});
    reader.releaseLock();
  }
}

// Bind all underlying reader SELECTs to an explicit input relation, not the
// physical capture table. No caller can supply SQL or the fixture predicate.
// Renumber only the authorized append order to avoid exposing omitted row counts.
const scopedCaptures=`WITH memory_raw_captures AS (
  SELECT row_number() OVER (ORDER BY c.rowid) AS rowid,c.event_id,c.session_id,c.message_id,c.position,c.raw_json
  FROM main.memory_raw_captures c
  WHERE EXISTS (SELECT 1 FROM memory_synthetic_smoke_environment
    WHERE singleton=1 AND purpose='private-synthetic-assertion-smoke/v1')
    AND ((c.session_id='synthetic-assertion-smoke-v1-accepted'
      AND c.event_id='event-5f46affe35eb1c8db1c1961f3d1c0f776f9372c3edca41d6a03ca0318fb2f441'
      AND c.receipt='sha256:b56ae40d5101ba5f2e343d2e793dc21a3c01b1c2f66898218abca21b5ac609a2')
    OR (c.session_id='synthetic-assertion-smoke-v1-rejected'
      AND c.event_id='event-cbddcf88d15872384c4bd879101e8cd60d3ac5bbc44db669d1d310f081ddfc74'
      AND c.receipt='sha256:4b4921195bd58a3d136e38318c8ddfe84cbb1f52540df6d77f5c1d64364354ee'))
    AND json_extract(c.raw_json,'$.id')=c.event_id
    AND json_extract(c.raw_json,'$.session_id')=c.session_id
    AND c.message_id='fixture-1' AND c.position=0
    AND json_extract(c.raw_json,'$.scope')='synthetic-assertion-smoke'
    AND json_extract(c.raw_json,'$.source')='fixture://assertion-smoke/v1'
    AND json_extract(c.raw_json,'$.prompt')='The imaginary locker has a blue label.'
    AND json_extract(c.raw_json,'$.actor')='user'
    AND json_extract(c.raw_json,'$.turn_id')='fixture-1'
    AND json_extract(c.raw_json,'$.metadata.message_id')='fixture-1'
    AND json_extract(c.raw_json,'$.metadata.role')='user'
    AND json_type(c.raw_json,'$.metadata.native_session_id') IS NULL
    AND json_type(c.raw_json,'$.metadata.native_turn_id') IS NULL
) `;

function boundedArgs(name,value){
  if(value===undefined)value={};
  if(!object(value))throw new TypeError();
  const isMessages=name==='memory_session_messages';
  const allowed=['offset','limit','snapshot_id',...(isMessages?['session_id','turn_id','message_id']:[])];
  if(Object.keys(value).some(k=>!allowed.includes(k))||
    (own(value,'offset')&&!int(value.offset,0,Number.MAX_SAFE_INTEGER))||
    (own(value,'limit')&&!int(value.limit,1,200))||
    (value.snapshot_id!=null&&!int(value.snapshot_id,0,Number.MAX_SAFE_INTEGER)))throw new TypeError();
  const id=v=>typeof v==='string'&&v.isWellFormed()&&!!v.trim()&&[...v].length<=2048;
  if(isMessages&&(!id(value.session_id)||(value.turn_id!=null&&!id(value.turn_id))||
    (value.message_id!=null&&!id(value.message_id))))throw new TypeError();
  return value;
}
async function marked(db){
  const row=await db.prepare('SELECT purpose FROM memory_synthetic_smoke_environment WHERE singleton=1 AND purpose=?').bind(marker).all();
  return row?.success!==false&&row?.results?.[0]?.purpose===marker;
}

/**
 * Integrator supplies owner identity policy only if Sites dispatch's
 * owner-private restriction is independently verified. An authenticated ID
 * by itself is not proof that a site with multiple viewers has one owner.
 */
export function createPreviewMcpRoute({isOwner=configuredMemoryOwner}={}){
  if(typeof isOwner!=='function')throw new TypeError('Trusted owner policy required.');
  return async (req,env)=>{
    if(new URL(req.url).pathname!==path)return null;
    if(req.method!=='POST')return Response.json({error:'Use POST.'},{status:405,headers:{...headers,Allow:'POST'}});
    let message;
    const rpc=(id,result)=>json({jsonrpc:'2.0',id,result});
    const error=(id,code,text)=>json({jsonrpc:'2.0',id,error:{code,message:text}});
    try{message=await readJson(req);}catch{return error(null,-32700,'Invalid or oversized JSON.');}
    if(!object(message)||message.jsonrpc!=='2.0'||typeof message.method!=='string')
      return error(null,-32600,'Invalid request.');
    const id=message.id??null;
    if(id!==null&&!(typeof id==='string'&&id.length<=512)&&!Number.isSafeInteger(id))
      return error(null,-32600,'Invalid request ID.');
    if(!own(message,'id'))return new Response(null,{status:202,headers});
    const params=message.params;
    if(params!==undefined&&!object(params))return error(id,-32602,'Invalid parameters.');
    switch(message.method){
      case 'server/discover':return rpc(id,{supportedVersions:[version],capabilities:{tools:{}}});
      case 'initialize':return rpc(id,{protocolVersion:version,serverInfo:{name:'assertion-memory-preview',version:'0.1.0'},
        capabilities:{tools:{}}});
      case 'tools/list':
        if(params&&(Object.keys(params).some(k=>k!=='cursor')||params.cursor!=null))
          return error(id,-32602,'Invalid tool list cursor.');
        return rpc(id,{tools:publicTools()});
      case 'tools/call':break;
      default:return error(id,-32601,'Method not found.');
    }
    const failed=text=>rpc(id,{isError:true,content:[{type:'text',text}]});
    let authorized=false;
    try{
      authorized=req.headers.get('sec-fetch-site')!=='cross-site'&&Boolean(req.headers.get('oai-authenticated-user-id'))&&
        await isOwner(req,env)===true;
    }catch{ /* A broken custom owner guard denies access. */ }
    if(!authorized)return failed('Owner authorization required.');
    if(!params||Object.keys(params).some(k=>!['name','arguments','_meta'].includes(k))||
        typeof params.name!=='string')return error(id,-32602,'Invalid tool call.');
    const name=params.name;
    if(name!=='memory_sessions'&&name!=='memory_session_messages')return failed('Unknown read-only preview tool.');
    let args;
    try{args=boundedArgs(name,params.arguments);}catch{return failed('Invalid preview tool arguments.');}
    try{
      const db=env?.DB;
      if(!await marked(db))return failed('Dedicated synthetic preview storage is unavailable.');
      const api=createRawSessionReads({prepare:sql=>db.prepare(scopedCaptures+sql),batch:statements=>db.batch(statements)});
      const result=await api[name](args);
      if(name==='memory_session_messages')result.notice=result.notice.replace(
        'Read bodies with memory_source(event_id).','Message bodies are not exposed in this preview.');
      return rpc(id,{content:[{type:'text',text:JSON.stringify(result)}]});
    }catch{return failed('Dedicated synthetic preview storage is unavailable.');}
  };
}
