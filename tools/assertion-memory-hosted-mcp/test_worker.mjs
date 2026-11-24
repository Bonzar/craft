
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {makeHostedRelay,withHostedMcp} from './worker.mjs';
const catalog64=readFileSync(new URL('./catalog.v011.review.json',import.meta.url)).toString('base64');
const vmTest=fileURLToPath(new URL('./test_vm.py',import.meta.url));
const catalog=JSON.parse(Buffer.from(catalog64,'base64'));
const hash=catalog.catalog_sha256,ref='842983c4-2df7-4fac-be9f-125a03aa42aa',tag='assertion-memory-v0.1.1';
let upstream=0;
const fixture=(lane)=>['memory_sessions','memory_history','original_'+lane].map(name=>({name,inputSchema:{type:'object'}}));
for(const lane of ['test','prod']){
 const tagLane=lane.toUpperCase(), other=lane==='test'?'prod':'test';
 const origin='https://example-'+lane+'.chatgpt.site';
 const p={lane,site_origin:origin,gateway_url:'https://gateway.fixture.apigw.yandexcloud.net/memory-mcp-'+lane+'-tools',
          catalog_sha256:hash,enabled_capabilities:['baseline_read'],base_release:tag,
          rollout_flag:'HOSTED_MEMORY_'+tagLane+'_READ_ENABLED',credential_var:'HOSTED_MEMORY_'+tagLane+'_READ_TOKEN'};
 const env={[p.rollout_flag]:'true',[p.credential_var]:(lane==='test'?'1':'2').repeat(64)};
 const old=fixture(lane), names=old.map(t=>t.name);
 const legacy=async req=>{
  const b=await req.json(); let result;
  if(b.method==='tools/list')result={tools:old};
  else if(b.method==='server/discover')result={supportedVersions:['2026-07-28'],serverInfo:{name:'legacy-'+lane,version:'1.0.0'},capabilities:{tools:{}}};
  else result={content:[{type:'text',text:'legacy-'+lane}]};
  return Response.json({jsonrpc:'2.0',id:b.id,result});
 };
 const relay=makeHostedRelay({profile:p,catalog,uuid:()=>ref,verifyOwner:(req,e,scope)=>scope===lane&&req.headers.get('oai-authenticated-user-id')===lane+'-mock-owner',
  fetcher:async(url,opts)=>{
   upstream++;
   assert.equal(url,p.gateway_url);
   assert.equal(opts.redirect,'manual');
   const b=JSON.parse(opts.body);
   assert(!Object.hasOwn(b,'environment'));
   const raw=opts.headers['x-memory-mcp-'+lane+'-read-auth'];
   const run=spawnSync('python',['-B',vmTest,catalog64,JSON.stringify({lane,body:b,path:new URL(url).pathname,bearer:raw.slice(7)})],
     {encoding:'utf8',maxBuffer:1024*1024});
   if(run.status!==0)throw Error(run.stderr);
   const [status,result]=JSON.parse(run.stdout);return Response.json(result,{status});
  }
 });
 const app=withHostedMcp({legacyFetch:legacy,legacyNames:names,relay});
 async function invoke(method,params={},opts={}){
  return (await app(new Request((opts.url??origin)+'/mcp',{method:'POST',headers:{'content-type':'application/json',
      'oai-authenticated-user-id':opts.owner??lane+'-mock-owner'},
      body:JSON.stringify({jsonrpc:'2.0',id:3,method,params})}),opts.env??env)).json();
 }
 const meta={progressToken:'fixture',example:{future:true}};
 assert.equal((await invoke('server/discover',{_meta:meta})).result.serverInfo.name,'legacy-'+lane);
 assert.deepEqual((await invoke('tools/list',{_meta:meta},{env:{}})).result.tools,old);
 const tools=(await invoke('tools/list',{_meta:meta})).result.tools;
 assert.equal(tools.length,5);assert.deepEqual(tools.slice(0,3),old);
 assert.equal((await invoke('tools/list',{_meta:[]})).error.code,-32602);
 assert.equal((await invoke('tools/call',{name:'memory_sessions',_meta:meta})).result.content[0].text,'legacy-'+lane);
 for(const name of ['memory_query','memory_process','memory_ingest_register','memory_ingest_report','memory_capture_message','memory_store_artifact'])
   assert((await invoke('tools/call',{name,_meta:meta})).result.isError);
 assert((await invoke('tools/call',{name:'memory_status'},{owner:other+'-mock-owner'})).result.isError);
 assert((await invoke('tools/call',{name:'memory_status'},{url:'https://evil.chatgpt.site'})).result.isError);
 const wrongEnv={...env,[p.credential_var]:(lane==='test'?'2':'1').repeat(64)};
 assert((await invoke('tools/call',{name:'memory_status'}, {env:wrongEnv})).result.isError);
 for(const [name,args,expected] of [['memory_status',{},'status-'+lane],['memory_provenance',{record_ids:['fixture']},'provenance-'+lane]]){
  const answer=(await invoke('tools/call',{name,arguments:args,_meta:meta})).result;
  assert.equal(JSON.parse(answer.content[0].text).fixture,expected);
 }
}
assert.equal(upstream,6);
console.log('Worker -> common ASGI -> canonical FastMCP: TEST and PROD synthetic lanes matched; opposite owner, token, host, effect tools and metadata failures held');
