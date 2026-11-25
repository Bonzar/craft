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
