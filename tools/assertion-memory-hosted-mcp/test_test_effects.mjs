import {strict as assert} from 'node:assert';
import {makeHostedRelay} from './worker.mjs';
import {readFileSync} from 'node:fs';
const legacy=JSON.parse(readFileSync(new URL('./catalog.v011.review.json',import.meta.url)));
const catalog=structuredClone(legacy);
catalog.catalog_sha256='d'.repeat(64);
for(const name of ['memory_capture_message','memory_process']){
 const p=catalog.catalog.tools[name];
 p.capability_projections.intake_process={...p.canonicalInputSchema,additionalProperties:false};
}
const origin='https://example.chatgpt.site',gw='https://test.test.apigw.yandexcloud.net';
const p={lane:'test',site_origin:origin,base_release:'assertion-memory-v0.1.1',gateway_url:gw+'/memory-mcp-test-tools',
 effect_gateway_url:gw+'/memory-mcp-test-effects',enabled_capabilities:['baseline_read','intake_process'],
 catalog_sha256:catalog.catalog_sha256,read_catalog_sha256:legacy.catalog_sha256,rollout_flag:'HOSTED_MEMORY_TEST_READ_ENABLED',credential_var:'HOSTED_MEMORY_TEST_READ_TOKEN'};
const e={HOSTED_MEMORY_TEST_READ_ENABLED:'true',HOSTED_MEMORY_TEST_READ_TOKEN:'a'.repeat(64)};
let calls=0;
const relay=makeHostedRelay({profile:p,catalog,verifyOwner:(_,env)=>env.proof===true,
 fetcher:()=>{calls++;throw Error('no requests allowed in denial tests');}});
const req=new Request(origin+'/mcp',{method:'POST'});
assert.deepEqual(relay.catalog([],e).map(x=>x.name).sort(),['memory_provenance','memory_status']);
const on={...e,HOSTED_MEMORY_TEST_EFFECTS_ENABLED:'true',HOSTED_MEMORY_TEST_EFFECTS_TOKEN:'b'.repeat(64)};
const effects=relay.catalog([],on).filter(x=>!x.annotations.readOnlyHint);
assert.deepEqual(effects.map(x=>x.name).sort(),['memory_capture_message','memory_process']);
assert.equal((await relay.call('memory_process',{},req,on)).isError,true);
assert.equal((await relay.call('memory_process',{},req,{...on,proof:true,HOSTED_MEMORY_TEST_EFFECTS_TOKEN:e.HOSTED_MEMORY_TEST_READ_TOKEN})).isError,true);
assert.equal((await relay.call('memory_query',{},req,{...on,proof:true})).isError,true);
assert.equal(calls,0);
assert.throws(()=>makeHostedRelay({profile:{...p,lane:'prod'},catalog,verifyOwner:()=>true}));
console.log('Owner gate, effect flag, strict capability set, and split secret tests passed');
