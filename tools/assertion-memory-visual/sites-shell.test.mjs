import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import test from 'node:test';
import {visualShell} from './sites-shell.mjs';
const owner='owner@example.test', gateway='demo-scope.demo-dc.apigw.yandexcloud.net', digest='a'.repeat(64);
const host='graph-test.example-workspace.chatgpt.site';
const req=(path,login=true)=>new Request('https://'+host+path,{headers:login?{'oai-authenticated-user-email':owner,'oai-authenticated-user-id':'fixture'}:{}});
const graphToken='1'.repeat(64), assetToken='2'.repeat(64);
const env={MEMORY_GRAPH_MODE:'test',MEMORY_GRAPH_SITE_HOST:host,MEMORY_VISUAL_GATEWAY_HOST:gateway,
  MEMORY_GRAPH_TEST_TOKEN:graphToken,MEMORY_VISUAL_TEST_TOKEN:assetToken,MEMORY_GRAPH_OWNER_EMAIL_SHA256:createHash('sha256').update(owner).digest('hex')};
test('owner and scope guard, distinct visual gateway and version pin; graph remains existing relay',async()=>{
  const calls=[];
  const graph={fetch:async()=>new Response('unchanged graph')};
  const shell=visualShell('test',graph,async (url,opts)=>{
    calls.push({url,opts});
    assert.equal(url,'https://'+gateway+'/memory-visual-test-read');
    assert.deepEqual(Object.keys(opts.headers).sort(),['content-type','x-memory-visual-auth']);
    assert.equal(opts.headers['x-memory-visual-auth'],'Bearer '+assetToken);
    assert.equal(opts.redirect,'error');
    return Response.json({digest});
  });
  assert.equal((await shell.fetch(req('/',false),env)).status,403);
  assert.equal((await shell.fetch(req('/'),{...env,MEMORY_VISUAL_TEST_TOKEN:graphToken})).status,503);
  assert.equal((await shell.fetch(req('/'),{...env,MEMORY_GRAPH_MODE:'prod'})).status,421);
  assert.equal((await shell.fetch(req('/'),{...env,MEMORY_VISUAL_GATEWAY_HOST:'user.example'})).status,503);
  assert.equal(calls.length,0);
  const root=await shell.fetch(req('/?focus=test'),env);
  assert.equal(root.headers.get('location'),'/visual/'+digest+'/?focus=test');
  assert.match(root.headers.get('content-security-policy'),/connect-src 'self'/);
  assert.equal(await (await shell.fetch(req('/api/v1/graph'),env)).text(),'unchanged graph');
  assert.equal(calls.length,1);
});

test('admin bridge is opt-in, owner-bound, lane-pinned and payload-projecting',async()=>{
  const hash=createHash('sha256').update('fixture').digest('hex');
  const af={...env,MEMORY_VISUAL_ADMIN_ENABLED:'true',MEMORY_VISUAL_ADMIN_OWNER_ID_SHA256:hash,
    MEMORY_VISUAL_ADMIN_GATEWAY_HOST:gateway,MEMORY_VISUAL_TEST_ADMIN_TOKEN:'3'.repeat(64)};
  const baseTag='assertion-memory-v0.1.9';
  let upstreamCalls=0;
  const shell=visualShell('test',{fetch:()=>new Response('graph')},async(url,opts)=>{
    upstreamCalls++;
    assert.equal(url,'https://'+gateway+'/memory-visual-test-admin');
    assert.equal(opts.redirect,'error');
    assert.equal(opts.headers['x-memory-visual-admin-auth'],'Bearer '+af.MEMORY_VISUAL_TEST_ADMIN_TOKEN);
    const p=JSON.parse(opts.body);
    assert.equal(p.environment,'test');assert.equal(p.actor,hash);
    assert.match(p.request_id,/^[a-f0-9-]{36}$/);
    let result;
    if(p.action==='status'){
      assert.deepEqual(p.arguments,{limit:2,offset:0});
      result={stage:'test',release_version:baseTag,global_order:false,capabilities:{enroll_device:true,reset_database:true},
        queues:[{id:'source',status:'paused',ordering:'available_at_then_id',total:1,next_offset:null,
        items:[{id:'event-synthetic',status:'paused',phase:'extraction',position:null,available_at:null,text:'not returned'}]}]};
    }else if(p.action==='enroll_device')result={name:p.arguments.name,enrolled:true,bearer:'never return'};
    else if(p.action==='prepare_reset')result={stage:'test',confirmation_id:'one-time',expires_in:30,phrase:'ОЧИСТИТЬ TEST'};
    else if(p.action==='reset_database')result={stage:'test',reset:true};
    else assert.fail(p.action);
    return Response.json({request_id:p.request_id,environment:'test',release_version:baseTag,result});
  });
  const post=(path,body,site='same-origin',origin='https://'+host)=>new Request('https://'+host+path,{
    method:'POST',body:JSON.stringify(body),headers:{'oai-authenticated-user-email':owner,
      'oai-authenticated-user-id':'fixture','origin':origin,'sec-fetch-site':site,'content-type':'application/json'}});
  const s='/visual/admin/status?limit=2', d='/visual/admin/devices', r='/visual/admin/reset';
  assert.equal((await shell.fetch(req(s),env)).status,404);
  assert.equal((await shell.fetch(req(s,false),af)).status,403);
  assert.equal((await shell.fetch(req(s),{...af,MEMORY_VISUAL_ADMIN_OWNER_ID_SHA256:'f'.repeat(64)})).status,403);
  assert.equal((await shell.fetch(req(s),{...af,MEMORY_VISUAL_PROD_ADMIN_TOKEN:'f'.repeat(64)})).status,503);
  assert.equal((await shell.fetch(req(s),{...af,MEMORY_VISUAL_TEST_ADMIN_TOKEN:graphToken})).status,503);
  assert.equal((await shell.fetch(req(s+'&limit=2'),af)).status,400);
  assert.equal((await shell.fetch(post(r+'/prepare',{},'cross-site','https://other.test'),af)).status,403);
  assert.equal((await shell.fetch(post(r+'/prepare',{},'same-origin','https://other.test'),af)).status,403);
  assert.equal((await shell.fetch(post(d,{name:'client',fingerprint:'x',token:'literal'}),af)).status,400);
  assert.equal(upstreamCalls,0);
  const status=await shell.fetch(req(s),af);
  assert.equal(status.headers.get('cache-control'),'private, no-store, no-transform');
  assert.deepEqual((await status.json()).queues[0].items[0],
    {id:'event-synthetic',status:'paused',phase:'extraction',position:null,available_at:null});
  assert.deepEqual(await (await shell.fetch(post(d,{name:'client',fingerprint:'d'.repeat(64)}),af)).json(),{name:'client',enrolled:true});
  assert.deepEqual(await (await shell.fetch(post(r+'/prepare',{}),af)).json(),
    {confirmation_id:'one-time',phrase:'ОЧИСТИТЬ TEST',expires_in:30,stage:'test'});
  assert.deepEqual(await (await shell.fetch(post(r,{confirmation_id:'one-time',confirmation:'ОЧИСТИТЬ TEST'}),af)).json(),{stage:'test',reset:true});
  assert.equal(upstreamCalls,4);
});
