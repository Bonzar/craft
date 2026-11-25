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

function base(request){
 const result=spawnSync(python,[driver],{input:JSON.stringify(request)+'\n',encoding:'utf8',timeout:5000,
   env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 assert.equal(result.status,0,result.stderr);
 return JSON.parse(result.stdout);
}
function wire(variant){
 return async (url,o)=>{
  assert.equal(o.method,'POST');
  assert.equal(o.redirect,'manual');
  const prot=new URL(url);
  assert.equal(prot.origin,'https://one.two.apigw.yandexcloud.net');
  assert.match(prot.pathname,/^\/memory-mcp-(test|prod)-(tools|effects)$/);
  assert.equal(o.headers.cookie,undefined);
  const path=prot.pathname,lane=path.split('-')[2],kind=path.endsWith('-effects')?'effects':'read';
  const key=kind==='read'?laneToken:'e'.repeat(64);
  assert.equal(o.headers['x-memory-mcp-'+lane+'-'+kind+'-auth'],'Bearer '+key);
  const request={variant,path,key,body:JSON.parse(o.body)};
  const payload=base(request);
  if(request.body.operation==='catalog'&&payload.status===200&&variant!=='locked-down'){
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
 const includeEffects=['full','locked-down'].includes(variant);
 const pre='HOSTED_MEMORY_'+lane.toUpperCase();
 const origin='https://'+lane+'.chatgpt.site';
 const handler=attachReleaseMcp({legacyFetch:()=>{throw Error('there must be no legacy call');},
  verifyOwner:req=>req.headers.get('x-offline-contract-owner')===noDataOwner,
  profile:{lane,siteOrigin:origin,readUrl:'https://one.two.apigw.yandexcloud.net/memory-mcp-'+lane+'-tools',
   includeEffects,...(includeEffects?{effectsUrl:'https://one.two.apigw.yandexcloud.net/memory-mcp-'+lane+'-effects'}:{})},fetcher:wire(variant)});
 const env={[pre+'_DYNAMIC_CATALOG_ENABLED']:'true',[pre+'_READ_TOKEN']:laneToken,
  ...(includeEffects?{[pre+'_EFFECTS_ENABLED']:'true',[pre+'_EFFECTS_TOKEN']:'e'.repeat(64)}:{})};
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

for(const lane of ['test','prod'])test(lane+' closed Sites sees the full canonical Base registry without worker changes',async()=>{
 const full=client(lane,'full'),limited=client(lane,'locked-down');
 const tools=(await full('tools/list',{})).result.tools;
 assert.equal(tools.length,18);
 assert.equal(new Set(tools.map(x=>x.name)).size,18);
 const query=tools.find(x=>x.name==='memory_query');
 assert.equal(query.annotations.readOnlyHint,false);
 assert.equal(query.inputSchema.properties.query.type,'string');
 assert.equal((await limited('tools/list',{})).result.tools.length,5);
 assert.equal((await limited('tools/call',{name:'memory_query',arguments:{query:'Фильм?'}})).error.code,-32602);
 const r=await full('tools/call',{name:'memory_source',arguments:{record_id:'fictional-choice',section:'text'}});
 assert.equal(r.result.structuredContent.content,'Синий маяк в 19:40.');
 const generated=await full('tools/call',{name:'memory_query',arguments:{query:'Несвязанный вопрос о космосе?'}});
 assert.equal(generated.result.structuredContent.answer_accepted,false);
 assert.equal((await full('tools/call',{name:'memory_query',arguments:{query:13}})).result.isError,true);
 const request_id='842983c4-2df7-4fac-be9f-125a03aa42aa';
 const call=(part,key,operation,arguments_,expected)=>base({
  variant:'full',path:'/memory-mcp-'+lane+'-'+part,key,body:{request_id,operation,arguments:arguments_,expected}
 });
 const cat=call('tools',laneToken,'catalog',{}).body;
 const fingerprint=({release,generation,catalog_sha256})=>({release,generation,catalog_sha256});
 assert.equal(call('effects',laneToken,'catalog',{}).status,401);
 assert.equal(call('tools','e'.repeat(64),'catalog',{}).status,401);
 assert.equal(call('tools',laneToken,'memory_query',{query:'Фильм?'},fingerprint(cat)).status,400);
 assert.equal(call('effects','e'.repeat(64),'memory_source',{record_id:'fictional-choice'},fingerprint(cat)).status,400);
 assert.equal(call('tools',laneToken,'memory_source',{record_id:'fictional-choice'}).status,400);
 assert.equal(call('tools',laneToken,'memory_source',{record_id:'fictional-choice'},
  {...fingerprint(cat),catalog_sha256:'0'.repeat(64)}).status,409);
});
