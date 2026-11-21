import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {admitRawCapture,CapturePositionConflict,UnsupportedCapture} from '../raw_capture.mjs';
import {captureJob,claimJob} from '../queue.mjs';
import {prepareCapturedSource} from '../source_evidence.mjs';

const reference=1_800_000_000_123;
const capture={session_id:'fixture',message_id:'message-1',actor_id:'user',role:'user',text:'Ё🙂\n"box".',
  position:2,source:'3rd-party'};
const eventId='event-0673110088c38eb952b978c35a6b9b8ad85c58c3e1bc54dc064b62cc2ac67caf';
const receipt='sha256:e388134b9dc3fc4872f18e1f67265b24ba95d5e396ef5b69b848185314811636';
function fixture(t) {
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
  return {db,raw};
}
test('canonical event/receipt with atomic claimable job, exact replay and omitted ordinal',async t=>{
  const {db,raw}=fixture(t);
  const result=await admitRawCapture(db,capture,{now:reference});
  assert.deepEqual(result,{created:true,event_id:eventId,position:2,recorded_at:'2027-01-15T08:00:00.123000+00:00',
    job:{id:eventId,state:'ready',attempts:0}});
  const row=raw.prepare('SELECT receipt,raw_json FROM memory_raw_captures').get();
  assert.equal(row.receipt,receipt);
  const claimed=await claimJob(db,{now:reference+1});
  assert.equal(claimed.payload,row.raw_json);
  assert.equal(claimed.kind,'source-thoughts');
  assert.equal(prepareCapturedSource({source:claimed.payload}).eligible,true);
  assert.deepEqual(await admitRawCapture(db,{...capture,position:null},{now:reference+1}),
    {...result,created:false,job:{id:eventId,state:'running',attempts:1}});
  assert.deepEqual(await admitRawCapture(db,capture,{now:reference+2}),
    {...result,created:false,job:{id:eventId,state:'running',attempts:1}});
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_jobs').get().n,1);
});
test('atomic position races, immutable replay content, no backfilling position',async t=>{
  const {db,raw}=fixture(t);
  const results=await Promise.allSettled([
    admitRawCapture(db,capture,{now:reference}),admitRawCapture(db,{...capture,message_id:'other'},{now:reference}),
  ]);
  assert.equal(results.filter(result=>result.status==='fulfilled').length,1);
  assert.ok(results.find(result=>result.status==='rejected').reason instanceof CapturePositionConflict);
  const winner=raw.prepare('SELECT message_id FROM memory_raw_captures').get().message_id;
  await assert.rejects(admitRawCapture(db,{...capture,message_id:winner,text:'changed'},{now:reference}),/immutable content/);
  await assert.rejects(admitRawCapture(db,{...capture,message_id:winner,position:99},{now:reference}),/ordinal/);
  const unknown={...capture,message_id:'unknown',position:null};
  const a=await admitRawCapture(db,unknown,{now:reference});
  const b=await admitRawCapture(db,{...unknown,position:2},{now:reference+1});
  assert.deepEqual(b,{...a,created:false});
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_jobs').get().n,2);
});
test('failed linked job insert rolls back raw event; unsupported claims never admit',async t=>{
  const {db,raw}=fixture(t);
  await captureJob(db,{id:eventId,kind:'generic',body:'{}',now:reference});
  await assert.rejects(admitRawCapture(db,capture,{now:reference}),/UNIQUE constraint/);
  await assert.rejects(admitRawCapture(db,{...capture,delivery:{stream_id:'s',epoch:1,seq:1}},{now:reference}),UnsupportedCapture);
  await assert.rejects(admitRawCapture(db,{...capture,artifact_ids:['artifact-'+'a'.repeat(64)]},{now:reference}),UnsupportedCapture);
  await assert.rejects(admitRawCapture(db,{...capture,text:'a'.repeat(65536)},{now:reference}),UnsupportedCapture);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_raw_captures').get().n,0);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_jobs').get().n,1);
});
