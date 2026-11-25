const latestVisualAlias=path=>path==='/visual'||path==='/visual/';
export function buildPairedShell(profile, fallback){
const fallbackDigest=fallback?.digest, fallbackFiles=fallback?.files??{};
// Edge module is pure Web APIs. VM origin above is a separate process/identity.
const hex=/^[a-f0-9]{64}$/;
const item=/^(?:index\.html|assets\/(?:graph\.worker-|index-)[a-zA-Z0-9_-]+\.(?:js|css))$/;
const SAFE={
  'cache-control':'private, no-store, no-transform','x-content-type-options':'nosniff',
  'referrer-policy':'no-referrer','x-frame-options':'DENY',
  'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
};
const LIMIT=2400000;
async function owner(req,env,url) {
  const id=req.headers.get('oai-authenticated-user-id')||'';
  const email=req.headers.get('oai-authenticated-user-email')?.trim().toLowerCase()||'';
  const origin=req.headers.get('origin');
  const entry=(url.pathname==='/'||latestVisualAlias(url.pathname)||/^\/visual\/[a-f0-9]{64}\/$/.test(url.pathname))&&req.method==='GET'&&req.headers.get('sec-fetch-mode')==='navigate'&&req.headers.get('sec-fetch-dest')==='document';
  if(!id||id.length>512||!email||email.length>512||!hex.test(env.MEMORY_GRAPH_OWNER_EMAIL_SHA256||'')
     ||(req.headers.get('sec-fetch-site')==='cross-site'&&!entry)||(origin!==null&&origin!==url.origin))return false;
  const hashed=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(email))),b=>b.toString(16).padStart(2,'0')).join('');
  let difference=0;
  for(let j=0;j<64;j++)difference|=hashed.charCodeAt(j)^env.MEMORY_GRAPH_OWNER_EMAIL_SHA256.charCodeAt(j);
  return difference===0;
}
async function bounded(response,limit){
  const reader=response.body?.getReader();
  if(!reader)throw Error('body absent');
  const chunks=[];let size=0;
  while(true){
    const next=await reader.read();if(next.done)break;
    size+=next.value.length;if(size>limit){await reader.cancel();throw Error('size limit');}
    chunks.push(next.value);
  }
  const data=new Uint8Array(size);let offset=0;
  for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
  return data;
}

return function makePairedShell(stage,graph,fetcher=(...args)=>globalThis.fetch(...args)){
  if(stage!=='test'&&stage!=='prod') throw Error('unsupported visual environment');
  const {siteOrigin,visualKey,oppositeVisualKey:oppositeKey,graphKey,visualUrl:gateway}=profile;
  if(profile.lane!==stage)throw Error('profile mismatch');
  const host=new URL(siteOrigin).hostname;
  const shouldFallback=(env,failure)=>hex.test(fallbackDigest??'')&&env.MEMORY_VISUAL_PINNED_FALLBACK==='true'&&
    env.MEMORY_VISUAL_PINNED_FALLBACK_DIGEST===fallbackDigest&&
    (['gateway_transport','gateway_mime','gateway_body','pointer_shape'].includes(failure.reason)||
      failure.reason==='gateway_status'&&failure.status>=500&&failure.status<=599);
  async function pinnedAsset(name){
    if(!Object.hasOwn(fallbackFiles,name))return null;
    const entry=fallbackFiles[name];
    const compressed=Uint8Array.from(atob(entry.gz),x=>x.charCodeAt(0));
    const stream=new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
    const bytes=await bounded(new Response(stream),entry.bytes);
    const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
      x=>x.toString(16).padStart(2,'0')).join('');
    if(bytes.length!==entry.bytes||sha!==entry.sha)throw Error('pinned file mismatch');
    return bytes;
  }
  const healthPath='/__memory-visual-egress';
  const diagnosticMarker='visual-egress-v1:'+stage;
  const secureEqual=(a,b)=>{
    if(!hex.test(a??'')||!hex.test(b??''))return false;
    let difference=0;for(let i=0;i<64;i++)difference|=a.charCodeAt(i)^b.charCodeAt(i);
    return difference===0;
  };
  const visualToken=env=>{
    const token=env[visualKey];
    return hex.test(token??'')&&token!==env[graphKey]&&env[oppositeKey]===undefined?token:null;
  };
  const relay=(token,body,signal)=>fetcher(gateway,{method:'POST',redirect:'manual',signal,headers:{
    'content-type':'application/json','x-memory-visual-auth':'Bearer '+token,
  },body:JSON.stringify(body)});
  const failure=(reason,status,kind)=>({reason,status,kind});
  async function receive(token,body,signal,type,limit,isHead=false) {
    let remote;
    try {remote=await relay(token,body,signal);}
    catch(error){
      const kind=error?.name==='TypeError'&&error?.message==='Illegal invocation'?'IllegalInvocation':
        ['TypeError','AbortError','TimeoutError','NetworkError'].includes(error?.name)?error.name:'other';
      return failure('gateway_transport',undefined,kind);
    }
    const discard=async()=>{try{await remote.body?.cancel();}catch{}};
    // Workers supports manual, but rejects redirect:error before the request.
    // Never follow or return Link/Location from the authenticated upstream.
    if(remote.status>=300&&remote.status<400){await discard();return failure('gateway_redirect',remote.status);}
    if(remote.status!==200){await discard();return failure(
      remote.status===401||remote.status===403?'gateway_auth':'gateway_status',remote.status);}
    const mediaType=(remote.headers.get('content-type')??'').split(';',1)[0].trim().toLowerCase();
    if(mediaType!==type){await discard();return failure('gateway_mime',remote.status);}
    if(isHead){await discard();return {bytes:null};}
    try{return {bytes:await bounded(remote,limit)};}catch{return failure('gateway_body');}
  }
  async function current(token,signal){
    const remote=await receive(token,{op:'current'},signal,'application/json',512);
    if(remote.reason)return remote;
    let value;
    try{value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(remote.bytes));}
    catch{return failure('gateway_body');}
    if(!value||typeof value!=='object'||Object.keys(value).join(',')!=='digest'||
       !hex.test(value.digest??''))return failure('pointer_shape');
    return {digest:value.digest};
  }
  async function health(req,env,url,res){
    const key=env.MEMORY_VISUAL_DIAG_KEY, expiry=env.MEMORY_VISUAL_DIAG_UNTIL;
    const now=Math.floor(Date.now()/1000), token=visualToken(env);
    const expected=env.MEMORY_VISUAL_DIAG_EXPECTED;
    // Sites private dispatch authenticates service access before forwarding.
    // No user identity or incoming Sites credential is trusted here; the
    // independent route credential is always required.
    if(req.method!=='POST'||url.search||req.body!==null||
       env.MEMORY_VISUAL_DIAG_ENABLED!=='true'||
       !/^\d{10}$/.test(expiry??'')||+expiry<=now||+expiry>now+1800||
       env.MEMORY_VISUAL_DIAG_REVISION!==diagnosticMarker||
       req.headers.get('x-memory-visual-diag-revision')!==diagnosticMarker||
       !secureEqual(req.headers.get('x-memory-visual-diagnostic'),key)||
       key===token||key===env[graphKey]||key===env.MEMORY_MCP_TEST_TOKEN||key===env.MEMORY_PERSONAL_TOKEN||
       !hex.test(expected??''))return res(404);
    const got=token?await current(token,AbortSignal.timeout(7000)):failure('gateway_configuration');
    const code=got.reason??(got.digest===expected?'ok':'pointer_mismatch');
    const safe={code};
    if(Number.isInteger(got.status)&&got.status>=100&&got.status<=599)safe.status=got.status;
    return res(code==='ok'?200:502,JSON.stringify(safe),{'content-type':'application/json; charset=utf-8'});
  }
  return {async fetch(req,env){
    const url=new URL(req.url);
    const res=(status,body=null,extra={})=>new Response(req.method==='HEAD'?null:body,{status,headers:{...SAFE,...extra}});
    if(url.protocol!=='https:'||url.hostname!==host||url.port||url.username||url.password||env.MEMORY_GRAPH_MODE!==stage)return res(421);
    if(url.pathname===healthPath)return health(req,env,url,res);
    if(!await owner(req,env,url))return res(403);
    if(req.method!=='GET'&&req.method!=='HEAD')return res(405);
    if(url.pathname.startsWith('/api/')){
      const { [visualKey]: _credential, [oppositeKey]: _unrelated, MEMORY_VISUAL_DIAG_KEY: _diagnostic, ...graphEnv }=env;
      return graph.fetch(req,graphEnv);
    }
    const token=visualToken(env);
    if(!token)return res(503);
    const path=url.pathname;
    const match=path.match(/^\/visual\/([a-f0-9]{64})\/(.*)$/);
    const name=match&&(match[2]||'index.html');
    const isLatestAlias=latestVisualAlias(path);
    if(path!=='/'&&!isLatestAlias&&(!match||!item.test(name)||(match[2]&&url.search)))return res(404);
    const operation=path==='/'||isLatestAlias?'current':'asset';
    const fail=(reason,{status,kind}={})=>{
      // Only controlled labels and numeric HTTP statuses, no live header,
      // upstream exception message, response body, URL, or credential.
      const extra={'x-memory-visual-error':reason};
      if(Number.isInteger(status)&&status>=100&&status<=599)
        extra['x-memory-visual-upstream-status']=String(status);
      if(kind)extra['x-memory-visual-error-class']=kind;
      console.warn('memory_visual_gateway',stage,operation,reason,status??null,kind??null);
      return res(502,null,extra);
    };
    if(operation==='current'){
      const pointer=await current(token,AbortSignal.timeout(7000));
      if(pointer.reason){
        const rejected=fail(pointer.reason,pointer);
        if(isLatestAlias||!shouldFallback(env,pointer))return rejected;
        return res(302,null,{...Object.fromEntries(rejected.headers),
          location:'/visual/'+fallbackDigest+'/'+url.search,'x-memory-visual-digest':fallbackDigest,
          'x-memory-visual-source':'pinned-ci'});
      }
      return res(302,null,{location:'/visual/'+pointer.digest+'/'+url.search,'x-memory-visual-digest':pointer.digest});
    }
    const type=name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'text/javascript';
    const exact=await receive(token,{op:'asset',digest:match[1],name},AbortSignal.timeout(9500),type,LIMIT,req.method==='HEAD');
    if(exact.reason){
      if(exact.status===404)return res(404);
      const rejected=fail(exact.reason,exact);
      if(match[1]!==fallbackDigest||!shouldFallback(env,exact))return rejected;
      try{
        const bytes=await pinnedAsset(name);
        if(!bytes)return res(404);
        return res(200,bytes,{...Object.fromEntries(rejected.headers),
          'content-type':type+'; charset=utf-8','x-memory-visual-digest':fallbackDigest,
          'x-memory-visual-source':'pinned-ci'});
      }catch{return fail('pinned_asset_integrity');}
    }
    return res(200,exact.bytes,{'content-type':type+'; charset=utf-8','x-memory-visual-digest':match[1]});

  }};
}
}

// Keep native plugin and owner enrollment/other paths in the existing app.
export function withVisualAtMcpOrigin(profile, graph, existing, fallback, fetcher=(...args)=>globalThis.fetch(...args)) {
  const visual = buildPairedShell(profile,fallback)(profile.lane, graph, fetcher);
  return { async fetch(req, env, ctx) {
    const path=new URL(req.url).pathname;
    if(path==='/' || path==='/__memory-visual-egress' || (latestVisualAlias(path)) || path.startsWith('/visual/') || path.startsWith('/api/v1/'))
      return visual.fetch(req,env);
    return existing.fetch(req,env,ctx);
  }};
}
