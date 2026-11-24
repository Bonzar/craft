import assert from 'node:assert/strict';
import test from 'node:test';
import {attachReleaseMcp} from './worker.mjs';

const tag='assertion-memory-v0.2.0',gen='f'.repeat(64);
const hash={read:'a'.repeat(64),effects:'b'.repeat(64)};
const secret={read:'c'.repeat(64),effects:'d'.repeat(64)};
const tx=(cap,name)=>({name,description:'Release descriptor',inputSchema:{type:'object',
 properties:{q:{type:'string'}}},annotations:{readOnlyHint:cap==='read'}});
function setup(lane,mutate=(value)=>value,profilePatch={}){
 const pre='HOSTED_MEMORY_'+lane.toUpperCase(),siteOrigin='https://'+lane+'.chatgpt.site';
 const site='https://one.two.apigw.yandexcloud.net';
 const env={[pre+'_DYNAMIC_CATALOG_ENABLED']:'true',[pre+'_READ_TOKEN']:secret.read,
  [pre+'_EFFECTS_TOKEN']:secret.effects,[pre+'_EFFECTS_ENABLED']:'true'};
 let legacy=0;
 const wires=[];
 const handle=attachReleaseMcp({legacyFetch:()=>{legacy++;return Response.json({legacy:true});},
  profile:{lane,siteOrigin,readUrl:site+'/memory-mcp-'+lane+'-tools',
    effectsUrl:site+'/memory-mcp-'+lane+'-effects',includeEffects:true,...profilePatch},
  verifyOwner:req=>req.headers.get('authorized-for-test')==='true',
  fetcher:async (url,options)=>{
   assert.equal(options.method,'POST');
   assert.equal(options.redirect,'manual');
   assert.equal(new URL(url).origin,site);
   const cap=new URL(url).pathname.endsWith('-effects')?'effects':'read';
   const hp='x-memory-mcp-'+lane+(cap==='read'?'-read-auth':'-effects-auth');
   assert.equal(options.headers[hp],'Bearer '+secret[cap]);assert.equal(options.headers.cookie,undefined);
   const w=JSON.parse(options.body);wires.push({cap,w});
   const value={request_id:w.request_id,environment:lane,release:tag,generation:gen,catalog_sha256:hash[cap]};
   if(w.operation==='catalog'){
    assert.deepEqual(w.arguments,{});assert.equal(w.expected,undefined);
    value.capability=cap==='read'?'baseline_read':'intake_process';
    value.tools=[tx(cap,cap==='read'?'newly_added_by_base':'queued_by_base')];
   }else{
    assert.deepEqual(w.expected,{release:tag,generation:gen,catalog_sha256:hash[cap]});
    value.status='ok';
    value.result={message:'Receipt only'};
   }
   const out=mutate(value,{cap,w});return out instanceof Response?out:Response.json(out);
  }});
 const req=(method,params={},request={})=>new Request(siteOrigin+'/mcp'+(request.query??''),{method:'POST',
  headers:{'content-type':'application/json',...(request.anonymous?{}:{'authorized-for-test':'true'})},
  body:JSON.stringify({jsonrpc:'2.0',id:3,method,params})});
 return {env,pre,req,wires,legacy:()=>legacy,handle};
}
async function rpc(f,method,params={},override={},request){return (await f.handle(f.req(method,params,request),{...f.env,...override})).json();}

for(const lane of ['test','prod'])test(lane+' obtains only authoritative descriptors and binds calls to release',async()=>{
 const f=setup(lane);
 assert.deepEqual((await rpc(f,'tools/list',{_meta:{}})).result.tools.map(x=>x.name),['newly_added_by_base','queued_by_base']);
 const answer=await rpc(f,'tools/call',{name:'queued_by_base',arguments:{q:'args'}});
 assert.deepEqual(JSON.parse(answer.result.content[0].text),{message:'Receipt only'});
 assert.equal(f.wires.at(-1).cap,'effects');assert.equal(f.wires.at(-1).w.expected.catalog_sha256,hash.effects);
 const before=f.wires.filter(x=>x.w.operation!=='catalog').length;
 assert.equal((await rpc(f,'tools/call',{name:'legacy_name'})).error.code,-32602);
 assert.equal(f.wires.filter(x=>x.w.operation!=='catalog').length,before);
 assert.equal(f.legacy(),0);
});
test('dark and handshake preserve existing; unowned, foreign credentials, altered URL deny',async()=>{
 const f=setup('test');
 assert.deepEqual(await rpc(f,'tools/list',{}, {[f.pre+'_DYNAMIC_CATALOG_ENABLED']:'false'}),{legacy:true});
 assert.deepEqual(await rpc(f,'server/discover'),{legacy:true});
 assert.equal((await rpc(f,'tools/call',{name:'queued_by_base'}, {}, {query:'?wrong=1'})).error.code,-32600);
 assert.equal((await rpc(f,'tools/list',{}, {}, {anonymous:true})).error.code,-32001);
 assert.equal((await rpc(f,'tools/list',{}, {HOSTED_MEMORY_PROD_READ_TOKEN:secret.read})).error.code,-32603);
 assert.equal(f.wires.length,0);assert.equal(f.legacy(),2);
 assert.throws(()=>setup('test',undefined,{effectsUrl:'https://foreign.test/memory-mcp-test-effects'}));
});
test('reject cross-generation, duplicate tools, external schema refs, and missing catalogue without fallback',async()=>{
 for(const mutation of [
  (v,x)=>x.cap==='effects'?{...v,generation:'e'.repeat(64)}:v,
  (v,x)=>x.cap==='effects'?{...v,tools:[{...v.tools[0],name:'newly_added_by_base'}]}:v,
  (v,x)=>x.cap==='effects'?{...v,tools:[{...v.tools[0],inputSchema:{type:'object',properties:{q:{$ref:'https://foreign.test'}}}}]}:v,
  (v,x)=>x.cap==='effects'?{...v,environment:'prod'}:v,
  (v,x)=>x.cap==='effects'?new Response(null,{status:503}):v]){
  const f=setup('test',mutation);
  assert.equal((await rpc(f,'tools/list')).error.code,-32603);
  assert.equal(f.legacy(),0);
 }
});
test('stale release after discovery is not returned or retried',async()=>{
 const f=setup('prod',(v,{w})=>w.operation==='catalog'?v:{...v,generation:'e'.repeat(64)});
 const r=await rpc(f,'tools/call',{name:'queued_by_base'});
 assert.equal(r.result.isError,true);
 assert.equal(f.wires.length,3);
 assert.equal(f.legacy(),0);
});
