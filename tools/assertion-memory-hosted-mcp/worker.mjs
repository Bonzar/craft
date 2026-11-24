// Common Sites Worker transport; lane is fixed by the deployment, never by a request.
const EFFECTS=new Set(['memory_capture_message','memory_process']);
const QUERY='memory_query';
const SHA=/^[0-9a-f]{64}$/;
const TAG=/^assertion-memory-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]{1,30})?$/;
const UUID=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const object=x=>x!==null&&typeof x==='object'&&Object.getPrototypeOf(x)===Object.prototype;
const permitted=(x,a)=>object(x)&&Object.keys(x).every(k=>a.includes(k));
const fail=(text='Memory tool unavailable.')=>({isError:true,content:[{type:'text',text}]});
const rej=(msg)=>new TypeError(msg);
async function limited(response){
 if(response.status!==200||!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type')??'')){
  try{await response.body?.cancel();}catch{}throw Error('upstream');
 }
 const rd=response.body?.getReader();if(!rd)throw Error('no response');
 try {const d=new TextDecoder('utf-8',{fatal:true});let n=0,s='';
  for(;;){const {done,value}=await rd.read();if(done)break;n+=value.length;if(n>131072)throw Error('limit');s+=d.decode(value,{stream:true});}
  return JSON.parse(s+d.decode());
 } finally {try{await rd.cancel();}catch{}rd.releaseLock();}
}
export function makeHostedRelay({profile,catalog,verifyOwner,fetcher=(...a)=>globalThis.fetch(...a),uuid=()=>crypto.randomUUID()}){
 // Profiles and canonical catalog are trusted, immutable release inputs; validate that they agree.
 const cfg=structuredClone(profile), shared=structuredClone(catalog);
 if(!['test','prod'].includes(cfg.lane)||typeof verifyOwner!=='function'||!SHA.test(cfg.catalog_sha256)||
  cfg.catalog_sha256!==shared.catalog_sha256||!TAG.test(cfg.base_release)||
  !Array.isArray(cfg.enabled_capabilities)||new Set(cfg.enabled_capabilities).size!==cfg.enabled_capabilities.length||
  !cfg.enabled_capabilities.includes('baseline_read')||!cfg.enabled_capabilities.every(x=>x==='baseline_read'||x==='intake_process'||x==='generate'))throw rej('Invalid build profile');
 const site=new URL(cfg.site_origin),gateway=new URL(cfg.gateway_url);
 if(site.protocol!=='https:'||site.origin!==cfg.site_origin||!site.hostname.endsWith('.chatgpt.site')||site.port||
  gateway.protocol!=='https:'||!/^([a-z0-9-]+\.){2}apigw\.yandexcloud\.net$/.test(gateway.hostname)||
  gateway.port||gateway.username||gateway.password||gateway.search||gateway.hash||
  gateway.pathname!=='/memory-mcp-'+cfg.lane+'-tools')throw rej('Invalid fixed origins');
 if(cfg.rollout_flag!=='HOSTED_MEMORY_'+cfg.lane.toUpperCase()+'_READ_ENABLED'||
  cfg.credential_var!=='HOSTED_MEMORY_'+cfg.lane.toUpperCase()+'_READ_TOKEN')throw rej('Invalid runtime binding');
 const policy=shared.catalog.tools;
 const effectOn=cfg.enabled_capabilities.includes('intake_process');
 const prefix='HOSTED_MEMORY_'+cfg.lane.toUpperCase(),
       otherPrefix='HOSTED_MEMORY_'+(cfg.lane==='test'?'PROD':'TEST'),
       effectFlag=prefix+'_EFFECTS_ENABLED',effectVar=prefix+'_EFFECTS_TOKEN',genFlag=prefix+'_GENERATE_ENABLED',
       foreignEffect=otherPrefix+'_EFFECTS_TOKEN',foreignRead=otherPrefix+'_READ_TOKEN';
 const gen=cfg.enabled_capabilities.includes('generate');
 if(gen&&(!effectOn||!policy[QUERY]?.capability_projections?.generate||
   !Number.isSafeInteger(cfg.query_timeout_ms)||cfg.query_timeout_ms<11000||cfg.query_timeout_ms>250000))throw rej('Invalid generation profile');
 let effectGateway;
 if(effectOn){
  if(!SHA.test(cfg.read_catalog_sha256)||cfg.read_catalog_sha256===cfg.catalog_sha256)throw rej('Invalid original read catalog');
  effectGateway=new URL(cfg.effect_gateway_url);
  if(effectGateway.href!==gateway.origin+'/memory-mcp-'+cfg.lane+'-effects'||
    [...EFFECTS].some(n=>!policy[n]?.capability_projections?.intake_process)||
    Object.entries(policy).some(([n,m])=>m.capability_projections?.intake_process&&!EFFECTS.has(n)))throw rej('Invalid effect profile');
 } else if(cfg.effect_gateway_url!==undefined)throw rej('Invalid effect profile');
 const effectEnabled=env=>effectOn&&env?.[effectFlag]==='true'&&
   typeof env[effectVar]==='string'&&SHA.test(env[effectVar])&&
   typeof env[cfg.credential_var]==='string'&&SHA.test(env[cfg.credential_var])&&
   env[effectVar]!==env[cfg.credential_var]&&env[foreignEffect]===undefined&&env[foreignRead]===undefined;
 const readEnabled=env=>env?.[cfg.rollout_flag]==='true';
 const enabled=env=>readEnabled(env)||effectEnabled(env);
 const capability=(name,env)=>name===QUERY&&gen&&effectEnabled(env)&&env[genFlag]==='true'?'generate':
   EFFECTS.has(name)&&effectEnabled(env)?'intake_process':
   (policy[name]?.classification==='baseline_read'&&readEnabled(env)?'baseline_read':null);
 return Object.freeze({
  enabled,
  catalog(legacy,env){
   if(!Array.isArray(legacy))throw rej('Invalid legacy catalog');
   const names=new Set(legacy.map(x=>x?.name));
   if(!enabled(env))return [...legacy];
   const current=Object.entries(policy).filter(([n])=>capability(n,env)).map(([name,p])=>{
    const cap=capability(name,env),read=cap==='baseline_read';
    if(names.has(name))throw rej('Legacy catalog collision');
    return {name,description:cfg.tool_descriptions?.[name]??(read?'Read a bounded canonical memory result.':
      (cap==='generate'?'Generate and persist the canonical memory answer if needed.':
       (name==='memory_capture_message'?'Durably capture an owner-authorized source message in this environment.':'Schedule existing canonical processing in this environment.'))),
     inputSchema:structuredClone(p.capability_projections[cap]),annotations:{readOnlyHint:read,destructiveHint:false,openWorldHint:false}};
   });
   return [...legacy,...current];
  },
  async call(name,args,req,env,legacyNames=[]){
   // Exact trusted legacy descriptors are allowed to continue handling their own methods.
   if(legacyNames.includes(name))return null;
   if(!Object.hasOwn(policy,name))return null;
   const cap=capability(name,env); if(!cap||!object(args))return fail();
   const effect=cap!=='baseline_read';
   try {
    const u=new URL(req.url),token=env[effect?effectVar:cfg.credential_var];
    if(u.origin!==site.origin||u.pathname!=='/mcp'||u.search||req.method!=='POST'||
       req.headers.get('sec-fetch-site')==='cross-site'||typeof token!=='string'||!SHA.test(token)||env[foreignRead]!==undefined||env[foreignEffect]!==undefined||
       (effect&&(token===env[cfg.credential_var]||!(typeof env[cfg.credential_var]==='string'&&SHA.test(env[cfg.credential_var]))))||
       await verifyOwner(req,env,cfg.lane)!==true)return fail('Owner authorization required.');
    const ref=uuid();
    if(!UUID.test(ref))return fail();
    const body=JSON.stringify({request_id:ref,operation:name,arguments:args});
    if(new TextEncoder().encode(body).length>100000)return fail();
    const res=await limited(await fetcher((effect?effectGateway:gateway).href,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(cap==='generate'?cfg.query_timeout_ms:8000),
      headers:{'content-type':'application/json',accept:'application/json',
       ['x-memory-mcp-'+cfg.lane+(effect?'-effects-auth':'-read-auth')]:'Bearer '+token},body}));
    if(!permitted(res,['environment','release','request_id','catalog_sha256','result'])||
     res.environment!==cfg.lane||res.release!==cfg.base_release||res.catalog_sha256!==(effect?cfg.catalog_sha256:(cfg.read_catalog_sha256??cfg.catalog_sha256))||res.request_id!==ref||
     !permitted(res.result,['content','structuredContent'])||!Array.isArray(res.result.content)||res.result.content.length<1||
     res.result.content.length>5||!res.result.content.every(x=>permitted(x,['type','text'])&&x.type==='text'&&typeof x.text==='string'))return fail();
    return {content:res.result.content,...(object(res.result.structuredContent)?{structuredContent:res.result.structuredContent}:{})};
   } catch {return fail();}
  }
 });
}

// Reuse the exact legacy response for server/discover and initialize, retaining serverInfo.
// During migration wrap existing tools/list and intercept only our profile's own operations.
export function withHostedMcp({legacyFetch,legacyNames,relay}){
 if(typeof legacyFetch!=='function'||!Array.isArray(legacyNames)||new Set(legacyNames).size!==legacyNames.length)
  throw rej('Invalid legacy surface');
 const names=Object.freeze([...legacyNames]);
 const json=(body)=>Response.json(body,{headers:{'cache-control':'private, no-store','x-content-type-options':'nosniff'}});
 const error=(id,code,message)=>json({jsonrpc:'2.0',id,error:{code,message}});
 return async (req,env,ctx)=>{
  if(!relay.enabled(env)||new URL(req.url).pathname!=='/mcp'||new URL(req.url).search||req.method!=='POST')
   return legacyFetch(req,env,ctx);
  let msg;
  // Read bounded cloned envelope. The native parser's existing behavior applies when relay is dark.
  try {
   const rd=req.clone().body?.getReader();if(!rd)throw Error();
   try {const d=new TextDecoder('utf-8',{fatal:true});let count=0,s='';
    for(;;){const {done,value}=await rd.read();if(done)break;count+=value.length;if(count>100000)throw Error();s+=d.decode(value,{stream:true});}
    msg=JSON.parse(s+d.decode());
   } finally {void rd.cancel().catch(()=>{});rd.releaseLock();}
  } catch {return error(null,-32700,'Invalid MCP request.');}
  if(!object(msg)||msg.jsonrpc!=='2.0'||!Object.hasOwn(msg,'id')||
   !(msg.id===null||(typeof msg.id==='string'&&msg.id.length<=512)||Number.isSafeInteger(msg.id)))
   return legacyFetch(req,env,ctx);
  if(msg.method==='tools/list'){
   const p=msg.params;
   if(p!==undefined&&(!permitted(p,['cursor','_meta'])||p.cursor!=null||
     (Object.hasOwn(p,'_meta')&&!object(p._meta))))return error(msg.id,-32602,'Invalid tool list parameters.');
   try{
    const previous=await limited(await legacyFetch(req,env,ctx));
    if(previous.error)return json(previous);
    const tools=previous.result?.tools;
    if(previous.jsonrpc!=='2.0'||previous.id!==msg.id||!Array.isArray(tools)||
     tools.length!==names.length||!names.every(n=>tools.some(x=>x.name===n)))throw Error();
    return json({...previous,result:{...previous.result,tools:relay.catalog(tools,env)}});
   }catch{return error(msg.id,-32603,'Tool catalog unavailable.');}
  }
  if(msg.method==='tools/call'){
   const p=msg.params;
   if(!permitted(p,['name','arguments','_meta'])||typeof p.name!=='string'||
      (Object.hasOwn(p,'_meta')&&!object(p._meta)))return error(msg.id,-32602,'Invalid tool call.');
   if(names.includes(p.name))return legacyFetch(req,env,ctx);
   const result=await relay.call(p.name,p.arguments??{},req,env,names);
   if(result!==null)return json({jsonrpc:'2.0',id:msg.id,result});
  }
  return legacyFetch(req,env,ctx);
 };
}
