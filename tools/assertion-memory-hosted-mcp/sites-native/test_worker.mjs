import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {test} from 'node:test';
import {canonicalProfile,nativeSite} from './worker.mjs';
globalThis.crypto??=webcrypto;
const sha=x=>createHash('sha256').update(x).digest('hex');
const gatewayOrigin='https://relay.example.apigw.yandexcloud.net';
const d='a'.repeat(64);
const email='fictional@example.test', id='fictional-owner';
const mk=lane=>({lane,siteOrigin:'https://'+lane+'.example.chatgpt.site',gatewayOrigin});
const headers={'oai-authenticated-user-email':email,'oai-authenticated-user-id':id,
  'sec-fetch-site':'same-origin'};
const own='MEMORY_GRAPH_OWNER_EMAIL_SHA256';
function setup(lane){
  const config=mk(lane),p=canonicalProfile(config),calls=[];
  const pre='HOSTED_MEMORY_'+lane.toUpperCase();
  const env={MEMORY_GRAPH_MODE:lane,[own]:sha(email),[p.graphKey]:'b'.repeat(64),[p.visualKey]:'c'.repeat(64),
    [pre+'_DYNAMIC_CATALOG_ENABLED']:'true',[pre+'_READ_TOKEN']:'d'.repeat(64),
    ...(lane==='prod'?{MEMORY_PERSONAL_OWNER_SHA256:sha('assertion-memory-owner/1:'+id)}:{})};
  const fetcher=async(url,options)=>{
    assert.equal(new URL(url).origin,gatewayOrigin);
    const body=JSON.parse(options.body);
    calls.push({url,options,body});
    if(url===p.visualUrl)return Response.json({digest:d});
    if(url===p.graphUrl)return Response.json({capabilities:{live:true},readiness:[]});
    if(url===p.readUrl)return Response.json({request_id:body.request_id,environment:lane,
      release:'assertion-memory-v0.2.0',generation:d,catalog_sha256:d,capability:'baseline_read',tools:[]});
    throw Error('unexpected transport');
  };
  const app=nativeSite(config,{fetcher,legacy:{fetch:()=>new Response('legacy')},
    ...(lane==='test'?{testOwner:req=>req.headers.get('oai-authenticated-user-id')===id}:{})});
  const call=(path,options={},extra={})=>app.fetch(new Request(p.siteOrigin+path,
    {...options,headers:{...headers,...options.headers}}),{...env,...extra});
  return {p,env,call,calls};
}
for(const lane of ['test','prod'])test('canonical '+lane+' retains scoped alias, Graph and MCP',async()=>{
  const f=setup(lane);
  const alias=await f.call('/visual/?x=1&x=2');
  assert.equal(alias.status,302);
  assert.equal(alias.headers.get('location'),'/visual/'+d+'/?x=1&x=2');
  const dashboard=await f.call('/api/v1/capabilities?queue_status=1&phase=extract&limit=10');
  assert.equal(dashboard.status,200);
  assert.equal((await dashboard.json()).capabilities.live,false);
  assert.equal(f.calls[1].body.path,'/api/v1/capabilities?queue_status=1&phase=extract&limit=10');
  const opts={method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/list'})};
  const catalog=await f.call('/mcp',opts);
  assert.deepEqual((await catalog.json()).result.tools,[]);
  assert.deepEqual(f.calls.map(x=>x.url),[f.p.visualUrl,f.p.graphUrl,f.p.readUrl]);
  assert.equal((await f.call('/visual/',{}, {MEMORY_GRAPH_MODE:lane==='test'?'prod':'test'})).status,421);
  assert.equal((await f.call('/visual/',{headers:{'oai-authenticated-user-email':'wrong@example.test'}})).status,403);
  assert.equal((await f.call('/api/v1/processing/pause/prepare',{method:'POST'})).status,405);
  const mixed=await f.call('/mcp',opts,{[f.p.graphKey]:f.env['HOSTED_MEMORY_'+lane.toUpperCase()+'_READ_TOKEN']});
  assert.equal((await mixed.json()).error.code,-32001);
  assert.equal(f.calls.length,3);
});
test('profiles are compile-time narrow data, not URL routing or secrets',()=>{
 assert.throws(()=>canonicalProfile({...mk('prod'),token:'secret'}));
 assert.throws(()=>canonicalProfile({...mk('test'),gatewayOrigin:'https://relay.example.apigw.yandexcloud.net/other'}));
 assert.throws(()=>canonicalProfile({...mk('prod'),siteOrigin:'http://prod.example.chatgpt.site'}));
 assert.throws(()=>nativeSite(mk('prod'),{legacy:{fetch(){}},pause(){}}));
});

test('TEST enrollment adapter reads only the existing D1 binding, rejects env fallback', async()=>{
  const {matchesEnrolledTestOwner}=await import('./test-owner.mjs');
  let reads=0;
  const req=new Request(mk('test').siteOrigin+'/mcp',{headers});
  const env={MEMORY_MCP_TEST_OWNER_BINDING:'d1',DB:{prepare(sql){
    assert.match(sql,/^SELECT owner_sha256 FROM memory_test_owner_binding/);
    return {bind(scope,marker){
      assert.equal(scope,'assertion-memory-preview:TEST:owner-v1');
      assert.equal(marker,'private-synthetic-assertion-smoke/v1');
      return {first:async()=>{reads++;return {owner_sha256:sha(scope+'\0'+id)};}};
    }};
  }}};
  assert.equal(await matchesEnrolledTestOwner(req,env),true);
  assert.equal(await matchesEnrolledTestOwner(req,{...env,MEMORY_MCP_TEST_OWNER_SHA256:'a'.repeat(64)}),false);
  assert.equal(await matchesEnrolledTestOwner(new Request(req,{headers:{'oai-authenticated-user-id':'staging-principal-other'}}),env),false);
  assert.equal(reads,1);
});
