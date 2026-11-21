import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {captureJob, claimJob} from '../queue.mjs';
import {createPublisher, PublicationConflict, VerificationRejected} from '../publication.mjs';
const referenceTime = 1_800_000_000_000;
const receipt = {verifier:'test-evidence',policyVersion:'policy/v1',evidenceSha256:'a'.repeat(64)};
const payload = '{"synthetic":true}';
async function fixture(t) {
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
  await captureJob(db,{id:'job',kind:'synth',body:'{"source":"synthetic"}',now:referenceTime});
  const job=await claimJob(db,{now:referenceTime,leaseMs:100});
  return {db,raw,job,key:{id:job.id,leaseToken:job.lease_token,payload}};
}
test('trusted verifier receives both exact byte strings; publication is immutable and replay is idempotent',async t=>{
  const {db,raw,job,key}=await fixture(t);
  let calls=0;
  const publish=createPublisher({clock:()=>referenceTime+10,verify:async args=>{
    calls++;
    assert.equal(args.source,job.payload);
    assert.equal(args.sourceSha256,job.payload_sha256);
    assert.equal(args.payload,payload);
    assert.equal(Object.isFrozen(args),true);
    return receipt;
  }});
  const a=await publish(db,key);
  assert.equal(a.created,true);
  assert.equal(a.publication.payload_sha256.length,64);
  assert.equal(a.publication.verifier,receipt.verifier);
  assert.equal(Object.hasOwn(a.publication,'payload'),false);
  assert.equal(Object.hasOwn(a.publication,'lease_sha256'),false);
  assert.equal(raw.prepare('SELECT state FROM memory_jobs').get().state,'done');
  const b=await publish(db,key);
  assert.deepEqual(b,{...a,created:false});
  assert.equal(calls,1);
  await assert.rejects(publish(db,{...key,payload:'{"other":1}'}),PublicationConflict);
  assert.throws(()=>raw.exec('DELETE FROM memory_job_publications'),/immutable/);
  assert.throws(()=>raw.exec('UPDATE memory_job_publications SET verifier=\'x\''),/immutable/);
  assert.equal(raw.prepare('SELECT payload FROM memory_job_publications').get().payload,payload);
});
test('caller flags or missing evidence cannot authorize publication',async t=>{
  const {db,raw,key}=await fixture(t);
  const publish=createPublisher({clock:()=>referenceTime+10,verify:async()=>({verified:true})});
  await assert.rejects(publish(db,{...key,...receipt,verified:true}),VerificationRejected);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_job_publications').get().n,0);
  assert.equal(raw.prepare('SELECT state FROM memory_jobs').get().state,'running');
});
test('lease rotation during verification fences prior claim; concurrent replay writes only once',async t=>{
  const {db,raw,key}=await fixture(t);
  let current=referenceTime+10;
  const publish=createPublisher({clock:()=>current,verify:async()=>{
    current=referenceTime+100;
    await claimJob(db,{now:current,leaseMs:100});
    return receipt;
  }});
  assert.equal(await publish(db,key),null);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_job_publications').get().n,0);
  const newKey={...key,leaseToken:raw.prepare('SELECT lease_token FROM memory_jobs').get().lease_token};
  const publishNew=createPublisher({clock:()=>current,verify:async()=>receipt});
  const results=await Promise.all([publishNew(db,newKey),publishNew(db,newKey)]);
  assert.deepEqual(results.map(x=>x.created).sort(),[false,true]);
  assert.equal(raw.prepare('SELECT state FROM memory_jobs').get().state,'done');
  assert.equal(await publishNew(db,key),null);
});
test('completion failure rolls back the inserted publication',async t=>{
  const {db,raw,key}=await fixture(t);
  raw.exec(`CREATE TRIGGER deny_done BEFORE UPDATE ON memory_jobs
    WHEN NEW.state='done' BEGIN SELECT RAISE(ABORT, 'synthetic completion failure'); END;`);
  const publish=createPublisher({clock:()=>referenceTime+10,verify:async()=>receipt});
  await assert.rejects(publish(db,key),/synthetic completion failure/);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_job_publications').get().n,0);
  assert.equal(raw.prepare('SELECT state FROM memory_jobs').get().state,'running');
  raw.exec('DROP TRIGGER deny_done');
  assert.equal((await publish(db,key)).created,true);
});
