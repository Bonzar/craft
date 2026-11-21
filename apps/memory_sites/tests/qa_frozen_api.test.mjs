import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { frozenQaApi } from '../qa_frozen_loader.mjs';

test('one original synthetic revision shares gated graph, binaries and cards', async () => {
  const cwd=fileURLToPath(new URL('../../../',import.meta.url));
  const frozen=JSON.parse(execFileSync(process.env.PYTHON||'python',
    ['-m','apps.memory_sites.tests.qa_frozen_oracle'],{cwd,encoding:'utf8'}));
  for (const encodings of Object.values(frozen.artifacts))
    for (const key of Object.keys(encodings)) encodings[key]=Buffer.from(encodings[key],'base64');
  const route=frozenQaApi(frozen, r=>r.headers.get('x-fixture')==='owner'?'csrf-fixture':null);
  const call=(path,owner=true)=>route(new Request('https://example.invalid'+path,{
    headers:{'Accept-Encoding':'identity',...(owner?{'x-fixture':'owner'}:{})}}));
  assert.equal((await call('/api/v1/graph',false)).status,401);
  assert.equal((await call('/api/v1/graph?history=1',false)).status,401);
  assert.equal((await call('/api/v1/graph?history=1')).status,501);
  assert.equal((await call('/api/v1/graph?mode=known_now')).status,501);
  const caps = await (await call('/api/v1/capabilities')).json();
  assert.equal(caps.capabilities.live,false);
  assert.equal(caps.capabilities.history,false);
  const m=await (await call('/api/v1/graph')).json();
  assert.deepEqual(m,frozen.manifest);
  for (const kind of ['nodes','edges']) {
    const bytes=Buffer.from(await (await call(m[kind].url)).arrayBuffer());
    assert.deepEqual(bytes,frozen.artifacts[kind+'.bin'].identity);
  }
  for (const {kind,card} of frozen.cards) {
    const path='/api/v1/'+kind+'/'+encodeURIComponent(card.id)+'?revision='+m.revision;
    assert.equal((await call(path,false)).status,401);
    assert.deepEqual(await (await call(path)).json(),card);
  }
  assert.equal((await call('/api/v1/graph/expired/nodes/0')).status,409);
});
