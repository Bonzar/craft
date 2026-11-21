import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {admitRawCapture} from '../raw_capture.mjs';
import {captureJob,claimJob,failJob,finishJob,saveCheckpoint} from '../queue.mjs';
import {getRawStatus,normalizeRawStatus,UnsupportedStatusSection} from '../raw_status.mjs';

const epoch=1_800_000_000_000;
async function fixture(t) {
  const raw=new DatabaseSync(':memory:');
  for(const file of ['0002_queue','0004_raw_capture']) {
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
  const add=(suffix,scope)=>admitRawCapture(db,{session_id:'synthetic',message_id:'id-'+suffix,actor_id:'user',
    role:'user',text:'Synthetic-private-prompt',scope,source:'test'}, {now:epoch+suffix});
  const [failed,running,done,ready]=await Promise.all([add(1,'alpha'),add(2,'alpha'),add(3,'beta'),add(4,'alpha')]);
  const f=await claimJob(db,{now:epoch+20});
  assert.equal(f.id,failed.event_id);
  await failJob(db,{id:f.id,leaseToken:f.lease_token,now:epoch+21});
  // Demonstrate future unknown errors never leak raw backend exception prose.
  raw.prepare("UPDATE memory_jobs SET failure_code='Synthetic-private-error' WHERE id=?").run(f.id);
  const r=await claimJob(db,{now:epoch+20});
  assert.equal(r.id,running.event_id);
  await saveCheckpoint(db,{id:r.id,leaseToken:r.lease_token,checkpoint:'"Synthetic-private-checkpoint"',now:epoch+21});
  const d=await claimJob(db,{now:epoch+20});
  assert.equal(d.id,done.event_id);
  await finishJob(db,{id:d.id,leaseToken:d.lease_token,result:'"Synthetic-private-result"',now:epoch+22});
  await captureJob(db,{id:'unrelated',kind:'source-thoughts',body:'"Synthetic-private-other"',now:epoch+5});
  return {db,failed,running,done,ready,runningKey:r.lease_token};
}

test('global and exact-scope counts, separate independent pages, no raw contents',async t=>{
  const {db,runningKey}=await fixture(t);
  const a=await getRawStatus(db,{scope:'alpha',limit:1});
  assert.deepEqual(a.job_counts,{ready:1,running:1,done:0,failed:1});
  assert.equal(a.raw_pending_count,3);
  assert.equal(a.raw_error_count,1);
  assert.equal(a.event_count,3);
  assert.equal(a.pending_jobs.length,1);
  assert.deepEqual(a.pagination.pending_jobs,{offset:0,limit:1,total:3,next_offset:1});
  assert.deepEqual(a.pagination.errors,{offset:0,limit:1,total:1,next_offset:null});
  assert.equal(a.errors[0].failure_code,'unspecified_failure');
  const next=await getRawStatus(db,{scope:'alpha',offset:1,limit:2});
  assert.equal(next.pending_jobs.length,2);
  assert.deepEqual(next.errors,[]);
  assert.equal(next.response_complete,true);
  const all=await getRawStatus(db);
  assert.equal(all.event_count,4); // unattached job does not count as raw
  assert.equal(all.job_counts.done,1);
  for (const result of [a,next,all]) {
    assert.equal(/Synthetic-private/.test(JSON.stringify(result)),false);
    assert.equal(JSON.stringify(result).includes(runningKey),false);
    assert.equal(result.coverage,'raw_capture_jobs_only');
    assert.ok(result.unavailable.includes('transport'));
  }
});

test('job detail is scope-checked and lossless across character pages',async t=>{
  const {db,running}=await fixture(t);
  const input={event_id:running.event_id,section:'job',scope:'alpha',limit:17};
  const wanted=(await getRawStatus(db,{event_id:running.event_id,scope:'alpha'})).job;
  let cursor=input, text='';
  while(cursor) {
    const part=await getRawStatus(db,cursor);
    text+=part.content;
    cursor=part.next_arguments;
  }
  assert.deepEqual(JSON.parse(text),wanted);
  assert.equal(wanted.state,'running');
  await assert.rejects(getRawStatus(db,{...input,scope:'beta'}),/Unknown raw job/);
  await assert.rejects(getRawStatus(db,{...input,event_id:'unrelated'}),/Unknown raw job/);
  await assert.rejects(getRawStatus(db,{...input,offset:99_999}),/outside/);
});

test('validates exact canonical page bounds for covered sections only',()=>{
  assert.equal(normalizeRawStatus().limit,100);
  assert.equal(normalizeRawStatus({limit:1000}).limit,1000);
  assert.equal(normalizeRawStatus({section:'job',event_id:'any',limit:12000}).limit,12000);
  for(const bad of [{limit:1001},{section:'job'},{offset:-1},{scope:'  '},{catalog_id:'some'}])
    assert.throws(()=>normalizeRawStatus(bad),TypeError);
  assert.throws(()=>normalizeRawStatus({section:'transport'}),UnsupportedStatusSection);
});
