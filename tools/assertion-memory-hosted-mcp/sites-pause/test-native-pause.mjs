import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {createHash, webcrypto} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {attachTestPause} from './v25-bind.mjs';
globalThis.crypto??=webcrypto;
const root=process.env.V25_SITES_ROOT;
const hash=x=>createHash('sha256').update(x).digest('hex');
const fixtureHost='https://fixture.owner.chatgpt.site';
const gateway='https://demo.site.apigw.yandexcloud.net/memory-visual-test-pause';
const id='real-test-owner-fixture-109', email='fictional@example.test';
const path='/api/v1/processing/pause', rev='9'.repeat(32);
const baseEnv={MEMORY_GRAPH_MODE:'test',MEMORY_TEST_PAUSE_ENABLED:'true',MEMORY_MCP_TEST_OWNER_BINDING:'d1',
 MEMORY_GRAPH_OWNER_EMAIL_SHA256:hash(email),MEMORY_TEST_PAUSE_UPSTREAM_KEY:'b'.repeat(64),MEMORY_TEST_PAUSE_CSRF_KEY:'c'.repeat(64)};
function setup(real,host=fixtureHost,carrier=false){
 let hits=0, env={...baseEnv};
 env.DB={prepare(sql){
   assert.match(sql,/memory_test_owner_binding/);
   return {bind:(scope,marker)=>({first:async()=>{
     assert.equal(scope,'assertion-memory-preview:TEST:owner-v1');
     assert.equal(marker,'private-synthetic-assertion-smoke/v1');
     return {owner_sha256:hash(scope+'\0'+id)};
   }})};
 }};
 const native=attachTestPause({origin:host,gateway,withinEnrolledOwner:real,
     legacy:{fetch:async()=>new Response('unchanged legacy')},fetcher:async(url,o)=>{
   hits++;
   const target=carrier?new URL('/memory-mcp-test-effects',gateway).href:gateway;
   const header=carrier?'x-memory-mcp-test-effects-auth':'x-memory-test-pause-auth';
   assert.equal(url,target);assert.equal(o.redirect,'manual');
   assert.deepEqual(Object.keys(o.headers).sort(),['content-type',header]);
   assert.equal(o.headers[header],'Bearer '+'b'.repeat(64));
   const b=JSON.parse(o.body);
   assert.equal(b.actor,hash('native-queue-pause-test-v1\0'+id));
   if(b.action==='sessions_prepare'){
     assert.deepEqual(b.arguments,{});
     return Response.json({stage:'test',revision:rev,selected_session_ids:[],sessions:[{id:'some-session',queued:1,eligible:true}]});
   }
   if(b.action==='sessions_set')return Response.json({stage:'test',revision:'8'.repeat(32),selected_session_ids:b.arguments.selected_session_ids,sessions:[]});
   if(b.action==='item_preview')return Response.json({stage:'test',queue_id:'source',item_id:b.arguments.item_id,
     source_preview:'synthetic <text>',source_role:'user'});
   if(b.action==='item_remove')return Response.json({stage:'test',queue_id:b.arguments.queue_id,item_id:b.arguments.item_id,
     removed:true,effect:b.arguments.queue_id==='source'?'source_and_queue':'queue_only'});
   if(b.action==='prepare')assert.deepEqual(b.arguments,{});
   else {assert.equal(b.action,'set');assert.equal(b.arguments.expected_revision,rev);}
   return Response.json({stage:'test',paused:b.action==='set',revision:rev});
 }});
 const req=(target,body,patch={})=>new Request(host+target,{
  method:target.endsWith('/prepare')?'POST':'PUT',
  body:JSON.stringify(body),headers:{'content-type':'application/json','sec-fetch-site':'same-origin',origin:host,
    'oai-authenticated-user-id':id,'oai-authenticated-user-email':email,...patch}});
 return {env,req,fetch:native.fetch,hits:()=>hits};
}
async function suite(real,host=fixtureHost){
 const f=setup(real,host), {env,req}=f;
 assert.equal(await (await f.fetch(req('/elsewhere',{}),env)).text(),'unchanged legacy');
 assert.equal((await f.fetch(req(path+'/prepare',{}),{...env,MEMORY_TEST_PAUSE_ENABLED:'false'})).status,404);
 assert.equal((await f.fetch(req(path+'/prepare',{}, {origin:'https://evil.test'}),env)).status,403);
 assert.equal((await f.fetch(req(path+'/prepare',{}, {'oai-authenticated-user-id':'foreign'}),env)).status,403);
 assert.equal((await f.fetch(req(path+'/prepare',{}, {'oai-authenticated-user-email':'foreign@example.test'}),env)).status,403);
 assert.equal((await f.fetch(req(path+'/prepare',{}),{...env,MEMORY_GRAPH_TEST_TOKEN:'b'.repeat(64)})).status,503);
 const body={stage:'test',paused:true,expected_revision:rev};
 assert.equal((await f.fetch(req(path,body),env)).status,403);
 assert.equal(f.hits(),0);
 const ready=await f.fetch(req(path+'/prepare',{}),env);
 assert.equal(ready.status,200);
 const cookie=ready.headers.get('set-cookie');
 assert.match(cookie,/^__Host-assertion-test-pause=[a-f0-9]{64};.*Path=\/; HttpOnly; Secure; SameSite=Strict$/);
 const p=await ready.json();
 assert.equal(p.paused,false);assert.equal(p.revision,rev);assert.equal(p.stage,'test');
 const proof={cookie:cookie.split(';')[0],'x-csrf-token':p.csrf_token};
 const bad={...proof,cookie:proof.cookie.replace(/[a-f0-9]$/,'x')};
 assert.equal((await f.fetch(req(path,body,bad),env)).status,403);
 assert.equal((await f.fetch(req(path,{...body,stage:'prod'},proof),env)).status,400);
 assert.equal((await f.fetch(req(path,{...body,expected_revision:'e'.repeat(32)},proof),env)).status,403);
 const result=await f.fetch(req(path,body,proof),env);
 assert.equal(result.status,200);
 assert.deepEqual(await result.json(),{stage:'test',paused:true,revision:rev});
 assert.equal(f.hits(),2);
}
test('native transport smoke with pinned synthetic owner predicate',async()=>{
 await suite(async req=>req.headers.get('oai-authenticated-user-id')===id);
});
test('saved V25 owner rule and legacy paired HTTP route', {skip:!root}, async()=>{
 const src=resolve(root);
 const entry=await readFile(resolve(src,'preview_visual_entry.mjs'),'utf8');
 assert.match(entry,/export default withVisualAtMcpOrigin\('test',visualGraph,previous\);/);
 const enrollment=resolve(src,'test_owner_enrollment.mjs');
 const source=await readFile(enrollment,'utf8');
 const host=/^const origin='(https:\/\/[a-z0-9.-]+\.chatgpt\.site)';$/m.exec(source)?.[1];
 assert.ok(host,'exact native Site origin missing');
 const {matchesEnrolledOwner}=await import(pathToFileURL(enrollment));
 await suite(matchesEnrolledOwner,host);
 // Exercise actual published module graph in place, no source changes and no IO.
 const actual=(await import(pathToFileURL(resolve(src,'preview_visual_entry.mjs')))).default;
 const mounted=attachTestPause({origin:host,gateway,legacy:actual,withinEnrolledOwner:matchesEnrolledOwner});
 const f=setup(matchesEnrolledOwner,host);
 const req=new Request(host+'/api/v1/session',{headers:{
  'oai-authenticated-user-id':id,'oai-authenticated-user-email':email,'sec-fetch-site':'same-origin'}});
 const env={...f.env,MEMORY_TEST_PAUSE_ENABLED:'false'};
 const before=await actual.fetch(req.clone(),env);
 const after=await mounted.fetch(req.clone(),env);
 assert.equal(after.status,before.status);
 assert.equal(await after.text(),await before.text());
});

test('TEST owner pin attestation is mutually exclusive with writes and never reaches Gateway',async()=>{
 const f=setup(async req=>req.headers.get('oai-authenticated-user-id')===id);
 const env={MEMORY_GRAPH_MODE:'test',MEMORY_TEST_PAUSE_OWNER_ATTEST_ONLY:'true',
   MEMORY_GRAPH_OWNER_EMAIL_SHA256:hash(email)};
 const url=path+'/prepare';
 const response=await f.fetch(f.req(url,{}),env);
 assert.equal(response.status,200);
 assert.equal(response.headers.get('set-cookie'),null);
 assert.deepEqual(await response.json(),{stage:'test',owner_sha256:hash('native-queue-pause-test-v1\0'+id)});
 assert.equal((await f.fetch(f.req(url,{}),{...env,MEMORY_TEST_PAUSE_ENABLED:'true'})).status,404);
 assert.equal((await f.fetch(f.req(url,{}),{...env,MEMORY_GRAPH_MODE:'prod'})).status,404);
 assert.equal((await f.fetch(f.req(path,{stage:'test',paused:true,expected_revision:rev}),env)).status,404);
 assert.equal((await f.fetch(f.req(url,{}, {origin:'https://foreign.test'}),env)).status,403);
 assert.equal((await f.fetch(f.req(url,{}, {'oai-authenticated-user-id':'alien'}),env)).status,403);
 assert.equal((await f.fetch(f.req(url,{unexpected:true}),env)).status,400);
 assert.equal(f.hits(),0);
});

test('existing TEST transport preserves a dedicated pause bearer and rejects effects-key reuse',async()=>{
 const f=setup(async req=>req.headers.get('oai-authenticated-user-id')===id,fixtureHost,true);
 const env={...f.env,MEMORY_TEST_PAUSE_CARRIER:'effects',HOSTED_MEMORY_TEST_EFFECTS_TOKEN:'d'.repeat(64)};
 for(const patch of [{HOSTED_MEMORY_TEST_EFFECTS_TOKEN:env.MEMORY_TEST_PAUSE_UPSTREAM_KEY},
                     {HOSTED_MEMORY_TEST_EFFECTS_TOKEN:undefined},
                     {HOSTED_MEMORY_TEST_EFFECTS_TOKEN:'malformed'},
                     {MEMORY_TEST_PAUSE_CARRIER:'prod'}]){
   assert.equal((await f.fetch(f.req(path+'/prepare',{}),{...env,...patch})).status,503);
 }
 assert.equal(f.hits(),0);
 const prepare=await f.fetch(f.req(path+'/prepare',{}),env);
 assert.equal(prepare.status,200);
 const p=await prepare.json();
 const cookie=prepare.headers.get('set-cookie').split(';')[0];
 const res=await f.fetch(f.req(path,{stage:'test',paused:true,expected_revision:p.revision},
                            {cookie,'x-csrf-token':p.csrf_token}),env);
 assert.equal(res.status,200);
 assert.equal(f.hits(),2);
});


test('native TEST owner controls use only the existing carrier and exact action-scoped proof',async()=>{
 const f=setup(async req=>req.headers.get('oai-authenticated-user-id')===id,fixtureHost,true);
 const env={...f.env,MEMORY_TEST_QUEUE_CONTROL_ENABLED:'true',MEMORY_TEST_PAUSE_CARRIER:'effects',
   HOSTED_MEMORY_TEST_EFFECTS_TOKEN:'d'.repeat(64)};
 const selection='/api/v1/processing/sessions', preview='/api/v1/processing/items/source-preview', deletion='/api/v1/processing/items/remove';
 const body={stage:'test',queue_id:'source',item_id:'job',expected_revision:rev};
 assert.equal((await f.fetch(f.req(preview+'/prepare',body),{...env,MEMORY_TEST_QUEUE_CONTROL_ENABLED:'false'})).status,404);
 assert.equal((await f.fetch(f.req(preview+'/prepare',body),{...env,MEMORY_GRAPH_MODE:'prod'})).status,404);
 assert.equal((await f.fetch(f.req(preview+'/prepare',body,{'oai-authenticated-user-id':'foreign'}),env)).status,403);
 assert.equal((await f.fetch(f.req(preview+'/prepare',body,{origin:'https://evil.test'}),env)).status,403);
 assert.equal((await f.fetch(f.req(preview,body),env)).status,403);
 assert.equal(f.hits(),0);
 const ask=async (url,value)=>(await f.fetch(f.req(url+'/prepare',value),env)).json();
 const previewProof={'x-csrf-token':(await ask(preview,body)).csrf_token};
 assert.equal(f.hits(),0);
 assert.equal((await f.fetch(f.req(deletion,body,previewProof),env)).status,403);
 assert.equal((await f.fetch(f.req(preview,{...body,item_id:'other'},previewProof),env)).status,403);
 const text=await f.fetch(f.req(preview,body,previewProof),env);
 assert.equal((await text.json()).source_preview,'synthetic <text>');
 const deleteProof={'x-csrf-token':(await ask(deletion,body)).csrf_token};
 assert.equal((await (await f.fetch(f.req(deletion,body,deleteProof),env)).json()).effect,'source_and_queue');
 const state=await ask(selection,{});
 assert.deepEqual(state.selected_session_ids,[]);
 const saved=await f.fetch(f.req(selection,{stage:'test',selected_session_ids:['some-session'],expected_revision:state.revision},
     {'x-csrf-token':state.csrf_token}),env);
 assert.deepEqual((await saved.json()).selected_session_ids,['some-session']);
 assert.equal(f.hits(),4);
});
