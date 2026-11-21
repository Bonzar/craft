import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {captureJob, CaptureConflict, QueueInputError, claimJob, saveCheckpoint,
  renewJobLease, finishJob, failJob, sweepExpired, processJobs, CAPTURE_BYTES} from '../queue.mjs';
const schema=readFileSync(new URL('../drizzle/0002_queue.sql',import.meta.url), 'utf8');
const clock=1_800_000_000_000;
const capture=(db,id,more={})=>captureJob(db,{id,kind:'test',body:'{"synthetic":true}',now:clock,...more});
const key=(job,now)=>({id:job.id,leaseToken:job.lease_token,now});
function fixture(t) {
  const raw=new DatabaseSync(':memory:');
  raw.exec(schema);
  t.after(()=>raw.close());
  return {raw,db:{
    prepare(sql) {return {sql,args:[],bind(...args){return {...this,args}}};},
    async batch(statements) {
      raw.exec('BEGIN IMMEDIATE');
      try {
        const out=statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');
        return out;
      } catch (err) {raw.exec('ROLLBACK');throw err;}
    }
  }};
}
test('idempotent capture across competing requests and digest conflicts',async(t)=>{
  const {db,raw}=fixture(t);
  const outcomes=await Promise.all([capture(db,'a'),capture(db,'a')]);
  assert.equal(outcomes.filter(x=>x.created).length,1);
  assert.equal(outcomes.filter(x=>!x.created).length,1);
  assert.equal(Object.hasOwn(outcomes[0].job,'payload'),false);
  assert.equal(Object.hasOwn(outcomes[0].job,'payload_sha256'),false);
  await assert.rejects(capture(db,'a',{body:'{"synthetic":false}'}),CaptureConflict);
  await assert.rejects(capture(db,'a',{kind:'different'}),CaptureConflict);
  await assert.rejects(capture(db,'a',{maxAttempts:4}),CaptureConflict);
  await assert.rejects(capture(db,'b',{body:'"'+ 'Е'.repeat(CAPTURE_BYTES/2) +'"'}),QueueInputError);
  assert.equal(raw.prepare('SELECT COUNT(*) n FROM memory_jobs').get().n,1);
});
test('only one concurrent claim, checkpoint survives expiration, and old token cannot publish',async(t)=>{
  const {db}=fixture(t);
  await capture(db,'claim');
  const outcomes=await Promise.all([claimJob(db,{now:clock,leaseMs:10}),claimJob(db,{now:clock,leaseMs:10})]);
  const first=outcomes.find(Boolean);
  assert.equal(outcomes.filter(Boolean).length,1);
  assert.equal(first.attempts,1);
  assert.equal((await saveCheckpoint(db,{...key(first,clock+4),checkpoint:'{"substep":1}'})).state,'running');
  assert.equal(await claimJob(db,{now:clock+9,leaseMs:10}),null);
  assert.equal(await finishJob(db,key(first,clock+10)),null);
  const second=await claimJob(db,{now:clock+10,leaseMs:10});
  assert.equal(second.attempts,2);
  assert.equal(second.checkpoint,'{"substep":1}');
  assert.notEqual(second.lease_token,first.lease_token);
  assert.equal(await saveCheckpoint(db,{...key(first,clock+11),checkpoint:'{"substep":99}'}),null);
  assert.equal(await finishJob(db,key(first,clock+11)),null);
  assert.equal(await failJob(db,key(first,clock+11)),null);
  assert.equal((await renewJobLease(db,{...key(second,clock+15),leaseMs:20})).lease_expires_at,clock+35);
  assert.equal((await finishJob(db,{...key(second,clock+24),result:'{"synthetic":true}'})).state,'done');
  assert.equal(await claimJob(db,{now:clock+25,leaseMs:10}),null);
});
test('retry delays are respected, then exhausted abandoned leases fail',async(t)=>{
  const {db,raw}=fixture(t);
  await capture(db,'retry',{maxAttempts:2});
  let job=await claimJob(db,{now:clock,leaseMs:10});
  const failed=await failJob(db,{...key(job,clock+3),retryable:true,retryDelayMs:20});
  assert.equal(failed.state,'ready');
  assert.equal(await claimJob(db,{now:clock+22,leaseMs:10}),null);
  job=await claimJob(db,{now:clock+23,leaseMs:10});
  assert.equal(job.attempts,2);
  assert.equal(await sweepExpired(db,{now:clock+32}),0);
  assert.equal(await claimJob(db,{now:clock+33,leaseMs:10}),null);
  assert.equal(await sweepExpired(db,{now:clock+33}),1);
  assert.equal(raw.prepare('SELECT failure_code FROM memory_jobs').get().failure_code,'lease_exhausted');
  assert.equal(await finishJob(db,key(job,clock+34)),null);
});
test('bounded driver only processes requested count; no unintentional raw output',async(t)=>{
  const {db}=fixture(t);
  await capture(db,'one');
  await capture(db,'two');
  const seen=[];
  const stats=await processJobs(db,{limit:1,now:()=>clock,handle:async(job,{checkpoint,renew})=>{
    seen.push(job.id);
    assert.ok(await checkpoint('{"after":1}'));
    assert.ok(await renew());
    return {ok:true,result:'{}'};
  }});
  assert.deepEqual(seen,['one']);
  assert.deepEqual(stats,{swept:0,claimed:1,done:1,failed:0,retried:0,lost:0});
  assert.equal((await claimJob(db,{now:clock})).id,'two');
  await assert.rejects(processJobs(db,{limit:26,handle:()=>({ok:true})}),QueueInputError);
});
