import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {admitRawCapture} from '../raw_capture.mjs';
import {claimJob} from '../queue.mjs';
import {createLiveSourceProvider} from '../live_source_provider.mjs';
import {CANONICAL_MODEL_INSTRUCTIONS,prepareInitialSourcePrompts} from '../source_model_prompt.mjs';
import {ModelHttpsError} from '../model_https.mjs';

const runtime = {
  OPENAI_API_KEY:'synthetic-local-test-key',
  MEMORY_CONSTRUCT_MODEL:'fixture-model-construction',
  MEMORY_REVIEW_MODEL:'fixture-model-review',
};
const construct = {thoughts:[{text:'The fictional flower is red.',question:null,support_fragments:[0]}],needs_detail:null};
const accept = {verdict:'accept',text:'The fictional source supports it.'};
const reject = {verdict:'reject',text:'The output differs from its source.'};
function response(value) {
  return Response.json({status:'completed',output:[{type:'message',role:'assistant',status:'completed',
    content:[{type:'output_text',text:JSON.stringify(value)}]}]});
}
async function fixture(t) {
  const raw=new DatabaseSync(':memory:');
  for(const name of ['0002_queue','0003_publications','0004_raw_capture','0005_semantic_thoughts','0006_live_source_commit']) {
    const sql=readFileSync(new URL('../drizzle/'+name+'.sql',import.meta.url),'utf8');
    for(const statement of sql.split('--> statement-breakpoint')) raw.exec(statement);
  }
  t.after(()=>raw.close());
  const db={
    prepare(sql){return {sql,args:[],bind(...args){return {...this,args};}};},
    async batch(statements) {
      raw.exec('BEGIN IMMEDIATE');
      try {
        const result=statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');
        return result;
      } catch(err) {raw.exec('ROLLBACK');throw err;}
    },
  };
  await admitRawCapture(db,{session_id:'isolated-source',message_id:'one',actor_id:'user',role:'user',
    source:'fixture://provider',text:'The fictional flower is red.',position:0});
  const job=await claimJob(db,{leaseMs:60_000});
  return {db,raw,job,key:{id:job.id,leaseToken:job.lease_token}};
}

test('trusted runtime sends only two original schema-bound requests to fixed endpoint, then persists',async t=>{
  const f=await fixture(t);
  const wires=[];
  const expected=prepareInitialSourcePrompts({source:f.job.payload});
  t.mock.method(globalThis,'fetch',async(url, init)=>{
    assert.equal(url,'https://api.openai.com/v1/responses');
    assert.equal(init.redirect,'error');
    assert.equal(init.headers.authorization,'Bearer '+runtime.OPENAI_API_KEY);
    wires.push(JSON.parse(init.body));
    return response(wires.length===1?construct:accept);
  });
  const subjectChecks=[];
  const run=createLiveSourceProvider({...runtime,fetchImpl:()=>{throw Error('Override not supported')},
    endpoint:'https://do-not-send.invalid'},{authorizeSource:async subject=>{
      subjectChecks.push(subject);
      return subject.id===f.job.id&&subject.source===f.job.payload&&subject.sourceSha256===f.job.payload_sha256;
    }});
  const result=await run(f.db,f.key);
  assert.equal(result.status,'committed');
  assert.equal(wires.length,2);
  assert.deepEqual(wires.map(x=>x.model),[runtime.MEMORY_CONSTRUCT_MODEL,runtime.MEMORY_REVIEW_MODEL]);
  for(const [index,request] of [expected.construct,expected.review(JSON.stringify(construct))].entries()) {
    const actual=wires[index];
    assert.equal(actual.instructions,CANONICAL_MODEL_INSTRUCTIONS);
    assert.equal(actual.input[0].content,request.prompt);
    assert.equal(actual.text.format.strict,true);
    assert.deepEqual(actual.text.format.schema,request.schema);
    assert.equal(actual.store,false);
    assert.equal(actual.tool_choice,'none');
    assert.deepEqual(actual.tools,[]);
  }
  assert.equal(subjectChecks.length,3);
  assert.equal(f.raw.prepare('SELECT state FROM memory_jobs').get().state,'done');
  assert.equal(f.raw.prepare('SELECT content_hash FROM memory_thought_versions').get().content_hash,result.content_hash);
  assert.doesNotMatch(JSON.stringify(result),/synthetic-local-test-key|flower/);
});

test('authorization and missing/same-model configuration fail closed; no outbound requests',async t=>{
  const f=await fixture(t);
  const fetchMock=t.mock.method(globalThis,'fetch',()=>{throw Error('unexpected network')});
  for(const invalid of [{...runtime,OPENAI_API_KEY:undefined},{...runtime,MEMORY_REVIEW_MODEL:runtime.MEMORY_CONSTRUCT_MODEL}])
    assert.throws(()=>createLiveSourceProvider(invalid,{authorizeSource:async()=>true}),/configuration/);
  assert.throws(()=>createLiveSourceProvider(runtime),/configuration/);
  const run=createLiveSourceProvider(runtime,{authorizeSource:async()=>false});
  assert.deepEqual(await run(f.db,f.key),{status:'refused'});
  assert.equal(fetchMock.mock.callCount(),0);
  assert.equal(f.raw.prepare('SELECT count(*) n FROM memory_thought_versions').get().n,0);
});

test('provider failure or semantic rejection does not fall back or write a thought',async t=>{
  const f=await fixture(t);
  let count=0;
  const fetchMock=t.mock.method(globalThis,'fetch',async()=>{
    count++;
    if(count===1) return new Response('do not surface this provider error text',{status:429});
    return response(count===2?construct:reject);
  });
  const run=createLiveSourceProvider(runtime,{authorizeSource:async()=>true});
  await assert.rejects(run(f.db,f.key),error=>error instanceof ModelHttpsError&&error.code==='rate_limit'&&
    !error.message.includes('provider error text'));
  assert.equal(fetchMock.mock.callCount(),1);
  assert.deepEqual(await run(f.db,f.key),{status:'rejected'});
  assert.equal(fetchMock.mock.callCount(),3);
  assert.equal(f.raw.prepare('SELECT count(*) n FROM memory_thought_versions').get().n,0);
  assert.equal(f.raw.prepare('SELECT state FROM memory_jobs').get().state,'running');
});
