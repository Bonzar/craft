// Owner-private native Worker. Supply environment bindings and existing same-origin graph relay.
const dnsLabel = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
const siteHost = new RegExp('^'+dnsLabel+'\\.'+dnsLabel+'\\.chatgpt\\.site$');
const gatewayHost = new RegExp('^'+dnsLabel+'\\.'+dnsLabel+'\\.apigw\\.yandexcloud\\.net$');
const routes = {test:'/memory-visual-test-read',prod:'/memory-visual-prod-read'};
const hex=/^[a-f0-9]{64}$/;
const item=/^(?:index\.html|assets\/(?:graph\.worker-|index-)[a-zA-Z0-9_-]+\.(?:js|css))$/;
const SAFE={
  'cache-control':'private, no-store, no-transform','x-content-type-options':'nosniff',
  'referrer-policy':'no-referrer','x-frame-options':'DENY',
  'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
};
const LIMIT=2400000;
const latestAlias=path=>path==='/visual'||path==='/visual/';
async function owner(req,env,url) {
  const id=req.headers.get('oai-authenticated-user-id')||'';
  const email=req.headers.get('oai-authenticated-user-email')?.trim().toLowerCase()||'';
  const origin=req.headers.get('origin');
  const entry=(url.pathname==='/'||(env.MEMORY_GRAPH_MODE==='test'&&latestAlias(url.pathname))||/^\/visual\/[a-f0-9]{64}\/$/.test(url.pathname))&&req.method==='GET'&&req.headers.get('sec-fetch-mode')==='navigate'&&req.headers.get('sec-fetch-dest')==='document';
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
export function visualShell(mode, graph, fetcher=fetch){
  if(mode!=='test'&&mode!=='prod')throw Error('Unsupported visual scope');
  return {async fetch(req,env){
    const url=new URL(req.url);
    const res=(status,body=null,extra={})=>new Response(req.method==='HEAD'?null:body,{status,headers:{...SAFE,...extra}});
    if(url.protocol!=='https:'||!siteHost.test(env.MEMORY_GRAPH_SITE_HOST||'')||url.hostname!==env.MEMORY_GRAPH_SITE_HOST||url.port||url.username||url.password||env.MEMORY_GRAPH_MODE!==mode)return res(421);
    if(!await owner(req,env,url))return res(403);
    if(req.method!=='GET'&&req.method!=='HEAD')return res(405);
    if(url.pathname.startsWith('/api/'))return graph.fetch(req,env);
    const isTest=mode==='test';
    const token=env[isTest?'MEMORY_VISUAL_TEST_TOKEN':'MEMORY_VISUAL_PROD_TOKEN'];
    const graphToken=env[isTest?'MEMORY_GRAPH_TEST_TOKEN':'MEMORY_GRAPH_PROD_TOKEN'];
    const opposite=env[isTest?'MEMORY_VISUAL_PROD_TOKEN':'MEMORY_VISUAL_TEST_TOKEN'];
    const upstream=env.MEMORY_VISUAL_GATEWAY_HOST||'';
    if(!hex.test(token||'')||token===graphToken||opposite!==undefined||!gatewayHost.test(upstream))return res(503);
    const gateway='https://'+upstream+routes[mode];
    const path=url.pathname;
    const match=path.match(/^\/visual\/([a-f0-9]{64})\/(.*)$/);
    const name=match&&(match[2]||'index.html');
    const current=path==='/'||(mode==='test'&&latestAlias(path));
    if(!current&&(!match||!item.test(name)||(match[2]&&url.search)))return res(404);
    const relay=body=>fetcher(gateway,{method:'POST',redirect:'error',signal:req.signal,headers:{
      'content-type':'application/json','x-memory-visual-auth':'Bearer '+token,
    },body:JSON.stringify(body)});
    try{
      if(current){
        const remote=await relay({op:'current'});
        if(remote.status!==200||remote.headers.get('content-type')!=='application/json')throw Error('invalid pointer');
        const data=JSON.parse(new TextDecoder().decode(await bounded(remote,512)));
        if(Object.keys(data).join(',')!=='digest'||typeof data.digest!=='string'||!hex.test(data.digest))throw Error('invalid digest');
        return res(302,null,{location:'/visual/'+data.digest+'/'+url.search,'x-memory-visual-digest':data.digest});
      }
      const type=name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'text/javascript';
      const remote=await relay({op:'asset',digest:match[1],name});
      if(remote.status===404){await remote.body?.cancel();return res(404);}
      if(remote.status!==200||remote.headers.get('content-type')!==type){await remote.body?.cancel();throw Error('invalid response');}
      if(req.method==='HEAD'){await remote.body?.cancel();return res(200,null,{'content-type':type+'; charset=utf-8'});}
      return res(200,await bounded(remote,LIMIT),{'content-type':type+'; charset=utf-8'});
    }catch{return res(502);}
  }};
}
