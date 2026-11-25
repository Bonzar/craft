// Narrow owner-only UI bridge. This is not a generic REST proxy or MCP tool.
const HEX = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const ID = /^[A-Za-z0-9_.:-]{1,160}$/;
const NAME = /^[a-z0-9][a-z0-9-]{0,31}$/;
const TAG = /^assertion-memory-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,30})?$/;
const VISUAL_TAG = /^assertion-memory-visual-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,30})?$/;
const GATEWAY = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2}apigw\.yandexcloud\.net$/;
const prefix='/visual/admin/';
const plain=x=>x!==null&&typeof x==='object'&&Object.getPrototypeOf(x)===Object.prototype;
const exact=(x,keys)=>plain(x)&&Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const uint=(x,maximum)=>Number.isSafeInteger(x)&&x>=0&&x<=maximum;
const out=(status,body)=>Response.json(body,{status,headers:{
  'cache-control':'private, no-store, no-transform','x-content-type-options':'nosniff','referrer-policy':'no-referrer',
}});
const error=(status,code)=>out(status,{error:code});
const deny=()=>{throw new Error('Invalid visual administration exchange');};
async function readJson(body,maximum){
  const reader=body?.getReader();if(!reader)deny();
  try{
    const decoder=new TextDecoder('utf-8',{fatal:true});let data='',seen=0;
    for(;;){
      const {value,done}=await reader.read();
      if(done)break;
      seen+=value.byteLength;if(seen>maximum)deny();data+=decoder.decode(value,{stream:true});
    }
    return JSON.parse(data+decoder.decode());
  }finally{void reader.cancel().catch(()=>{});reader.releaseLock();}
}
const toHex=bytes=>Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
async function actor(req,env){
  const value=req.headers.get('oai-authenticated-user-id');
  const expected=env.MEMORY_VISUAL_ADMIN_OWNER_ID_SHA256;
  if(!value||value.length>512||!HEX.test(expected??''))return null;
  const seen=toHex(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)));
  let diff=0;for(let i=0;i<64;i++)diff|=seen.charCodeAt(i)^expected.charCodeAt(i);
  return diff===0?seen:null;
}
const number=(url,key,def,maximum)=>{
  const values=url.searchParams.getAll(key);if(!values.length)return def;
  if(values.length!==1||! /^(?:0|[1-9]\d{0,6})$/.test(values[0]))deny();
  const n=Number(values[0]);if(!uint(n,maximum))deny();return n;
};
const wiring=Object.freeze({
  status:{method:'GET',action:'status'},
  devices:{method:'POST',action:'enroll_device'},
  'reset/prepare':{method:'POST',action:'prepare_reset'},
  reset:{method:'POST',action:'reset_database'},
});
async function args(action,req,url){
  if(action==='status'){
    if([...url.searchParams.keys()].some(k=>!['limit','offset'].includes(k)))deny();
    const limit=number(url,'limit',50,100),offset=number(url,'offset',0,1000000);
    if(limit<1)deny();return {limit,offset};
  }
  if(url.search||!/^application\/json(?:\s*;|$)/i.test(req.headers.get('content-type')??''))deny();
  const value=await readJson(req.body,2048);
  if(action==='enroll_device'&&exact(value,['name','fingerprint'])&&
    typeof value.name==='string'&&NAME.test(value.name)&&typeof value.fingerprint==='string'&&HEX.test(value.fingerprint))return value;
  if(action==='prepare_reset'&&exact(value,[]))return value;
  if(action==='reset_database'&&exact(value,['confirmation_id','confirmation'])&&
     typeof value.confirmation_id==='string'&&ID.test(value.confirmation_id)&&
     typeof value.confirmation==='string'&&value.confirmation.length<=64)return value;
  deny();
}
const label=x=>typeof x==='string'&&ID.test(x);
const dates=x=>typeof x==='string'&&x.length<=35&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(x);
function publicStatus(v,lane,tag){
  if(!plain(v)||v.stage!==lane||v.release_version!==tag||v.global_order!==false||
     !Array.isArray(v.queues)||v.queues.length>16)deny();
  const seen=new Set();
  const queues=v.queues.map(q=>{
    if(!plain(q)||!label(q.id)||seen.has(q.id)||!label(q.status)||!label(q.ordering)||
       !uint(q.total,1000000)||!(q.next_offset==null||uint(q.next_offset,1000000))||
       !Array.isArray(q.items)||q.items.length>100)deny();
    seen.add(q.id);
    const items=q.items.map(i=>{
      if(!plain(i)||!label(i.id)||!label(i.status)||!(i.phase==null||label(i.phase))||
         !(i.position==null||uint(i.position,1000000))||!(i.available_at==null||dates(i.available_at)))deny();
      return {id:i.id,status:i.status,phase:i.phase??null,position:i.position??null,available_at:i.available_at??null};
    });
    return {id:q.id,status:q.status,ordering:q.ordering,total:q.total,next_offset:q.next_offset??null,items};
  });
  return {stage:lane,release_version:tag,
    ...(typeof v.visual_release_version==='string'&&VISUAL_TAG.test(v.visual_release_version)?{visual_release_version:v.visual_release_version}:{}),
    ...(dates(v.snapshot_at)?{snapshot_at:v.snapshot_at}:{}),
    global_order:false,queues,capabilities:{
      enroll_device:v.capabilities?.enroll_device===true,reset_database:v.capabilities?.reset_database===true,
    }};
}
function publicResult(action,v,lane,tag,arguments_){
  if(action==='status')return publicStatus(v,lane,tag);
  if(action==='enroll_device'&&plain(v)&&v.name===arguments_.name&&v.enrolled===true)
    return {name:v.name,enrolled:true};
  if(action==='prepare_reset'&&plain(v)&&label(v.confirmation_id)&&v.stage===lane&&
     v.phrase==='ОЧИСТИТЬ '+lane.toUpperCase()&&uint(v.expires_in,300)&&v.expires_in>=1)
    return {confirmation_id:v.confirmation_id,phrase:v.phrase,expires_in:v.expires_in,stage:lane};
  if(action==='reset_database'&&plain(v)&&v.stage===lane&&v.reset===true)return {stage:lane,reset:true};
  deny();
}
export function makeVisualAdminRelay(lane,fetcher=fetch,uuid=()=>crypto.randomUUID()){
  if(!['test','prod'].includes(lane))deny();
  return async function visualAdmin(req,env,url=new URL(req.url)){
    if(!url.pathname.startsWith(prefix))return null;
    const route=Object.hasOwn(wiring,url.pathname.slice(prefix.length))?wiring[url.pathname.slice(prefix.length)]:null;
    if(!route)return error(404,'not_found');
    if(env.MEMORY_VISUAL_ADMIN_ENABLED!=='true')return error(404,'not_found');
    if(req.method!==route.method)return error(405,'invalid_method');
    if(req.headers.get('sec-fetch-site')==='cross-site'||
       req.method==='POST'&&(req.headers.get('origin')!==url.origin||req.headers.get('sec-fetch-site')!=='same-origin'))
      return error(403,'owner_required');
    const who=await actor(req,env);
    if(!who)return error(403,'owner_required');
    const names=lane==='test'?['MEMORY_VISUAL_TEST_ADMIN_TOKEN','MEMORY_VISUAL_PROD_ADMIN_TOKEN']:
      ['MEMORY_VISUAL_PROD_ADMIN_TOKEN','MEMORY_VISUAL_TEST_ADMIN_TOKEN'];
    const token=env[names[0]],host=env.MEMORY_VISUAL_ADMIN_GATEWAY_HOST;
    if(!HEX.test(token??'')||env[names[1]]!==undefined||!GATEWAY.test(host??'')||
       Object.entries(env).some(([k,v])=>k!==names[0]&&k.endsWith('_TOKEN')&&v===token))
      return error(503,'admin_unavailable');
    let arguments_;
    try{arguments_=await args(route.action,req,url);}catch{return error(400,'invalid_parameters');}
    try{
      const request_id=uuid();if(!UUID.test(request_id))deny();
      const r=await fetcher('https://'+host+'/memory-visual-'+lane+'-admin',{
        method:'POST',redirect:'error',signal:AbortSignal.timeout(route.action==='reset_database'?60000:8000),
        headers:{'content-type':'application/json','accept':'application/json',
          'x-memory-visual-admin-auth':'Bearer '+token},
        body:JSON.stringify({request_id,environment:lane,action:route.action,actor:who,arguments:arguments_}),
      });
      if(r.status!==200){
        void r.body?.cancel().catch(()=>{});
        if(r.status===409)return error(409,'state_changed');
        if(r.status===429)return error(429,'try_later');
        return error(503,'admin_unavailable');
      }
      if(!/^application\/json(?:\s*;|$)/i.test(r.headers.get('content-type')??''))deny();
      const v=await readJson(r.body,131072);
      if(!exact(v,['request_id','environment','release_version','result'])||v.request_id!==request_id||
         v.environment!==lane||typeof v.release_version!=='string'||!TAG.test(v.release_version))deny();
      return out(200,publicResult(route.action,v.result,lane,v.release_version,arguments_));
    }catch{
      return error(503,route.action==='reset_database'?'check_status_before_retry':'admin_unavailable');
    }
  };
}
