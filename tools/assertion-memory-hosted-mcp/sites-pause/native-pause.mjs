const PATH='/api/v1/processing/pause', COOKIE='__Host-assertion-test-pause', TTL=120;
const HEX=/^[a-f0-9]{64}$/, REV=/^[a-f0-9]{32}$/;
const hex=b=>Array.from(new Uint8Array(b), n=>n.toString(16).padStart(2,'0')).join('');
const te=new TextEncoder();
function equal(a,b){if(a.length!==b.length)return false;let d=0;for(let i=0;i<a.length;i++)d|=a.charCodeAt(i)^b.charCodeAt(i);return d===0;}
async function mac(key,parts){
 const k=await crypto.subtle.importKey('raw',Uint8Array.from(key.match(/../g),x=>parseInt(x,16)),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 return hex(await crypto.subtle.sign('HMAC',k,te.encode(parts.join('\0'))));
}
const exact=(x,keys)=>x&&typeof x==='object'&&Object.getPrototypeOf(x)===Object.prototype&&Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const jsonType=h=>/^application\/json(?:\s*;|$)/i.test(h.get('content-type')??'');
async function json(r,limit){
 const read=r.body?.getReader();if(!read)throw Error();
 try{
  const d=new TextDecoder('utf-8',{fatal:true});let s='',n=0;
  while(true){const {done,value}=await read.read();if(done)break;n+=value.byteLength;if(n>limit)throw Error();s+=d.decode(value,{stream:true});}
  return JSON.parse(s+d.decode());
 }finally{try{await read.cancel();}catch{}read.releaseLock();}
}
const out=(status,body,headers={})=>Response.json(body,{status,headers:{'cache-control':'private, no-store','x-content-type-options':'nosniff',...headers}});
const fail=(status,code)=>out(status,{error:{code}});
const CONTROL=new Map([
 ['/api/v1/processing/sessions','sessions'],
 ['/api/v1/processing/items/remove','item_remove'],
 ['/api/v1/processing/items/source-preview','item_preview'],
]);
const sid=x=>typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$/.test(x);
const ids=x=>Array.isArray(x)&&x.length<=50&&x.every(sid)&&new Set(x).size===x.length;
const job=x=>typeof x==='string'&&x.length>0&&x.length<=512&&!/[\p{Cc}\p{Cs}]/u.test(x);
function upstreamSettings(env,gateway){
 const carrier=env.MEMORY_TEST_PAUSE_CARRIER??'dedicated';
 if((carrier!=='dedicated'&&carrier!=='effects')||
    (carrier==='effects'&&!HEX.test(env.HOSTED_MEMORY_TEST_EFFECTS_TOKEN??'')))throw Error();
 const target=carrier==='effects'?new URL('/memory-mcp-test-effects',gateway).href:gateway;
 const header=carrier==='effects'?'x-memory-mcp-test-effects-auth':'x-memory-test-pause-auth';
 const upstream=env.MEMORY_TEST_PAUSE_UPSTREAM_KEY,csrf=env.MEMORY_TEST_PAUSE_CSRF_KEY;
 if(!HEX.test(upstream??'')||!HEX.test(csrf??'')||upstream===csrf||
    Object.entries(env).some(([k,v])=>!['MEMORY_TEST_PAUSE_UPSTREAM_KEY','MEMORY_TEST_PAUSE_CSRF_KEY'].includes(k)&&/(?:_KEY|_TOKEN)$/.test(k)&&(v===upstream||v===csrf)))throw Error();
 return {target,header,upstream,csrf};
}
function validControl(kind,result,command){
 if(result?.stage!=='test')return false;
 if(kind==='sessions')return exact(result,['stage','revision','selected_session_ids','sessions'])&&typeof result.revision==='string'&&REV.test(result.revision)
   &&ids(result.selected_session_ids)&&Array.isArray(result.sessions)&&result.sessions.length<=150&&new Set(result.sessions.map(r=>r?.id)).size===result.sessions.length
   &&result.sessions.every(r=>exact(r,['id','queued','eligible'])&&sid(r.id)&&Number.isSafeInteger(r.queued)&&r.queued>=0&&typeof r.eligible==='boolean')
   &&(!command.selected_session_ids||command.expected_revision!==result.revision&&result.selected_session_ids.length===command.selected_session_ids.length
      &&result.selected_session_ids.every(x=>command.selected_session_ids.includes(x)));
 if(result.queue_id!==command.queue_id||result.item_id!==command.item_id)return false;
 if(kind==='item_remove')return exact(result,['stage','queue_id','item_id','removed','effect'])&&result.removed===true
   &&result.effect===(command.queue_id==='source'?'source_and_queue':'queue_only');
 return exact(result,['stage','queue_id','item_id','source_preview','source_role'])&&typeof result.source_preview==='string'
   &&result.source_preview.length>0&&Array.from(result.source_preview).length<=240&&[null,'user','assistant','system','developer','tool'].includes(result.source_role);
}
async function nativeControl({kind,prepare,req,env,u,origin,gateway,owner,fetcher,now}){
 if(env.MEMORY_TEST_QUEUE_CONTROL_ENABLED!=='true'||env.MEMORY_TEST_PAUSE_ENABLED!=='true'||
    env.MEMORY_TEST_PAUSE_OWNER_ATTEST_ONLY==='true'||env.MEMORY_GRAPH_MODE!=='test'||u.origin!==origin||u.search)return fail(404,'not_found');
 if(req.method!==(prepare?'POST':'PUT'))return fail(405,'invalid_method');
 if(req.headers.get('origin')!==origin||req.headers.get('sec-fetch-site')!=='same-origin')return fail(403,'origin_denied');
 if(!jsonType(req.headers))return fail(415,'invalid_request');
 let actor,body,config;
 try{actor=await owner(req,env);}catch{return fail(403,'owner_required');}
 if(typeof actor!=='string'||!HEX.test(actor))return fail(403,'owner_required');
 try{body=await json(req,16384);}catch{return fail(400,'invalid_request');}
 let command,action;
 if(kind==='sessions'){
   if(prepare){
     if(!exact(body,[]))return fail(400,'invalid_request');
     command={};action='sessions_prepare';
   }else{
     if(!exact(body,['stage','selected_session_ids','expected_revision'])||body.stage!=='test'||!ids(body.selected_session_ids)
         ||typeof body.expected_revision!=='string'||!REV.test(body.expected_revision))return fail(400,'invalid_request');
     command={selected_session_ids:body.selected_session_ids,expected_revision:body.expected_revision};action='sessions_set';
   }
 }else{
   if(!exact(body,['stage','queue_id','item_id','expected_revision'])||body.stage!=='test'||!job(body.item_id)||
      !(kind==='item_preview'?body.queue_id==='source':['source','maintenance'].includes(body.queue_id))||
      typeof body.expected_revision!=='string'||!REV.test(body.expected_revision))return fail(400,'invalid_request');
   command={queue_id:body.queue_id,item_id:body.item_id,expected_revision:body.expected_revision};action=kind;
 }
 try{config=upstreamSettings(env,gateway);}catch{return fail(503,'queue_control_unavailable');}
 const {target,header,upstream,csrf}=config;
 const s=Math.floor(now()/1000), prefix=['queue-items-csrf-v1',origin,actor,kind];
 const bound=kind==='sessions'?command.expected_revision:JSON.stringify(command);
 if(!prepare){
   const m=/^([0-9]{1,12})\.([a-f0-9]{64})$/.exec(req.headers.get('x-csrf-token')??'');
   if(!m||Number(m[1])<=s||Number(m[1])>s+30||!equal(m[2],await mac(csrf,[...prefix,m[1],bound])))return fail(403,'csrf_denied');
 }
 const issue=async(value,binding)=>{
   const until=String(s+30);
   return out(200,{...value,csrf_token:until+'.'+await mac(csrf,[...prefix,until,binding])});
 };
 if(prepare&&kind!=='sessions')return issue({stage:'test'},bound);
 try{
   const r=await fetcher(target,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(5000),
      headers:{'content-type':'application/json',[header]:'Bearer '+upstream},
      body:JSON.stringify({stage:'test',action,actor,arguments:command})});
   if(r.status!==200){try{await r.body?.cancel();}catch{}return r.status===409?fail(409,'queue_control_changed'):fail(503,'queue_control_unavailable');}
   if(!jsonType(r.headers))throw Error();
   const result=await json(r,65536);
   if(!validControl(kind,result,command))throw Error();
   return prepare?issue(result,result.revision):out(200,result);
 }catch{return fail(503,'queue_control_unavailable');}
}
export function makeTestPause({origin,gateway,owner,fetcher=(...a)=>fetch(...a),now=()=>Date.now()}){
 if(!/^https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.chatgpt\.site$/.test(origin)||
    !/^https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.apigw\.yandexcloud\.net\/memory-visual-test-pause$/.test(gateway)||typeof owner!=='function')throw Error('Invalid TEST pause scope');
 return async (req,env)=>{
  const u=new URL(req.url), isPrep=u.pathname.endsWith('/prepare');
  const kind=CONTROL.get(isPrep?u.pathname.slice(0,-8):u.pathname);
  if(kind)return nativeControl({kind,prepare:isPrep,req,env,u,origin,gateway,owner,fetcher,now});
  const prepare=u.pathname===PATH+'/prepare';
  if(!prepare&&u.pathname!==PATH)return null;
  const live=env.MEMORY_TEST_PAUSE_ENABLED==='true', attest=env.MEMORY_TEST_PAUSE_OWNER_ATTEST_ONLY==='true';
  if(live===attest||(attest&&!prepare)||env.MEMORY_GRAPH_MODE!=='test'||u.origin!==origin||u.search)return fail(404,'not_found');
  if(req.method!==(prepare?'POST':'PUT'))return fail(405,'invalid_method');
  if(req.headers.get('origin')!==origin||req.headers.get('sec-fetch-site')!=='same-origin')return fail(403,'origin_denied');
  if(!jsonType(req.headers))return fail(415,'invalid_request');
  let actor,body;
  try{actor=await owner(req,env);}catch{return fail(403,'owner_required');}
  if(typeof actor!=='string'||!HEX.test(actor))return fail(403,'owner_required');
  try{body=await json(req,512);}catch{return fail(400,'invalid_request');}
  if(prepare?!exact(body,[]):(!exact(body,['stage','paused','expected_revision'])||body.stage!=='test'||typeof body.paused!=='boolean'||typeof body.expected_revision!=='string'||!REV.test(body.expected_revision)))return fail(400,'invalid_request');
  // Bootstrap the static Base owner pin from the same actual owner proof as live
  // writes. This short-lived mode returns no CSRF cookie and never calls Base.
  if(attest)return out(200,{stage:'test',owner_sha256:actor});
  let config;
  try{config=upstreamSettings(env,gateway);}catch{return fail(503,'pause_unavailable');}
  const {target,header,upstream,csrf}=config;
  const s=Math.floor(now()/1000), identity=['queue-pause-csrf-v1',origin,actor];
  if(!prepare){
   const raw=req.headers.get('cookie')??'';
   const parts=raw.length>4096?[]:raw.split(';').map(x=>x.trim()).filter(x=>x.startsWith(COOKIE+'='));
   const c=parts.length===1?parts[0].slice(COOKIE.length+1):'';
   const m=/^([0-9]{1,12})\.([a-f0-9]{64})$/.exec(req.headers.get('x-csrf-token')??'');
   if(!HEX.test(c)||!m||Number(m[1])<s||Number(m[1])>s+TTL||!equal(m[2],await mac(csrf,[...identity,c,m[1],body.expected_revision])))return fail(403,'csrf_denied');
  }
  let result;
  try{
   const r=await fetcher(target,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(5000),
    headers:{'content-type':'application/json',[header]:'Bearer '+upstream},
    body:JSON.stringify({stage:'test',action:prepare?'prepare':'set',actor,arguments:body})});
   if(r.status!==200){try{await r.body?.cancel();}catch{}return r.status===409?fail(409,'queue_control_changed'):fail(503,'pause_unavailable');}
   if(!jsonType(r.headers))throw Error();
   result=await json(r,2048);
   if(!exact(result,['stage','paused','revision'])||result.stage!=='test'||typeof result.paused!=='boolean'||typeof result.revision!=='string'||!REV.test(result.revision)||(!prepare&&result.paused!==body.paused))throw Error();
  }catch{return fail(503,'pause_unavailable');}
  if(!prepare)return out(200,result);
  const n=hex(crypto.getRandomValues(new Uint8Array(32))), t=String(s+TTL);
  return out(200,{...result,csrf_token:t+'.'+await mac(csrf,[...identity,n,t,result.revision])},
   {'set-cookie':COOKIE+'='+n+'; Max-Age=120; Path=/; HttpOnly; Secure; SameSite=Strict'});
 };
}
