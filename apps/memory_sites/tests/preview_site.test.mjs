import test from 'node:test';
import assert from 'node:assert/strict';
import preview from '../preview_site.mjs';

const url='https://preview.example.test';
const request=(path,init)=>new Request(url+path,init);

test('renders synthetic-only summary without touching D1 and never exposes MCP', async()=>{
  const env={get DB(){throw Error('unexpected DB')}};
  const page=await preview.fetch(request('/'),env);
  assert.equal(page.status,200);
  assert.equal(page.headers.get('cache-control'),'no-store');
  assert.match(await page.text(),/Реальная модель не вызывается/);
  assert.equal((await preview.fetch(request('/mcp',{method:'POST'}),env)).status,404);
  assert.equal((await preview.fetch(request('/preview/run'),env)).status,405);
});
test('only accepts empty same-origin POST and sanitizes backend exceptions', async()=>{
  const env={get DB(){throw Error('test private backend detail')}};
  const cross=await preview.fetch(request('/preview/run',{method:'POST',headers:{origin:'https://external.example.test'}}),env);
  assert.equal(cross.status,403);
  const input=await preview.fetch(request('/preview/run',{method:'POST',headers:{origin:url},body:new URLSearchParams({text:'ignored'})}),env);
  assert.equal(input.status,400);
  const safe=await preview.fetch(request('/preview/run',{method:'POST',headers:{origin:url,accept:'text/html'},body:new URLSearchParams()}),env);
  assert.equal(safe.status,503);
  assert.doesNotMatch(await safe.text(),/private backend detail/);
});

test('page offers fixed test terms; search rejects missing sign-in and arbitrary user text', async()=>{
  const env={get DB(){throw Error('unexpected DB')}};
  const page=await preview.fetch(request('/'),env);
  assert.match(await page.text(),/Поиск по вымышленному графу/);
  const fixed=await preview.fetch(request('/preview/search?q='+encodeURIComponent('стойку')),env);
  assert.equal(fixed.status,401);
  const rejected=await preview.fetch(request('/preview/search?q=private-input',{
    headers:{'oai-authenticated-user-id':'fixture'},
  }),env);
  assert.equal(rejected.status,400);
  assert.doesNotMatch(await rejected.text(),/private-input/);
});
