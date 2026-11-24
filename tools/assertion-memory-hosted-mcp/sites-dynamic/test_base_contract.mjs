import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {attachReleaseMcp} from './worker.mjs';

const noDataOwner='offline-contract-owner';
const laneToken='a'.repeat(64);
const file=new URL('./worker.mjs',import.meta.url);
const identity=()=>createHash('sha256').update(readFileSync(file)).digest('hex');
const driver=process.env.BASE_PRIVATE_HTTP_DRIVER??fileURLToPath(new URL('./test_base_contract_driver.py',import.meta.url));
const python=process.env.BASE_PRIVATE_HTTP_PYTHON??'python';

function wire(variant){
 return async (url,o)=>{
  assert.equal(o.method,'POST');
  assert.equal(o.redirect,'manual');
  const prot=new URL(url);
  assert.equal(prot.origin,'https://one.two.apigw.yandexcloud.net');
  assert.match(prot.pathname,/^\/memory-mcp-(test|prod)-tools$/);
  assert.equal(o.headers.cookie,undefined);
  assert.equal(o.headers['x-memory-mcp-'+prot.pathname.split('-')[2]+'-read-auth'],'Bearer '+laneToken);
  const request={variant,path:prot.pathname,body:JSON.parse(o.body)};
  const result=spawnSync(python,[driver],{input:JSON.stringify(request)+'\n',encoding:'utf8',timeout:5000,
    env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  assert.equal(result.status,0,result.stderr);
  const payload=JSON.parse(result.stdout);
  if(request.body.operation==='catalog'&&payload.status===200){
   for(const tool of payload.body.tools){
    const actual=payload.declared[tool.name];
    assert.deepEqual(tool.inputSchema,{...actual.inputSchema,additionalProperties:false});
    assert.equal(tool.description,actual.description);
   }
  }
  return Response.json(payload.body,{status:payload.status});
 };
}
function client(lane,variant){
 const pre='HOSTED_MEMORY_'+lane.toUpperCase();
 const origin='https://'+lane+'.chatgpt.site';
 const handler=attachReleaseMcp({legacyFetch:()=>{throw Error('there must be no legacy call');},
  verifyOwner:req=>req.headers.get('x-offline-contract-owner')===noDataOwner,
  profile:{lane,siteOrigin:origin,readUrl:'https://one.two.apigw.yandexcloud.net/memory-mcp-'+lane+'-tools',
   includeEffects:false},fetcher:wire(variant)});
 const env={[pre+'_DYNAMIC_CATALOG_ENABLED']:'true',[pre+'_READ_TOKEN']:laneToken};
 return async (method,params)=> {
  const request=new Request(origin+'/mcp',{method:'POST',headers:{'content-type':'application/json',
   'x-offline-contract-owner':noDataOwner},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
  return (await handler(request,env)).json();
 };
}

for(const lane of ['test','prod'])test(lane+' unchanged Sites reflects a new required argument from Base only',async()=>{
 const sourceBefore=identity();
 const old=client(lane,'original'),latest=client(lane,'expanded');
 const prior=(await old('tools/list',{})).result.tools;
 const current=(await latest('tools/list',{})).result.tools;
 assert.equal(prior.length,1);assert.equal(current.length,1);
 assert.equal(prior[0].name,current[0].name);
 assert.equal(prior[0].inputSchema.properties.audience,undefined);
 const {type,enum:choices,default:fallback}=current[0].inputSchema.properties.audience;
 assert.deepEqual({type,choices,fallback},{type:'integer',choices:[2,3],fallback:2});
 assert.ok(current[0].inputSchema.required.includes('audience'));
 for(const args of [{},{audience:4},{audience:'3'}]){
   const bad=await latest('tools/call',{name:current[0].name,arguments:args});
   assert.equal(bad.result.isError,true);
 }
 const good=await latest('tools/call',{name:current[0].name,arguments:{audience:3}});
 assert.equal(good.result.structuredContent.audience,3);
 assert.deepEqual(JSON.parse(good.result.content[0].text),good.result.structuredContent);
 assert.equal(identity(),sourceBefore);
});
