import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createQaRevisionApi } from '../qa_revision_api.mjs';

const cwd = fileURLToPath(new URL('../../../', import.meta.url));
const golden = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-m','apps.memory_sites.tests.qa_transport_oracle'], {cwd,encoding:'utf8'}));
const origins = 'https://site.invalid';
const route = createQaRevisionApi({
  authorize: req => req.headers.has('x-synthetic-authorized'),
  loadCapabilities: () => golden.capabilities,
  loadCurrent: selector => selector.history ? null : golden.manifest,
  loadRevision: revision => revision === golden.manifest.revision ? {
    artifact: (name, gzip) => Buffer.from(golden.artifacts[name][gzip ? 'gzip' : 'identity'].base64,'base64')
  } : null
});
function request(path, options = {}, authorize = true) {
  return new Request(origins+path,{...options,headers:{...options.headers,...authorize && {'x-synthetic-authorized':'1'}}});
}

test('matches upstream frozen manifest, capabilities, auth denial and stale revision', async () => {
  const graph = await route(request('/api/v1/graph'));
  assert.equal(graph.status,200);
  assert.equal(graph.headers.get('etag'),golden.etag);
  assert.deepEqual(await graph.json(),golden.manifest);
  assert.deepEqual(await (await route(request('/api/v1/capabilities'))).json(),golden.capabilities);
  const unauthorized = await route(request(golden.manifest.nodes.url,{},false));
  assert.equal(unauthorized.status,401);
  assert.deepEqual(await unauthorized.json(),golden.unauthorized);
  const stale = await route(request('/api/v1/graph/invalid/nodes.bin'));
  assert.equal(stale.status,409);
  assert.deepEqual(await stale.json(),golden.expired);
});

test('serves original binary bytes and cache/encoding headers',async () => {
  for (const kind of ['nodes','edges']) for (const [encoding,header] of [['gzip','br, gzip;q=0.5'],['identity','gzip;q=0']]) {
    const url = golden.manifest[kind].url;
    const upstream = golden.artifacts[kind+'.bin'][encoding];
    const response = await route(request(url,{headers:{'accept-encoding':header}}));
    assert.equal(response.status,200);
    for (const [key,value] of Object.entries(upstream.headers)) assert.equal(response.headers.get(key),value,key);
    assert.equal(Buffer.from(await response.arrayBuffer()).toString('base64'),upstream.base64);
    const head = await route(request(url,{method:'HEAD',headers:{'accept-encoding':header}}));
    assert.equal(head.headers.get('content-length'),upstream.headers['content-length']);
    assert.equal(await head.text(),'');
  }
});

test('keeps a narrow local-only surface; no fabricated historical graph', async () => {
  assert.equal(await route(request('/api/v1/bookmarks')),null);
  assert.equal((await route(request('/api/v1/graph?mode=nonexistent'))).status,400);
  assert.equal((await route(request('/api/v1/graph?mode=known_then'))).status,501);
  const wait = await route(request('/api/v1/graph?history=1'));
  assert.equal(wait.status,202);
  assert.equal(wait.headers.get('retry-after'),'1');
});
