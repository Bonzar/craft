import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {captureJob, claimJob} from '../queue.mjs';
import {createVerifiedWorker} from '../verified_worker.mjs';

const epoch = 1_800_000_000_000;
const source = '{"synthetic":"Fixture has a blue box."}';
const fragments = [{id:'src:1',text:'Fixture has a blue box.'}];
const candidate = '{"thoughts":[{"text":"The box is blue.","question":null,"support_fragments":[0]}]}';
const accepted = '{"verdict":"accept","text":"Supported","rejected_drafts":[],"missing_meaning":null}';
const rejected = '{"verdict":"reject","text":"Incomplete","rejected_drafts":[],"missing_meaning":"missing meaning"}';

async function fixture(t, kind='source-thoughts') {
  const raw=new DatabaseSync(':memory:');
  for(const file of ['0002_queue','0003_publications']) {
    raw.exec(readFileSync(new URL('../drizzle/'+file+'.sql',import.meta.url),'utf8'));
  }
  t.after(()=>raw.close());
  const db={
    prepare(sql) {return {sql,args:[],bind(...args){return {...this,args}}};},
    async batch(statements) {
      raw.exec('BEGIN IMMEDIATE');
      try {
        const result=statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');
        return result;
      } catch (err) {raw.exec('ROLLBACK');throw err;}
    },
  };
  await captureJob(db,{id:'job',kind,body:source,now:epoch});
  const job=await claimJob(db,{now:epoch,leaseMs:500});
  const count=()=>raw.prepare('SELECT count(*) n FROM memory_job_publications').get().n;
  const state=()=>raw.prepare('SELECT state FROM memory_jobs').get().state;
  return {db,raw,job,count,state};
}
const adapters = {
  prepare:async()=>({eligible:true,fragments}),
  construct:async()=>candidate,
  review:async()=>accepted,
  clock:()=>epoch+1,
  leaseMs:500,
};

test('prepares offered evidence, validates construction, uses independent review then publishes',async t=>{
  const {db,raw,job,count,state}=await fixture(t);
  const sequence=[];
  const run=createVerifiedWorker({...adapters,
    prepare:async input=>{sequence.push('prepare');assert.equal(input.source,source);return {eligible:true,fragments};},
    construct:async input=>{sequence.push('construct');assert.deepEqual(input.fragments,fragments);return candidate;},
    review:async input=>{
      sequence.push('review');
      assert.deepEqual(JSON.parse(input.payload).drafts[0].support_ids,['src:1']);
      assert.equal(count(),0);
      assert.equal(state(),'running');
      return accepted;
    },
  });
  const result=await run(db,job);
  assert.equal(result.status,'published');
  assert.deepEqual(sequence,['prepare','construct','review']);
  assert.equal(count(),1);
  assert.equal(state(),'done');
  assert.equal(raw.prepare('SELECT verifier FROM memory_job_publications').get().verifier,'source-batch-review');
});

test('out-of-catalog supports never reach review',async t=>{
  const {db,job,count,state}=await fixture(t);
  let reviewed=false;
  const run=createVerifiedWorker({...adapters,
    construct:async()=>candidate.replace('[0]','[1]'),
    review:async()=>{reviewed=true;return accepted;},
  });
  assert.deepEqual(await run(db,job),{status:'retrying'});
  assert.equal(reviewed,false);
  assert.equal(state(),'ready');
  assert.equal(count(),0);
});

test('valid rejection and malformed review cannot publish',async t=>{
  for (const reply of [rejected,'{"verdict":"accept"}']) {
    const {db,job,count}=await fixture(t);
    const run=createVerifiedWorker({...adapters,review:async()=>reply});
    assert.deepEqual(await run(db,job),{status:'retrying'});
    assert.equal(count(),0);
  }
});

test('losing claim while reviewing leaves existing/new lease state intact',async t=>{
  const {db,job,count,state}=await fixture(t);
  let now=epoch+1;
  const run=createVerifiedWorker({...adapters,clock:()=>now,
    review:async()=>{now=epoch+501;assert.ok(await claimJob(db,{now,leaseMs:500}));return accepted;},
  });
  assert.deepEqual(await run(db,job),{status:'lost'});
  assert.equal(count(),0);
  assert.equal(state(),'running');
});

test('unknown kinds and ineligible source never reach generation',async t=>{
  const a=await fixture(t,'generic');
  const b=await fixture(t);
  let called=false;
  const run=createVerifiedWorker({...adapters,prepare:async()=>({eligible:false}),
    construct:async()=>{called=true;return candidate;},
  });
  assert.deepEqual(await run(a.db,a.job),{status:'unsupported'});
  assert.deepEqual(await run(b.db,b.job),{status:'failed'});
  assert.equal(called,false);
  assert.equal(a.state(),'running');
  assert.equal(a.count()+b.count(),0);
});
