// Fixed-route, release-authoritative Sites MCP bridge for both environments.
const SHA=/^[a-f0-9]{64}$/;
const TAG=/^assertion-memory-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,30})?$/;
const UUID=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const NAME=/^[A-Za-z0-9_-]{1,100}$/;
const isObject=v=>v!==null&&typeof v==='object'&&Object.getPrototypeOf(v)===Object.prototype;
const only=(v,names)=>isObject(v)&&Object.keys(v).every(n=>names.includes(n));
const exact=(v,names)=>only(v,names)&&Object.keys(v).length===names.length;
const fail=()=>new Error('Authoritative memory unavailable.');
const capName=Object.freeze({read:'baseline_read',effects:'intake_process'});
const byteSize=s=>new TextEncoder().encode(s).length;
const bounded=(n,min,max)=>Number.isSafeInteger(n)&&n>=min&&n<=max;
async function readJson(body,maximum){
 const rd=body?.getReader();if(!rd)throw fail();
 try{
  const d=new TextDecoder('utf-8',{fatal:true});let used=0,text='';
  for(;;){const {value,done}=await rd.read();if(done)break;
   used+=value.length;if(used>maximum)throw fail();text+=d.decode(value,{stream:true});}
  return JSON.parse(text+d.decode());
 }finally{void rd.cancel().catch(()=>{});rd.releaseLock();}
}
function upstream(u,lane,suffix){
 const url=new URL(u);
 if(url.protocol!=='https:'||!/^([a-z0-9-]+\.){2}apigw\.yandexcloud\.net$/.test(url.hostname)||
    url.port||url.username||url.password||url.search||url.hash||url.pathname!=='/memory-mcp-'+lane+'-'+suffix)
  throw fail();
 return url;
}
function schema(v){
 if(!isObject(v)||v.type!=='object'||!isObject(v.properties))throw fail();
 let remaining=2400;
 const pending=[[v,0]];
 while(pending.length){
  const [row,depth]=pending.pop();
  if(--remaining<0||depth>22)throw fail();
  if(isObject(row)){
   if(Object.hasOwn(row,'$id')||Object.hasOwn(row,'$schema')||Object.hasOwn(row,'$dynamicRef')||
      (Object.hasOwn(row,'$ref')&&(typeof row.$ref!=='string'||!row.$ref.startsWith('#/'))))throw fail();
   for(const next of Object.values(row))pending.push([next,depth+1]);
  }else if(Array.isArray(row)){for(const next of row)pending.push([next,depth+1]);}
  else if(typeof row==='string'&&row.length>12000)throw fail();
 }
 return v;
}
function descriptor(v,cap){
 if(!exact(v,['name','description','inputSchema','annotations'])||typeof v.name!=='string'||!NAME.test(v.name)||
    typeof v.description!=='string'||v.description.length>8000||
    !only(v.annotations,['readOnlyHint','destructiveHint','idempotentHint','openWorldHint'])||
    Object.values(v.annotations).some(x=>typeof x!=='boolean')||v.annotations.readOnlyHint!==(cap==='read'))
  throw fail();
 schema(v.inputSchema);
 return v;
}
function identity(v,lane,id){
 if(v.environment!==lane||v.request_id!==id||typeof v.release!=='string'||!TAG.test(v.release)||
    typeof v.generation!=='string'||!SHA.test(v.generation)||typeof v.catalog_sha256!=='string'||!SHA.test(v.catalog_sha256))
   throw fail();
 return {release:v.release,generation:v.generation,catalog_sha256:v.catalog_sha256};
}
function renderDomain(v){
 if(!isObject(v))throw fail();
 const text=JSON.stringify(v);
 if(byteSize(text)>120000)throw fail();
 return {content:[{type:'text',text}],structuredContent:v};
}
export function attachReleaseMcp({legacyFetch,profile,verifyOwner,fetcher=(...xs)=>globalThis.fetch(...xs),uuid=()=>crypto.randomUUID()}){
 if(typeof legacyFetch!=='function'||typeof verifyOwner!=='function'||typeof fetcher!=='function')throw fail();
 const cfg=structuredClone(profile);
 if(!isObject(cfg)||!['test','prod'].includes(cfg.lane)||typeof cfg.includeEffects!=='boolean')throw fail();
 const lane=cfg.lane,origin=new URL(cfg.siteOrigin);
 if(origin.protocol!=='https:'||origin.origin!==cfg.siteOrigin||!origin.hostname.endsWith('.chatgpt.site')||origin.port)throw fail();
 const routes={read:upstream(cfg.readUrl,lane,'tools')};
 if(cfg.includeEffects){routes.effects=upstream(cfg.effectsUrl,lane,'effects');
  if(routes.effects.origin!==routes.read.origin)throw fail();}
 else if(cfg.effectsUrl!==undefined)throw fail();
 const timeout=cfg.effectsTimeoutMs??8000;
 if(!bounded(timeout,1000,250000))throw fail();
 const prefix='HOSTED_MEMORY_'+lane.toUpperCase();
 const foreign='HOSTED_MEMORY_'+(lane==='test'?'PROD':'TEST');
 const active=env=>env?.[prefix+'_DYNAMIC_CATALOG_ENABLED']==='true';
 const effects=env=>cfg.includeEffects&&env?.[prefix+'_EFFECTS_ENABLED']==='true';
 const headers=(cap,env)=>{
  const read=env[prefix+'_READ_TOKEN'],eff=env[prefix+'_EFFECTS_TOKEN'];
  if(typeof read!=='string'||!SHA.test(read)||env[foreign+'_READ_TOKEN']!==undefined||
     env[foreign+'_EFFECTS_TOKEN']!==undefined||
     (effects(env)&&(typeof eff!=='string'||!SHA.test(eff)||eff===read)))throw fail();
  return {'content-type':'application/json',accept:'application/json',
   ['x-memory-mcp-'+lane+(cap==='read'?'-read-auth':'-effects-auth')]:'Bearer '+(cap==='read'?read:eff)};
 };
 async function post(cap,env,body,call=false){
  const id=uuid();if(typeof id!=='string'||!UUID.test(id))throw fail();
  const wire=JSON.stringify({request_id:id,...body});if(byteSize(wire)>100000)throw fail();
  const r=await fetcher(routes[cap].href,{method:'POST',redirect:'manual',
   signal:AbortSignal.timeout(call&&cap==='effects'?timeout:8000),headers:headers(cap,env),body:wire});
  if(r.status!==200||!/^application\/json(?:\s*;|$)/i.test(r.headers.get('content-type')??'')){
   void r.body?.cancel().catch(()=>{});throw fail();
  }
  return [id,await readJson(r.body,131072)];
 }
 async function list(cap,env){
  const [id,out]=await post(cap,env,{operation:'catalog',arguments:{}});
  if(!exact(out,['request_id','environment','release','generation','catalog_sha256','capability','tools'])||
     out.capability!==capName[cap]||!Array.isArray(out.tools)||out.tools.length>64)throw fail();
  return {cap,expected:identity(out,lane,id),tools:out.tools.map(t=>descriptor(t,cap))};
 }
 async function catalog(env){
  const scopes=await Promise.all([list('read',env),...(effects(env)?[list('effects',env)]:[])]);
  const first=scopes[0].expected,tools=new Map();
  for(const scope of scopes){
   if(scope.expected.release!==first.release||scope.expected.generation!==first.generation)throw fail();
   for(const tool of scope.tools){
    if(tools.has(tool.name))throw fail();
    tools.set(tool.name,{tool,...scope});
   }
  }
  return tools;
 }
 const json=v=>Response.json(v,{headers:{'cache-control':'private, no-store','x-content-type-options':'nosniff'}});
 const error=(id,code,message)=>json({jsonrpc:'2.0',id,error:{code,message}});
 return async (req,env,ctx)=>{
  const url=new URL(req.url);
  if(!active(env)||url.pathname!=='/mcp')return legacyFetch(req,env,ctx);
  if(url.origin!==origin.origin||req.method!=='POST'||url.search||
     !/^application\/json(?:\s*;|$)/i.test(req.headers.get('content-type')??''))
   return error(null,-32600,'Invalid MCP request.');
  let parsed;
  try{parsed=await readJson(req.clone().body,100000);}
  catch{return error(null,-32700,'Invalid MCP request.');}
  if(isObject(parsed)&&['server/discover','initialize','notifications/initialized','ping'].includes(parsed.method))
   return legacyFetch(req,env,ctx);
  if(!isObject(parsed)||!['tools/list','tools/call'].includes(parsed.method))
   return error(null,-32601,'Unknown MCP method.');
  const id=parsed.id;
  if(parsed.jsonrpc!=='2.0'||!(id===null||(typeof id==='string'&&id.length<=512)||Number.isSafeInteger(id)))
   return error(null,-32600,'Invalid MCP request.');
  const meta=v=>v===undefined||isObject(v);
  const arg=parsed.params;
  if(parsed.method==='tools/list'){
   if(arg!==undefined&&(!only(arg,['cursor','_meta'])||arg.cursor!=null||!meta(arg._meta)))
    return error(id,-32602,'Invalid tool list parameters.');
  }else if(!only(arg,['name','arguments','_meta'])||typeof arg.name!=='string'||!NAME.test(arg.name)||
    (arg.arguments!==undefined&&!isObject(arg.arguments))||!meta(arg._meta))
   return error(id,-32602,'Invalid tool call.');
  try{
   if(req.headers.get('sec-fetch-site')==='cross-site'||await verifyOwner(req,env,lane)!==true)
    return error(id,-32001,'Owner authorization required.');
   const tools=await catalog(env);
   if(parsed.method==='tools/list')return json({jsonrpc:'2.0',id,result:{tools:[...tools.values()].map(x=>x.tool)}});
   const found=tools.get(arg.name);
   if(!found)return error(id,-32602,'Unknown authorized memory tool.');
   const [rid,out]=await post(found.cap,env,{operation:arg.name,arguments:arg.arguments??{},expected:found.expected},true);
   if(!exact(out,['request_id','environment','release','generation','catalog_sha256','status','result'])||out.status!=='ok')throw fail();
   const seen=identity(out,lane,rid);
   if(Object.keys(seen).some(k=>seen[k]!==found.expected[k]))throw fail();
   return json({jsonrpc:'2.0',id,result:renderDomain(out.result)});
  }catch{
   if(parsed.method==='tools/list')return error(id,-32603,'Authoritative tool catalog unavailable.');
   return json({jsonrpc:'2.0',id,result:{isError:true,content:[{type:'text',text:'Memory tool result unavailable. Check status before repeating an operation that can write.'}]}});
  }
 };
}
