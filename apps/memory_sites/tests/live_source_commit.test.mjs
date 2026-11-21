import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {DatabaseSync} from 'node:sqlite';
import {createLiveSourceCommitter} from '../live_source_commit.mjs';
import {admitRawCapture} from '../raw_capture.mjs';
import {claimJob} from '../queue.mjs';
import {createPublisher} from '../publication.mjs';

const rawInput={session_id:'synthetic-session',message_id:'message-1',
  actor_id:'user',role:'user',text:'The 🍀 fixture. The lid is green.',source:'fixture://only'};
const candidate=JSON.stringify({thoughts:[{text:'Synthetic fixture contains a clover.',question:null,support_fragments:[0]}],needs_detail:null});
const accepted=JSON.stringify({verdict:'accept',text:'Result matches the cited source.'});
const rejected=JSON.stringify({verdict:'reject',text:'Result overstates the source.'});
const migrations=['0002_queue','0003_publications','0004_raw_capture','0005_semantic_thoughts','0006_live_source_commit'];
function adapter(raw) {
  return {
    prepare(sql) { return {sql,args:[],bind(...args){return {...this,args}}}; },
    async batch(statements) {
      raw.exec('BEGIN IMMEDIATE');
      try {
        const results=statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');
        return results;
      } catch(err) {raw.exec('ROLLBACK');throw err;}
    },
  };
}
async function fixture(t,path=':memory:') {
  const raw=new DatabaseSync(path);
  for(const migration of migrations) {
    const sql=readFileSync(new URL('../drizzle/'+migration+'.sql',import.meta.url),'utf8');
    for(const statement of sql.split('--> statement-breakpoint')) raw.exec(statement);
  }
  let closed=false;
  const close=()=>{if(!closed){ closed=true; raw.close(); }};
  t.after(close);
  const db=adapter(raw);
  const base=Date.now()+2000;
  const capture=await admitRawCapture(db,rawInput,{now:base});
  const job=await claimJob(db,{now:base,leaseMs:60_000});
  return {raw,db,base,capture,job,close,key:{id:job.id,leaseToken:job.lease_token}};
}
const worker=(base,more={})=>createLiveSourceCommitter({clock:()=>base+1,
  authorizeSource:async()=>true,construct:async()=>candidate,review:async()=>accepted,...more});
function counts(raw) {
  return ['memory_thought_versions','memory_thoughts','memory_live_source_commits'].map(name=>
    raw.prepare('SELECT count(*) n FROM '+name).get().n);
}
test('independent acceptance, durable restart replay, canonical Python origin/basis/content SHA',async t=>{
  const folder=mkdtempSync(join(tmpdir(),'source-oracle-'));
  const path=join(folder,'test.db');
  t.after(()=>rmSync(folder,{recursive:true,force:true}));
  const {raw,db,base,key,close}=await fixture(t,path);
  let called=0;
  const run=worker(base,{construct:async request=>{
    called++;
    assert.equal(request.task,'Construct thought');
    return candidate;
  },review:async request=>{
    called++;
    assert.equal(request.task,'Verify thought');
    assert.ok(request.prompt.includes('Synthetic fixture contains a clover.'));
    assert.deepEqual(counts(raw),[0,0,0]);
    return accepted;
  }});
  const result=await run(db,key);
  assert.equal(called,2);
  assert.equal(result.status,'committed');
  assert.deepEqual(counts(raw),[1,1,1]);
  assert.equal(raw.prepare('SELECT state FROM memory_jobs').get().state,'done');
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_job_publications').get().n,0);
  close();
  const check=new DatabaseSync(path);
  t.after(()=>check.close());
  const restart=worker(base+100_000,{review:async()=>{throw Error('No new review on replay');}});
  assert.deepEqual(await restart(adapter(check),key),result);
  assert.deepEqual(await restart(adapter(check),{...key,leaseToken:crypto.randomUUID()}),{status:'lost'});
  const row=check.prepare('SELECT payload,content_hash FROM memory_thought_versions').get();
  const rawSource=check.prepare('SELECT raw_json FROM memory_raw_captures').get().raw_json;
  const oracle=spawnSync('python',['-c',`
import sys,json
from dataclasses import asdict
from prototypes.assertion_memory.thought_store import Thought, ThoughtStore, _hash
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.capture import RawEvent
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
raw, js = json.load(sys.stdin)
with MemoryStore(':memory:') as store:
 q=CaptureQueue(store)
 t=ThoughtStore(store)
 event=q.capture_event(raw['session_id'],raw['turn_id'],raw['prompt'],source=raw['source'],scope=raw['scope'],
   actor=raw['actor'],message_id=raw['turn_id'],metadata=raw['metadata'],recorded_at=raw['recorded_at'])
 origin=ThoughtEngine._raw_origin(asdict(event))
 origin.update(kind='source_interpretation',event_id=event.id)
 with store.transaction() as db:
  bases=[t._pin_basis(db,{'event_id':event.id,'span':basis['span'],'meaning':basis['meaning']}) for basis in js['bases']]
 expected=Thought(js['id'],1,js['text'],js['question'],origin,bases,
    {'protocol':'unified-thought-v1','verification':'accepted'},js['created_at'])
 assert asdict(expected)==js,(expected,js)
 print(_hash(asdict(expected)))
`],{cwd:fileURLToPath(new URL('../../../',import.meta.url)),encoding:'utf8',input:JSON.stringify([JSON.parse(rawSource),JSON.parse(row.payload)])});
  assert.equal(oracle.status,0,oracle.stderr);
  assert.equal(oracle.stdout.trim(),result.content_hash);
  assert.equal(row.content_hash,result.content_hash);
});
test('refusal, rejection, malformed draft and foreign token never commit',async t=>{
  const {db,raw,base,key}=await fixture(t);
  assert.deepEqual(await worker(base,{review:async()=>rejected})(db,key),{status:'rejected'});
  assert.deepEqual(await worker(base,{authorizeSource:async()=>false})(db,key),{status:'refused'});
  assert.deepEqual(await worker(base,{construct:async()=>candidate.replace('[0]','[99]')})(db,key),{status:'rejected'});
  assert.deepEqual(await worker(base)(db,{...key,leaseToken:crypto.randomUUID()}),{status:'lost'});
  assert.deepEqual(counts(raw),[0,0,0]);
  assert.equal(raw.prepare('SELECT state FROM memory_jobs').get().state,'running');
});
test('source mutation, context arrival and lease loss while reviewing block permanent write',async t=>{
  for(const interruption of ['raw','context','expired','database-clock','lease-reclaimed']) {
    const {db,raw,base,key}=await fixture(t);
    let now=base+1;
    const run=worker(base,{clock:()=>now,review:async()=>{
      if(interruption==='raw') raw.exec("UPDATE memory_raw_captures SET raw_json=json_set(raw_json,'$.prompt','Changed')");
      if(interruption==='context') await admitRawCapture(db,{...rawInput,message_id:'neighbor'},{now});
      if(interruption==='expired') now=base+60_001;
      if(interruption==='database-clock') {
        now=1; // trusted-clock fault must not allow a lease expired in SQLite
        raw.prepare('UPDATE memory_jobs SET lease_expires_at=?').run(Date.now()-1000);
      }
      if(interruption==='lease-reclaimed') {
        const takeover=await claimJob(db,{now:base+60_001,leaseMs:60_000});
        assert.notEqual(takeover.lease_token,key.leaseToken);
      }
      return accepted;
    }});
    assert.deepEqual(await run(db,key),{status:'lost'});
    assert.deepEqual(counts(raw),[0,0,0]);
  }
});
test('provisional staged artifact cannot promote, and job completion failure rolls everything back',async t=>{
  const a=await fixture(t);
  const transient=createPublisher({clock:()=>a.base+1,verify:async()=>({verifier:'test-review',policyVersion:'test/v1',evidenceSha256:'a'.repeat(64)})});
  assert.equal((await transient(a.db,{...a.key,payload:'{"synthetic":true}'})).created,true);
  assert.deepEqual(await worker(a.base)(a.db,a.key),{status:'lost'});
  assert.deepEqual(counts(a.raw),[0,0,0]);
  const b=await fixture(t);
  b.raw.exec(`CREATE TRIGGER fixture_fail_done BEFORE UPDATE ON memory_jobs WHEN NEW.state='done'
    BEGIN SELECT RAISE(ABORT,'synthetic completion failure'); END;`);
  await assert.rejects(worker(b.base)(b.db,b.key),/synthetic completion failure/);
  assert.deepEqual(counts(b.raw),[0,0,0]);
  assert.equal(b.raw.prepare('SELECT state FROM memory_jobs').get().state,'running');
  b.raw.exec('DROP TRIGGER fixture_fail_done');
  assert.equal((await worker(b.base)(b.db,b.key)).status,'committed');
  assert.deepEqual(counts(b.raw),[1,1,1]);
});
