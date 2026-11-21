import test from 'node:test';
import assert from 'node:assert/strict';
import {permanentPreview} from '../permanent_preview_route.mjs';
const base='https://example.test', path=base+'/preview/permanent';
const signed={'oai-authenticated-user-id':'fixture-owner'};

test('read/persist demo never admits unsigned, cross-origin or free-text requests', async()=>{
  const env={get DB(){throw Error('private failure marker')}};
  assert.equal(await permanentPreview(new Request(base+'/explore'),env),null);
  assert.equal((await permanentPreview(new Request(path),env)).status,401);
  assert.equal((await permanentPreview(new Request(path,{method:'POST',headers:signed}),env)).status,403);
  const withBody=new Request(path,{method:'POST',headers:{...signed,origin:base},body:new URLSearchParams({text:'not accepted'})});
  assert.equal((await permanentPreview(withBody,env)).status,400);
  const out=await permanentPreview(new Request(path,{headers:signed}),env);
  assert.equal(out.status,503);
  assert.doesNotMatch(await out.text(),/private failure marker/);
});
