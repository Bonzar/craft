import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createQaSavedMaterialApi} from '../qa_saved_material.mjs';
const here=fileURLToPath(new URL('../../../',import.meta.url));
const script=fileURLToPath(new URL('./qa_saved_material_oracle.py',import.meta.url));
const fixture=JSON.parse(execFileSync('python',[script],{cwd:here,env:{...process.env,PYTHONPATH:here},encoding:'utf8'}));
const ownerSession=request=>request.headers.get('x-test-owner')?'test-csrf':null;
const api=createQaSavedMaterialApi({...fixture,ownerSession});
test('saved overview, pagination and unavailable status match the original Python route',async()=>{
  for(const [path,status,body] of fixture.expected){
    const response=await api(new Request('https://qa.local'+path,{headers:{'x-test-owner':'1'}}));
    assert.equal(response.status,status,path);
    if(status===409)assert.equal((await response.json()).error.code,body.error.code,path);
    else assert.deepEqual(await response.json(),body,path);
  }
});
test('saved material is owner-only, read-only, and startup rejects mismatched saved versions',async()=>{
  const path=fixture.expected[0][0];
  const req=(method,owner)=>new Request('https://qa.local'+path,{method,headers:owner?{'x-test-owner':'1'}:{}});
  assert.equal((await api(req('GET',false))).status,401);
  assert.equal((await api(req('POST',true))).status,405);
  assert.equal(await (await api(req('HEAD',true))).text(),'');
  assert.throws(()=>createQaSavedMaterialApi({...fixture,overviews:[{...fixture.overviews[0],revision:'foreign'}],ownerSession}));
});
