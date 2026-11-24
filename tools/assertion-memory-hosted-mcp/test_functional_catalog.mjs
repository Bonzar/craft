import {strict as assert} from 'node:assert';
import {readFileSync} from 'node:fs';
import {makeHostedRelay} from './worker.mjs';
const src=JSON.parse(readFileSync(new URL('./post-search-source-contract.review.json',import.meta.url)));
const policy={};
for(const [name,cap] of Object.entries({memory_status:'baseline_read',memory_provenance:'baseline_read',
 memory_search:'baseline_read',memory_capture_message:'intake_process',memory_process:'intake_process',memory_query:'generate'}))
 policy[name]={classification:cap,capability_projections:{[cap]:src.tools[name]}};
const catalog={catalog_sha256:'a'.repeat(64),catalog:{tools:policy}};
let calls=0;
for(const lane of ['test','prod']){
 const prefix='HOSTED_MEMORY_'+lane.toUpperCase(),origin='https://'+lane+'.chatgpt.site';
 const host='https://one.two.apigw.yandexcloud.net';
 const profile={lane,site_origin:origin,gateway_url:host+'/memory-mcp-'+lane+'-tools',
    effect_gateway_url:host+'/memory-mcp-'+lane+'-effects',base_release:'assertion-memory-v0.1.1',
    catalog_sha256:catalog.catalog_sha256,read_catalog_sha256:'b'.repeat(64),
    enabled_capabilities:['baseline_read','intake_process','generate'],query_timeout_ms:65000,
    rollout_flag:prefix+'_READ_ENABLED',credential_var:prefix+'_READ_TOKEN'};
 const relay=makeHostedRelay({profile,catalog,verifyOwner:()=>false,fetcher:()=>{calls++;throw Error('no network');}});
 const environment={[prefix+'_READ_ENABLED']:'true',[prefix+'_READ_TOKEN']:'c'.repeat(64),
   [prefix+'_EFFECTS_ENABLED']:'true',[prefix+'_EFFECTS_TOKEN']:'d'.repeat(64)};
 const req=new Request(origin+'/mcp',{method:'POST'});
 const partial=relay.catalog([],environment);
 assert.equal(partial.some(x=>x.name==='memory_query'),false);
 assert.equal(partial.find(x=>x.name==='memory_search').annotations.readOnlyHint,true);
 const on={...environment,[prefix+'_GENERATE_ENABLED']:'true'};
 const full=relay.catalog([],on);
 assert.equal(full.find(x=>x.name==='memory_query').annotations.readOnlyHint,false);
 assert.equal((await relay.call('memory_query',{},req,on)).isError,true);
 assert.equal((await relay.call('memory_search',{},req,on)).isError,true);
}
assert.equal(calls,0);
console.log('Search/read and generated answer gates are symmetric; no downstream calls made.');
