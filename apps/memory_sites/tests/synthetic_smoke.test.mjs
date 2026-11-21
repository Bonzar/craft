import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {runSyntheticStagingSmoke} from '../synthetic_smoke.mjs';
import {admitRawCapture} from '../raw_capture.mjs';

function fixture(t,marker=true) {
  const raw=new DatabaseSync(':memory:');
  for(const file of ['0002_queue','0003_publications','0004_raw_capture'])
    raw.exec(readFileSync(new URL('../drizzle/'+file+'.sql',import.meta.url),'utf8'));
  if(marker) raw.exec(readFileSync(new URL('../smoke/0001_preview_marker.sql',import.meta.url),'utf8'));
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

test('dedicated preview deterministically runs accepted and rejected fixture, no external fetch or new rows on replay',async t=>{
  const {db,raw}=fixture(t);
  const originalFetch = globalThis.fetch;
  globalThis.fetch=()=>{throw new Error('Network is forbidden in smoke');};
  t.after(()=>{globalThis.fetch=originalFetch;});
  const unrelated=await admitRawCapture(db,{session_id:'fixture-unrelated',message_id:'one',actor_id:'user',
    role:'user',text:'Fixture unrelated.',scope:'fixture-unrelated',source:'fixture://unrelated'},
    {now:1});
  const once=await runSyntheticStagingSmoke(db);
  assert.deepEqual(once,{
    ok:true,kind:'synthetic-staging-smoke',provider_called:false,live_graph_changed:false,
    accepted:{prepared:true,constructed:true,reviewed:true,outcome:'published',queue_state:'done',staged:true},
    rejected:{prepared:true,constructed:true,reviewed:true,outcome:'retrying',queue_state:'ready',staged:false},
  });
  assert.deepEqual(await runSyntheticStagingSmoke(db),once);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_raw_captures').get().n,3);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_job_publications').get().n,1);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_synthetic_smoke_checks').get().n,2);
  assert.equal(raw.prepare('SELECT attempts FROM memory_jobs WHERE id=?').get(unrelated.event_id).attempts,0);
  assert.equal(JSON.stringify(once).includes('event-'),false);
});

test('refuses ordinary database without provisioning marker before any write',async t=>{
  const {db,raw}=fixture(t,false);
  await assert.rejects(runSyntheticStagingSmoke(db),/not configured/);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_raw_captures').get().n,0);
});
