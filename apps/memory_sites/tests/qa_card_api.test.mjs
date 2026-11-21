import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createQaCardApi } from '../qa_card_api.mjs';

const cwd = fileURLToPath(new URL('../../../',import.meta.url));
const ref = JSON.parse(execFileSync(process.env.PYTHON || 'python',
  ['-m','apps.memory_sites.tests.qa_card_oracle'],{cwd,encoding:'utf8'}));
const route = createQaCardApi({
  ownerSession: req => req.headers.has('x-synthetic-owner') ? ref.owner.csrf_token : null,
  loadRevision: revision => revision === ref.revision ? {
    card: ({kind,id,ordinal}) => ref.samples.find(s=>s.kind===kind && (id!=null?s.card.id===id:s.ordinal===ordinal))?.card
  } : null
});
const req = (path,owner=true,method='GET')=>new Request('https://test.invalid'+path,
  {method,headers:owner?{'x-synthetic-owner':'1'}:{}});

test('GET session and private-card denial match original', async()=>{
  assert.deepEqual(await (await route(req('/api/v1/session',false))).json(),ref.visitor);
  assert.deepEqual(await (await route(req('/api/v1/session'))).json(),ref.owner);
  const denied=await route(req(ref.samples[0].path,false));
  assert.equal(denied.status,401);
  assert.deepEqual(await denied.json(),ref.denied);
  assert.equal(await route(req('/api/v1/session',true,'POST')),null);
});

test('ordinal and encoded stable card lookup return exact equivalent bodies',async()=>{
  for(const sample of ref.samples) for(const path of [sample.path,sample.stable]) {
    const r=await route(req(path)); assert.equal(r.status,200);
    assert.equal(r.headers.get('cache-control'),'private, no-store');
    assert.deepEqual(await r.json(),sample.card);
  }
  assert.equal(await (await route(req(ref.samples[0].path,true,'HEAD'))).text(),'');
});

test('pinned revision and not-found errors are exact; adjacency not implemented',async()=>{
  for(const sample of ref.errors) {
    const r=await route(req(sample.path)); assert.equal(r.status,sample.status);
    assert.deepEqual(await r.json(),sample.body);
  }
  assert.equal(await route(req('/api/v1/nodes/old/edges')),null);
});
