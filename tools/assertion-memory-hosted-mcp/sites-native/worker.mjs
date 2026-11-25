// Single owner-private source. Runtime credentials are bindings, never profile values.
import {attachReleaseMcp} from '../sites-dynamic/worker.mjs';
import {graphReader} from './graph.mjs';
import {withVisualAtMcpOrigin} from './visual.mjs';

const hex=/^[0-9a-f]{64}$/;
const profiles={
  test:{graphKey:'MEMORY_GRAPH_TEST_TOKEN',oppositeGraphKeys:['MEMORY_GRAPH_PROD_TOKEN','MEMORY_GRAPH_TOKEN'],
        oppositeVisualKey:'MEMORY_VISUAL_PROD_TOKEN',graphPath:'/memory-graph-test-read'},
  prod:{graphKey:'MEMORY_GRAPH_TOKEN',oppositeGraphKeys:['MEMORY_GRAPH_TEST_TOKEN'],
        oppositeVisualKey:'MEMORY_VISUAL_TEST_TOKEN',graphPath:'/memory-graph-read'},
};
const dns='[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
const site=new RegExp('^https://'+dns+'\\.'+dns+'\\.chatgpt\\.site$');
const gateway=new RegExp('^https://'+dns+'\\.'+dns+'\\.apigw\\.yandexcloud\\.net$');
const keys=['lane','siteOrigin','gatewayOrigin'];
export function canonicalProfile(config){
  if(!config||Object.getPrototypeOf(config)!==Object.prototype||
     Object.keys(config).length!==keys.length||!keys.every(k=>Object.hasOwn(config,k))||
     !Object.hasOwn(profiles,config.lane)||!site.test(config.siteOrigin)||!gateway.test(config.gatewayOrigin))
     throw Error('Invalid owner-private stage profile');
  const {lane,siteOrigin,gatewayOrigin}=config;
  const transport=profiles[lane];
  const capability=lane==='test'?'TEST':'PROD';
  return Object.freeze({lane,siteOrigin,graphKey:transport.graphKey,
     oppositeGraphKeys:Object.freeze([...transport.oppositeGraphKeys]),
     visualKey:'MEMORY_VISUAL_'+capability+'_TOKEN',oppositeVisualKey:transport.oppositeVisualKey,
     graphUrl:gatewayOrigin+transport.graphPath,
     visualUrl:gatewayOrigin+'/memory-visual-'+lane+'-read',
     readUrl:gatewayOrigin+'/memory-mcp-'+lane+'-tools',
     effectsUrl:gatewayOrigin+'/memory-mcp-'+lane+'-effects',
     ...(lane==='test'?{pauseUrl:gatewayOrigin+'/memory-visual-test-pause'}:{})});
}
const digest=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
  new TextEncoder().encode(value))),b=>b.toString(16).padStart(2,'0')).join('');
const equal=(a,b)=>{let d=0;for(let i=0;i<64;i++)d|=a.charCodeAt(i)^b.charCodeAt(i);return d===0;};
const knownId=id=>id&&id.length<=250&&!/[\s,]/.test(id);

export function nativeSite(config,{legacy,testOwner,pause, fallback,fetcher=(...p)=>globalThis.fetch(...p)}={}){
  const p=canonicalProfile(config);
  if(typeof legacy?.fetch!=='function'||(p.lane==='test'&&typeof testOwner!=='function')||
     (p.lane==='prod'&&(testOwner!==undefined||pause!==undefined))||
     (pause!==undefined&&typeof pause!=='function'))
    throw Error('Verified corresponding lane adapters required');
  const verifyOwner=async(req,env,lane)=>{
    const url=new URL(req.url),id=req.headers.get('oai-authenticated-user-id');
    if(lane!==p.lane||url.origin!==p.siteOrigin||url.pathname!=='/mcp'||url.search||
       req.method!=='POST'||req.headers.get('sec-fetch-site')==='cross-site'||!knownId(id))
      return false;
    const isTest=p.lane==='test', prefix='HOSTED_MEMORY_'+(isTest?'TEST':'PROD');
    const forbidden=isTest?['HOSTED_MEMORY_PROD_READ_TOKEN','HOSTED_MEMORY_PROD_EFFECTS_TOKEN',
      'MEMORY_PERSONAL_TOKEN','MEMORY_GRAPH_PROD_TOKEN','MEMORY_GRAPH_TOKEN','MEMORY_VISUAL_PROD_TOKEN']:
      ['HOSTED_MEMORY_TEST_READ_TOKEN','HOSTED_MEMORY_TEST_EFFECTS_TOKEN',
       'MEMORY_MCP_TEST_TOKEN','MEMORY_GRAPH_TEST_TOKEN','MEMORY_VISUAL_TEST_TOKEN'];
    const separate=isTest?['MEMORY_MCP_TEST_TOKEN','MEMORY_GRAPH_TEST_TOKEN','MEMORY_VISUAL_TEST_TOKEN']:
      ['MEMORY_PERSONAL_TOKEN','MEMORY_VM_SMOKE_TOKEN','MEMORY_GRAPH_PROD_TOKEN','MEMORY_GRAPH_TOKEN','MEMORY_VISUAL_PROD_TOKEN'];
    if(forbidden.some(k=>env[k]!==undefined)||separate.some(k=>env[k]!==undefined&&
       [env[prefix+'_READ_TOKEN'],env[prefix+'_EFFECTS_TOKEN']].includes(env[k])))return false;
    if(isTest)return await testOwner(req,env)===true;
    const pin=env.MEMORY_PERSONAL_OWNER_SHA256;
    return hex.test(pin??'')&&equal(await digest('assertion-memory-owner/1:'+id),pin);
  };
  const graph=graphReader(p,fetcher);
  const mcp=attachReleaseMcp({legacyFetch:(...args)=>legacy.fetch(...args),
      profile:{lane:p.lane,siteOrigin:p.siteOrigin,readUrl:p.readUrl,effectsUrl:p.effectsUrl,
                includeEffects:true,...(p.lane==='test'?{effectsTimeoutMs:10000}:{})},
      verifyOwner,fetcher});
  const previous={fetch(req,env,ctx){return new URL(req.url).pathname==='/mcp'?mcp(req,env,ctx):legacy.fetch(req,env,ctx);}};
  const visual=withVisualAtMcpOrigin(p,graph,previous,fallback,fetcher);
  return {async fetch(req,env,ctx){
    const u=new URL(req.url);
    // Neither a request parameter nor header can choose a lane or gateway.
    if(u.origin!==p.siteOrigin||env.MEMORY_GRAPH_MODE!==p.lane)return new Response(null,{status:421});
    if(pause&&(u.pathname==='/api/v1/processing/pause'||u.pathname==='/api/v1/processing/pause/prepare')){
      // Product-specific write adapter owns its separate Origin/enrollment/CSRF/binding checks.
      return (await pause(req,env,ctx))??new Response(null,{status:404});
    }
    return visual.fetch(req,env,ctx);
  }};
}
