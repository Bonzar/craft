import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createHash,webcrypto} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const checkout=process.env.PROD_SITES_CHECKOUT;
globalThis.crypto??=webcrypto;
const hash=s=>createHash('sha256').update(s).digest('hex');

test('saved owner PROD Sites entry passes scoped queue query and keeps Graph read-only',
 {skip:!checkout}, async()=>{
  const dir=resolve(checkout);
  const paired=await readFile(resolve(dir,'visual-pair-shell.mjs'),'utf8');
  const host=/prod:\s*\['([a-z0-9.-]+\.chatgpt\.site)'/.exec(paired)?.[1];
  assert.ok(host,'missing scoped PROD hostname');
  const native=(await import(pathToFileURL(resolve(dir,'dist/server/index.js')))).default;
  assert.equal(typeof native.fetch,'function');
  const email='owner-fixture@example.test';
  const token='c'.repeat(64);
  const environment={MEMORY_GRAPH_MODE:'prod',MEMORY_GRAPH_TOKEN:token,
    MEMORY_GRAPH_OWNER_EMAIL_SHA256:hash(email),MEMORY_VISUAL_PROD_TOKEN:'a'.repeat(64)};
  const route='/api/v1/capabilities?queue_status=1&limit=1&offset=0';
  const revision='d'.repeat(64);
  const backend={stage:'prod',release_version:'assertion-memory-v1.2.3',
    release_generation:revision,queues:[{id:'source',items:[]}],global_order:false,
    capabilities:{live:true},readiness:[]};
  const savedFetch=globalThis.fetch;
  const hits=[];
  globalThis.fetch=async (url,options)=>{
    const to=new URL(url);
    assert.equal(to.protocol,'https:');
    assert.equal(to.pathname,'/memory-graph-read');
    assert.equal(options.method,'POST');
    assert.equal(options.redirect,'manual');
    assert.deepEqual(JSON.parse(options.body),{path:route});
    assert.equal(options.headers['x-memory-graph-auth'],'Bearer '+token);
    assert(!Object.keys(options.headers).some(k=>/^(?:cookie|origin|oai-authenticated-)/i.test(k)));
    hits.push(to.pathname);
    return Response.json(backend);
  };
  const req=(path,opts={})=>new Request('https://'+host+path,{
    method:opts.method??'GET',headers:opts.headers??{
      'sec-fetch-site':'same-origin','oai-authenticated-user-id':'fiction-owner',
      'oai-authenticated-user-email':email,origin:'https://'+host}});
  try{
    assert.equal((await native.fetch(req(route,{headers:{}}),environment)).status,403);
    assert.equal((await native.fetch(req('/api/v1/processing/pause',{method:'PUT'}),environment)).status,405);
    assert.equal(hits.length,0);
    const resp=await native.fetch(req(route),environment);
    assert.equal(resp.status,200);
    const body=await resp.json();
    assert.equal(body.stage,'prod');
    assert.equal(body.release_generation,revision);
    assert.deepEqual(body.queues,backend.queues);
    assert.equal(body.capabilities.live,false); // published snapshot transport
    assert.ok(body.readiness.some(row=>row.code==='sites_snapshot_transport'));
    assert.equal(hits.length,1);
  } finally {globalThis.fetch=savedFetch;}
});
