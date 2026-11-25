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
test('owner visual shell forwards existing endpoints without buffering the delegated SSE body',async()=>{
  const want='test:0123456789abcdef:3';
  let output,requests=[];
  const graph={fetch:async request=>{
    const u=new URL(request.url);
    requests.push(u.pathname+u.search);
    if(u.pathname.endsWith('/capabilities'))return Response.json({stage:'test'});
    assert.equal(request.headers.get('last-event-id'),want);
    return new Response(new ReadableStream({start(c){output=c;}}),{headers:{'content-type':'text/event-stream'}});
  }};
  const shell=visualShell('test',graph,()=>{throw Error('must not reach asset gateway');});
  const basePath='/api/v1/capabilities?queue_status=1&limit=1';
  assert.equal((await shell.fetch(req(basePath,false),env)).status,403);
  assert.equal((await (await shell.fetch(req(basePath),env)).json()).stage,'test');
  const request=req('/api/v1/events?channel=dashboard');
  request.headers.set('last-event-id',want);
  const response=await shell.fetch(request,env);
  const reader=response.body.getReader(), utf8=new TextDecoder(), encode=new TextEncoder();
  output.enqueue(encode.encode('event: queue_changed\n\n'));
  assert.equal(utf8.decode((await reader.read()).value),'event: queue_changed\n\n');
  output.enqueue(encode.encode('event: graph_changed\n\n'));
  assert.equal(utf8.decode((await reader.read()).value),'event: graph_changed\n\n');
  output.close();
  assert.equal((await reader.read()).done,true);
  assert.deepEqual(requests,[basePath,'/api/v1/events?channel=dashboard']);
});
test('stable visual aliases select the current archive, preserve queries and stay owner-only',async()=>{
  const historical='b'.repeat(64);
  const calls=[];
  let available=true;
  const shell=visualShell('test',{},async (_url,opts)=>{
    const command=JSON.parse(opts.body);calls.push(command);
    if(!available)return new Response(null,{status:503});
    return command.op==='current'?Response.json({digest}):new Response('historical',{headers:{'content-type':'text/html'}});
  });
  assert.equal((await shell.fetch(req('/visual',false),env)).status,403);
  assert.equal((await shell.fetch(req('/visual//'),env)).status,404);
  assert.equal(calls.length,0);
  const query='?focus=%2F&node=1&node=2';
  for(const url of ['/visual','/visual/']){
    const ownerLink=req(url+query);
    ownerLink.headers.set('sec-fetch-site','cross-site');
    ownerLink.headers.set('sec-fetch-mode','navigate');
    ownerLink.headers.set('sec-fetch-dest','document');
    const response=await shell.fetch(ownerLink,env);
    assert.equal(response.status,302);
    assert.equal(response.headers.get('location'),'/visual/'+digest+'/'+query);
    assert.equal(response.headers.get('x-memory-visual-digest'),digest);
    assert.match(response.headers.get('cache-control'),/no-store/);
  }
  assert.deepEqual(calls,[{op:'current'},{op:'current'}]);
  const old=await shell.fetch(req('/visual/'+historical+'/'),env);
  assert.equal(await old.text(),'historical');
  assert.deepEqual(calls.at(-1),{op:'asset',digest:historical,name:'index.html'});
  available=false;
  for(const url of ['/visual','/visual/']){
    const unavailable=await shell.fetch(req(url),env);
    assert.equal(unavailable.status,502);
    assert.equal(unavailable.headers.get('location'),null);
    assert.equal(unavailable.headers.get('x-memory-visual-digest'),null);
  }
});
test('native alias source transform fails closed on drift and never silently modifies another source',async()=>{
  const {addNativeTestVisualAlias,INPUT_SHA256,OUTPUT_SHA256}=await import('./native-test-visual-alias.mjs');
  assert.match(INPUT_SHA256,/^[0-9a-f]{64}$/);
  assert.match(OUTPUT_SHA256,/^[0-9a-f]{64}$/);
  assert.notEqual(INPUT_SHA256,OUTPUT_SHA256);
  assert.throws(()=>addNativeTestVisualAlias(Buffer.from('another published checkout')),/Unexpected native visual source/);
});
test('stage or source drift cannot generate an approved native route',async()=>{
  const {addNativeVisualAlias,OUTPUT_SHA256,PROD_OUTPUT_SHA256}=await import('./native-test-visual-alias.mjs');
  assert.match(PROD_OUTPUT_SHA256,/^[0-9a-f]{64}$/);
  assert.notEqual(OUTPUT_SHA256,PROD_OUTPUT_SHA256);
  for(const lane of [undefined,'PROD','both',''])assert.throws(()=>addNativeVisualAlias(Buffer.from('foreign'),lane),/Explicit native visual stage/);
  assert.throws(()=>addNativeVisualAlias(Buffer.from('foreign'),'prod'),/Unexpected native visual source/);
});
test('only retired versioned documents recover to the stable visual alias',async()=>{
  const old='e'.repeat(64);
  const shell=visualShell('test',{},async (_url,opts)=>{
    const command=JSON.parse(opts.body);
    if(command.op==='current')return Response.json({digest});
    return new Response(null,{status:command.digest===old?410:404});
  });
  const doc=await shell.fetch(req('/visual/'+old+'/?foo=%2F&foo=x'),env);
  assert.equal(doc.status,302);
  assert.equal(doc.headers.get('location'),'/visual?foo=%2F&foo=x');
  assert.match(doc.headers.get('cache-control'),/no-store/);
  for(const file of ['assets/index-a.js','assets/index-a.css','assets/graph.worker-a.js']){
    const asset=await shell.fetch(req('/visual/'+old+'/'+file),env);
    assert.equal(asset.status,404);
    assert.equal(asset.headers.get('location'),null);
    assert.equal(await asset.text(),'');
  }
  assert.equal((await shell.fetch(req('/visual/'+old+'/',false),env)).status,403);
  assert.equal((await shell.fetch(req('/visual/'+digest+'/'),env)).status,404);
});
test('native retired document adapter cannot alter an unrelated or unspecified stage source',async()=>{
  const {addRetiredDocumentRecovery}=await import('./native-visual-retired-document.mjs');
  const unrelated=Buffer.from('unreviewed source');
  for(const lane of ['test','prod'])assert.throws(()=>addRetiredDocumentRecovery(unrelated,lane),/Unexpected native visual source/);
  assert.throws(()=>addRetiredDocumentRecovery(unrelated,'__proto__'),/Exact visual stage required/);
});
