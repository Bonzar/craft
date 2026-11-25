const SAFE = Object.freeze({'cache-control':'private, no-store, no-transform',
  'x-content-type-options':'nosniff','referrer-policy':'no-referrer','x-frame-options':'DENY',
  'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"});
const allowed = [
  /^\/api\/v1\/(?:capabilities|graph|search|bookmarks)$/,
  /^\/api\/v1\/graph\/[a-zA-Z0-9_-]{1,128}\/(?:nodes\.bin|edges\.bin|topics|sessions)$/,
  /^\/api\/v1\/graph\/[a-zA-Z0-9_-]{1,128}\/(?:nodes\/[0-9]{1,12}(?:\/edges)?|edges\/[0-9]{1,12})$/,
  /^\/api\/v1\/(?:nodes|edges|sources|overviews)\/[^/]{1,1600}$/,
  /^\/api\/v1\/nodes\/[^/]{1,1600}\/edges$/,
];
const HEX = /^[0-9a-f]{64}$/;
const json = (status, payload, head=false) => new Response(head?null:JSON.stringify(payload),{status,headers:{...SAFE,'content-type':'application/json; charset=utf-8'}});
const fail = (status, code, message, head) => json(status,{error:{code,message}},head);

export function relativeRead(url) {
  const path=url.pathname;
  if (path.length>1800 || url.search.length>4200 || /[\\\x00-\x20]|\/\//.test(path) || !allowed.some(pattern=>pattern.test(path))) return null;
  try {
    // No ambiguous decoded path segments, path delimiters, control or double decoding.
    if(path.split('/').some(segment=>{
      const decoded=decodeURIComponent(segment);
      return decoded==='.'||decoded==='..'||/[\\/\x00-\x1f\x7f%?#]/.test(decoded);
    })) return null;
    if(/[\x00-\x1f\x7f]/.test(decodeURIComponent(url.search))) return null;
  } catch { return null; }
  return path+url.search;
}

async function owner(req,env,url) {
  const id=req.headers.get('oai-authenticated-user-id')||'';
  const email=req.headers.get('oai-authenticated-user-email')?.trim().toLowerCase()||'';
  const origin=req.headers.get('origin');
  const topLevelEntry=url.pathname==='/'&&req.method==='GET'
    &&req.headers.get('sec-fetch-mode')==='navigate'&&req.headers.get('sec-fetch-dest')==='document';
  if(!id||id.length>512||!email||email.length>512||!HEX.test(env.MEMORY_GRAPH_OWNER_EMAIL_SHA256||'')
      ||(req.headers.get('sec-fetch-site')==='cross-site'&&!topLevelEntry)||(origin!==null&&origin!==url.origin)) return false;
  const raw=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(email));
  const actual=Array.from(new Uint8Array(raw),x=>x.toString(16).padStart(2,'0')).join('');
  let equal=0;
  for(let i=0;i<64;i++) equal |= actual.charCodeAt(i)^env.MEMORY_GRAPH_OWNER_EMAIL_SHA256.charCodeAt(i);
  return equal===0;
}

async function proxy(req,env,url,head,profile,fetcher) {
  const relative=relativeRead(url);
  if(!relative) return fail(404,'not_found','Маршрут недоступен.',head);
  const token=env[profile.graphKey];
  const other=profile.oppositeGraphKeys.some(key=>env[key]!==undefined);
  // Refuse mixed bindings, including equal values. Never try the other mode's key.
  if(other || !HEX.test(token||'')) return fail(503,'bridge_unavailable','Связь с графом на ВМ ещё не настроена.',head);
  const binary=/\/(nodes|edges)\.bin$/.test(url.pathname);
  let remote;
  try{
    remote=await fetcher(profile.graphUrl,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(7000),
      headers:{'content-type':'application/json','accept':binary?'application/octet-stream':'application/json',
        'x-memory-graph-auth':'Bearer '+token},
      body:JSON.stringify({path:relative})});
  }catch{return fail(502,'bridge_unavailable','Не удалось подключиться к графу на ВМ.',head);}
  if(remote.status<200||remote.status>=500||remote.status===401||remote.status===403
      ||remote.status>=300&&remote.status<400){
    await remote.body?.cancel();
    return fail(502,'bridge_unavailable','Граф на ВМ недоступен.',head);
  }
  const type=remote.headers.get('content-type')?.split(';',1)[0].trim().toLowerCase();
  if(type!==((remote.ok&&binary)?'application/octet-stream':'application/json')){
    await remote.body?.cancel();
    return fail(502,'bridge_format','Граф на ВМ вернул неподдерживаемый формат.',head);
  }
  if(head){await remote.body?.cancel();return new Response(null,{status:remote.status,headers:{...SAFE,'content-type':type}});}
  if(url.pathname==='/api/v1/capabilities'&&remote.ok){
    try{
      const value=await remote.json();
      if(!value||typeof value!=='object'||typeof value.capabilities?.live!=='boolean'||!Array.isArray(value.readiness)) throw new Error();
      value.capabilities.live=false;
      value.readiness.push({code:'sites_snapshot_transport',message:'Данные с ВМ обновляются при повторной загрузке. Потоковые обновления не подключены.'});
      return json(remote.status,value);
    }catch{return fail(502,'bridge_format','Граф на ВМ вернул неподдерживаемый формат.');}
  }
  const headers=new Headers({...SAFE,'content-type':type});
  for(const key of ['etag','retry-after']){
    const val=remote.headers.get(key);if(val)headers.set(key,val);
  }
  // Return original bytes directly. fetch normally decodes HTTP content codings;
  // never preserve content-length/encoding or replay an upstream cookie.
  return new Response(remote.body,{status:remote.status,headers});
}

export function graphReader(profile,fetcher=(...args)=>globalThis.fetch(...args)) {
  return {async fetch(req,env){
    const url=new URL(req.url),head=req.method==='HEAD';
    if(url.origin!==profile.siteOrigin||env.MEMORY_GRAPH_MODE!==profile.lane)return fail(421,'release_host_mismatch','Этот выпуск недоступен на данном адресе.',head);
    if(!await owner(req,env,url))return fail(403,'owner_required','Нужен вход владельца в ChatGPT.',head);
    if(req.method!=='GET'&&!head)return fail(405,'read_only','В этом окне граф доступен только для чтения.');
    if(url.pathname==='/api/v1/session'&&!url.search)return json(200,{authenticated:true},head);
    if(url.pathname==='/api/v1/events')return fail(501,'snapshot_only','Потоковые обновления не подключены.',head);
    if(url.pathname.startsWith('/api/'))return proxy(req,env,url,head,profile,fetcher);
    return fail(404,'not_found','Файл не найден.',head);
  }};
}
