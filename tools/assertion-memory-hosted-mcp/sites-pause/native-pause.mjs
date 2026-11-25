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
export function makeTestPause({origin,gateway,owner,fetcher=(...a)=>fetch(...a),now=()=>Date.now()}){
 if(!/^https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.chatgpt\.site$/.test(origin)||
    !/^https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.apigw\.yandexcloud\.net\/memory-visual-test-pause$/.test(gateway)||typeof owner!=='function')throw Error('Invalid TEST pause scope');
 return async (req,env)=>{
  const u=new URL(req.url), prepare=u.pathname===PATH+'/prepare';
  if(!prepare&&u.pathname!==PATH)return null;
  if(env.MEMORY_TEST_PAUSE_ENABLED!=='true'||env.MEMORY_GRAPH_MODE!=='test'||u.origin!==origin||u.search)return fail(404,'not_found');
  if(req.method!==(prepare?'POST':'PUT'))return fail(405,'invalid_method');
  if(req.headers.get('origin')!==origin||req.headers.get('sec-fetch-site')!=='same-origin')return fail(403,'origin_denied');
  if(!jsonType(req.headers))return fail(415,'invalid_request');
  const upstream=env.MEMORY_TEST_PAUSE_UPSTREAM_KEY,csrf=env.MEMORY_TEST_PAUSE_CSRF_KEY;
  if(!HEX.test(upstream??'')||!HEX.test(csrf??'')||upstream===csrf||
     Object.entries(env).some(([k,v])=>!['MEMORY_TEST_PAUSE_UPSTREAM_KEY','MEMORY_TEST_PAUSE_CSRF_KEY'].includes(k)&&/(?:_KEY|_TOKEN)$/.test(k)&&(v===upstream||v===csrf)))return fail(503,'pause_unavailable');
  let actor,body;
  try{actor=await owner(req,env);}catch{return fail(403,'owner_required');}
  if(typeof actor!=='string'||!HEX.test(actor))return fail(403,'owner_required');
  try{body=await json(req,512);}catch{return fail(400,'invalid_request');}
  if(prepare?!exact(body,[]):(!exact(body,['stage','paused','expected_revision'])||body.stage!=='test'||typeof body.paused!=='boolean'||typeof body.expected_revision!=='string'||!REV.test(body.expected_revision)))return fail(400,'invalid_request');
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
   const r=await fetcher(gateway,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(5000),
    headers:{'content-type':'application/json','x-memory-test-pause-auth':'Bearer '+upstream},
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
