// Separate read-only mode for the exact QA bundle. Its module and worker bytes
// are unchanged; only this shell installs a page-local same-origin fetch map.
import {frozenQaApi} from './qa_frozen_loader.mjs';
import {ownerPreview} from './qa_visual_route.mjs';
import {readSyntheticQaWire,SyntheticQaWireError} from './synthetic_qa_wire.mjs';

export const savedExplorerPath='/explore/saved';
const bootstrapPath=savedExplorerPath+'/bootstrap.mjs';
const logical='/api/v1';
const savedApi=logical+'/saved';
const policy={
  'cache-control':'private, no-store',
  'x-content-type-options':'nosniff',
  'referrer-policy':'no-referrer',
  'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
};
const fault=(method,status,code,message)=>new Response(method==='HEAD'?null:JSON.stringify({error:{code,message}}),{
  status,headers:{...policy,'content-type':'application/json'},
});
const decode=b64=>new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(atob(b64),x=>x.charCodeAt(0)));

/** Run only in the isolated document before importing the unchanged QA entry. */
function installSavedApi() {
  const base=window.location;
  if(base.pathname!=='/explore/saved'&&base.pathname!=='/explore/saved/') throw new Error('Explorer mode unavailable.');
  const originalFetch=window.fetch.bind(window);
  window.fetch=(input,options)=>{
    // The pinned QA bundle only calls fetch with a logical URL string.
    // Reject unsupported inputs instead of accidentally falling through.
    if(typeof input!=='string'&&!(input instanceof URL))
      return Promise.reject(new TypeError('Unsupported graph request.'));
    const target=new URL(input,base.origin);
    if(target.origin!==base.origin||target.username||target.password||
       !target.pathname.startsWith('/api/v1/')||target.pathname.startsWith('/api/v1/saved/'))
      return Promise.reject(new TypeError('Invalid graph request.'));
    target.pathname='/api/v1/saved'+target.pathname.slice('/api/v1'.length);
    return originalFetch(target.pathname+target.search,{...options,redirect:'error'});
  };
}

export function savedExplorerShell(assets) {
  const source=assets?.['/index.html']?.base64;
  if(typeof source!=='string') throw new Error('Missing verified graph shell.');
  const html=decode(source);
  // Match precisely the pinned Vite entry. A changed layout must be audited,
  // not guessed; any other script in this constrained shell is out of bounds.
  const modules=[...html.matchAll(/<script type="module" crossorigin src="(\/assets\/index-[A-Za-z0-9_-]+\.js)"><\/script>/g)];
  if(modules.length!==1||[...html.matchAll(/<script\b/gi)].length!==1||!assets[modules[0][1]])
    throw new Error('Unexpected verified graph shell.');
  const [tag,originalEntry]=modules[0];
  return {
    html:html.replace(tag,`<script type="module" crossorigin src="${bootstrapPath}"></script>`),
    script:`(${installSavedApi.toString()})();\nawait import(${JSON.stringify(originalEntry)});\n`,
  };
}

export function createSavedExplorerRoute({assets,owner=ownerPreview}={}) {
  const shell=savedExplorerShell(assets);
  return async (request,env)=>{
    const url=new URL(request.url);
    const p=url.pathname, method=request.method;
    const page=p===savedExplorerPath||p===savedExplorerPath+'/';
    const bootstrap=p===bootstrapPath;
    const api=p===savedApi||p.startsWith(savedApi+'/');
    if(!page&&!bootstrap&&!api) return null;
    // For a reserved namespace always return a response, even on any error.
    // No method, auth result, query or missing D1 state can select frozen QA.
    if(!await owner(request)||(api&&request.headers.get('sec-fetch-site')==='cross-site'))
      return fault(method,401,'unauthorized','Sign in to the private test Site.');
    if(method!=='GET'&&method!=='HEAD')
      return fault(method,405,'method_not_allowed','Read-only graph.');
    if(page||bootstrap) return new Response(method==='HEAD'?null:(page?shell.html:shell.script),{
      headers:{...policy,'content-type':page?'text/html; charset=utf-8':'text/javascript; charset=utf-8'},
    });
    const apiPath=logical+p.slice(savedApi.length);
    if(apiPath===logical+'/session')
      return new Response(method==='HEAD'?null:JSON.stringify({authenticated:true,csrf_token:'synthetic-readonly'}),
        {headers:{...policy,'content-type':'application/json'}});
    const allowed=apiPath===logical+'/capabilities'||apiPath===logical+'/graph'||
      /^\/api\/v1\/graph\/[^/]+\/(?:nodes\.bin|edges\.bin|topics|(?:nodes|edges)\/\d+)$/.test(apiPath)||
      /^\/api\/v1\/(?:nodes|edges)\/[^/]+$/.test(apiPath);
    if(!allowed) return fault(method,404,'not_found','Not available in the saved synthetic graph.');
    try {
      const wire=await readSyntheticQaWire(env?.DB);
      const pinned=apiPath.match(/^\/api\/v1\/graph\/([^/]+)\//)?.[1];
      if(pinned&&pinned!==wire.manifest.revision)
        return fault(method,409,'revision_expired','Snapshot revision is unavailable; reload the graph.');
      url.pathname=apiPath;
      // Delegate to the same pure graph protocol adapter without HTTP relay,
      // inheriting the owner decision already verified for this exact request.
      const inner=new Request(url,{method,headers:request.headers});
      const route=frozenQaApi(wire,()=> 'synthetic-readonly',{rawTransport:true});
      return (await route(inner))??fault(method,404,'not_found','Not available in the saved synthetic graph.');
    } catch(error) {
      const code=error instanceof SyntheticQaWireError?error.code:'synthetic_graph_unavailable';
      return fault(method,503,code,'Verified synthetic graph is unavailable.');
    }
  };
}
