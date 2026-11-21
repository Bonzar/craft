import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';
import {admitRawCapture} from '../raw_capture.mjs';
import {captureJob, claimJob} from '../queue.mjs';
import {createCanonicalSourceRunner} from '../canonical_source_runner.mjs';

const now = 1_800_000_000_000;
const capture = {session_id:'fixture-greenhouse',message_id:'message-1',actor_id:'user',role:'user',
  text:'Датчик нужен перед открытием. Ключ у Саши.',position:0,source:'fixture://greenhouse/first',scope:'synthetic'};
const first = {text:'Проверь датчик перед открытием.',question:null,support_fragments:[0]};
const second = {text:'Ключ у Саши.',question:null,support_fragments:[1]};
const ordinary = {verdict:'accept',text:'Verified'};
const independent = {...ordinary,rejected_drafts:[],missing_meaning:null};
const envelope = value => Response.json({status:'completed',output:[{type:'message',role:'assistant',
  status:'completed',content:[{type:'output_text',text:JSON.stringify(value)}]}]});

async function fixture(t,{admit=true}={}) {
  const raw = new DatabaseSync(':memory:');
  for (const name of ['0002_queue','0003_publications','0004_raw_capture']) {
    raw.exec(readFileSync(new URL('../drizzle/'+name+'.sql',import.meta.url),'utf8'));
  }
  t.after(()=>raw.close());
  const db = {
    prepare(sql) { return {sql,args:[],bind(...args) { return {...this,args}; }}; },
    async batch(statements) {
      raw.exec('BEGIN IMMEDIATE');
      try {
        const out = statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');
        return out;
      } catch (error) { raw.exec('ROLLBACK'); throw error; }
    },
  };
  if (admit) await admitRawCapture(db,capture,{now});
  const read = () => raw.prepare('SELECT state,result FROM memory_jobs ORDER BY created_at LIMIT 1').get();
  const publications = () => raw.prepare('SELECT payload,verifier FROM memory_job_publications').all();
  return {db,read,publications};
}

function adapter(f, {drafts=[first], verdict=ordinary, beforeReview, authorizeSource, clock=()=>now+1}={}) {
  const calls = [];
  const run = createCanonicalSourceRunner({
    clock,leaseMs:500,authorizeSource:authorizeSource ?? (raw=>JSON.parse(raw).scope==='synthetic'),
    model:{apiKey:'fake-key',constructModel:'fake-writer',reviewModel:'fake-reviewer',
      async fetchImpl(_url, init) {
        const body = JSON.parse(init.body);
        calls.push(body);
        if (body.model === 'fake-writer') return envelope({thoughts:drafts,needs_detail:null});
        assert.equal(f.publications().length,0);
        await beforeReview?.();
        return envelope(verdict);
      }},
  });
  return {calls,run:job=>run(f.db,job)};
}

test('actual admitted source publishes only after ordinary or independent canonical Verify',async t=>{
  for (const drafts of [[first],[first,second]]) {
    const f = await fixture(t);
    const batch = drafts.length > 1;
    const {run,calls} = adapter(f,{drafts,verdict:batch ? independent : ordinary});
    const job = await claimJob(f.db,{now:now+1,leaseMs:500});
    assert.equal((await run(job)).status,'published');
    assert.equal(f.read().state,'done');
    assert.equal(f.publications()[0].verifier,batch ? 'source-batch-review' : 'source-initial-review');
    const payload = JSON.parse(f.publications()[0].payload);
    assert.deepEqual(payload.drafts.map(x=>x.text),drafts.map(x=>x.text));
    assert.deepEqual(payload.drafts[0].support_ids,[JSON.stringify([job.id,0,[...'Датчик нужен перед открытием. '].length])]);
    assert.deepEqual(calls.map(x=>x.model),['fake-writer','fake-reviewer']);
    assert.equal('rejected_drafts' in calls[1].text.format.schema.properties,batch);
    assert.equal(calls[1].input[0].content.includes(first.text),true);
  }
});

test('reviewed zero-draft receipt completes no-op; reject never inserts anything',async t=>{
  for (const verdict of [ordinary,{verdict:'reject',text:'A material meaning was omitted.'}]) {
    const f = await fixture(t);
    const {run,calls} = adapter(f,{drafts:[],verdict});
    const job = await claimJob(f.db,{now:now+1,leaseMs:500});
    const accepted = verdict.verdict==='accept';
    assert.equal((await run(job)).status,accepted ? 'reviewed-empty' : 'retrying');
    assert.equal(f.read().state,accepted ? 'done' : 'ready');
    assert.equal(calls.length,2);
    assert.equal('rejected_drafts' in calls[1].text.format.schema.properties,false);
    assert.equal(f.publications().length,0);
    if (accepted) {
      assert.equal(JSON.parse(f.read().result).kind,'reviewed-empty-source');
      assert.equal(f.read().result.includes(capture.text),false);
    }
  }
});

test('batch with one rejected draft is not partially published',async t=>{
  const f = await fixture(t);
  const verdict = {verdict:'reject',text:'One draft failed.',rejected_drafts:[{draft:1,text:'Unclear.'}],missing_meaning:null};
  const {run,calls} = adapter(f,{drafts:[first,second],verdict});
  const job = await claimJob(f.db,{now:now+1,leaseMs:500});
  assert.deepEqual(await run(job),{status:'retrying'});
  assert.equal(calls.length,2);
  assert.equal(f.publications().length,0);
});

test('nonadmitted, unauthorized, or possible contextual source never reaches provider',async t=>{
  for (const cause of ['not_admitted','not_authorized','context']) {
    const f = await fixture(t,{admit:cause!=='not_admitted'});
    if (cause==='not_admitted') await captureJob(f.db,{id:'forged',kind:'source-thoughts',body:'{}',now});
    if (cause==='context') await admitRawCapture(f.db,{...capture,message_id:'message-2',position:1},{now:now+1});
    const job = await claimJob(f.db,{now:now+1,leaseMs:500});
    const {run,calls} = adapter(f,{authorizeSource:()=>cause!=='not_authorized'});
    assert.deepEqual(await run(job),{status:'failed'});
    assert.equal(calls.length,0);
    assert.equal(f.publications().length,0);
  }
});

test('context appearing during model review prevents publication or zero-draft completion',async t=>{
  for (const drafts of [[],[first]]) {
    const f = await fixture(t);
    const job = await claimJob(f.db,{now:now+1,leaseMs:500});
    const {run,calls} = adapter(f,{drafts,beforeReview:async()=>{
      await admitRawCapture(f.db,{...capture,message_id:'message-2',position:1},{now:now+2});
    }});
    assert.deepEqual(await run(job),{status:'retrying'});
    assert.equal(calls.length,2);
    assert.equal(f.read().state,'ready');
    assert.equal(f.publications().length,0);
  }
});

test('an expired lease cannot finish a reviewed-empty source or publish a draft',async t=>{
  for (const drafts of [[],[first]]) {
    const f = await fixture(t);
    let clock = now+1;
    const job = await claimJob(f.db,{now:clock,leaseMs:500});
    const {run} = adapter(f,{drafts,clock:()=>clock,beforeReview:async()=>{
      clock += 501;
      assert.ok(await claimJob(f.db,{now:clock,leaseMs:500}));
    }});
    assert.deepEqual(await run(job),{status:'lost'});
    assert.equal(f.read().state,'running');
    assert.equal(f.read().result,null);
    assert.equal(f.publications().length,0);
  }
});
