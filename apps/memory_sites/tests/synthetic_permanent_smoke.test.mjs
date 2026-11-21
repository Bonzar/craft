import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {getSyntheticPermanentSmoke,runSyntheticPermanentSmoke} from '../synthetic_permanent_smoke.mjs';
import {captureJob} from '../queue.mjs';
import {permanentPreview} from '../permanent_preview_route.mjs';

const sql=(order,folder='drizzle')=>readFileSync(new URL('../'+folder+'/'+order+'.sql',import.meta.url),'utf8');

function fixture(t,{mark=true}={}) {
  const dir=mkdtempSync(join(tmpdir(),'synthetic-permanent-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const path=join(dir,'d1.db');
  const raw=new DatabaseSync(path);
  const writes=[];
  const adapter=(raw,record=writes)=>({
    prepare(text){return {text,args:[],bind(...args){return {...this,args};}};},
    async batch(statements){
      raw.exec('BEGIN IMMEDIATE');
      try{
        for(const s of statements) if(!/^SELECT /i.test(s.text)) record.push(s.text);
        const result=statements.map(({text,args})=>({success:true,results:raw.prepare(text).all(...args)}));
        raw.exec('COMMIT');return result;
      } catch(e){raw.exec('ROLLBACK');throw e;}
    }
  });
  for(const name of ['0002_queue','0003_publications','0004_raw_capture','0005_semantic_thoughts','0006_live_source_commit'])
    raw.exec(sql(name));
  if(mark) raw.exec(sql('0001_preview_marker','smoke'));
  t.after(()=>raw.close());
  return {path,raw,db:adapter(raw),adapter,writes};
}

test('fresh accepted and rejected flow, independent readback after reopen and exact no-write repeat',async t=>{
  const {db,raw,path,writes,adapter}=fixture(t);
  assert.equal((await getSyntheticPermanentSmoke(db)).phase,'pending');
  assert.deepEqual(writes,[]);
  const result=await runSyntheticPermanentSmoke(db);
  assert.equal(result.ok,true);
  assert.deepEqual([result.provider_called,result.live_graph_changed],[false,false]);
  assert.equal(result.accepted.version,1);
  assert.equal(result.accepted.permanent,true);
  assert.equal(result.rejected.permanent,false);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_job_publications').get().n,0);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_live_source_commits').get().n,1);
  assert.equal(raw.prepare('SELECT count(*) n FROM memory_thought_versions').get().n,1);
  assert.deepEqual(raw.prepare('SELECT state FROM memory_jobs ORDER BY state').all().map(row=>row.state),['done','failed']);

  const readBack=new DatabaseSync(path);
  t.after(()=>readBack.close());
  const seen=[];
  assert.deepEqual(await getSyntheticPermanentSmoke(adapter(readBack,seen)),result);
  assert.deepEqual(await runSyntheticPermanentSmoke(adapter(readBack,seen)),result);
  assert.deepEqual(seen,[]);
});

test('unmarked DB does not write and existing jobs outside namespace are never claimed',async t=>{
  const a=fixture(t,{mark:false});
  assert.equal((await getSyntheticPermanentSmoke(a.db)).phase,'unavailable');
  assert.equal((await runSyntheticPermanentSmoke(a.db)).phase,'unavailable');
  assert.deepEqual(a.writes,[]);
  const b=fixture(t);
  await captureJob(b.db,{id:'v1-untouched',kind:'source-thoughts',body:'{}',now:Date.now()-1});
  assert.equal((await runSyntheticPermanentSmoke(b.db)).ok,true);
  assert.equal(b.raw.prepare("SELECT attempts FROM memory_jobs WHERE id='v1-untouched'").get().attempts,0);
});

test('only a verified permanent receipt enables the persisted graph link',async t=>{
  const {db}=fixture(t);
  const url='https://example.test/preview/permanent';
  const signed={'oai-authenticated-user-id':'fixture-owner'};
  const open=()=>permanentPreview(new Request(url,{headers:signed}),{DB:db});
  const before=await (await open()).text();
  assert.match(before,/href="\/explore"/);
  assert.doesNotMatch(before,/href="\/explore\/saved"/);
  const post=await permanentPreview(new Request(url,{method:'POST',headers:{...signed,origin:'https://example.test'}}),{DB:db});
  assert.equal(post.status,303);
  assert.equal(post.headers.get('location'),'/preview/permanent');
  const after=await (await open()).text();
  assert.match(after,/href="\/explore"/);
  assert.match(after,/href="\/explore\/saved"/);
});

test('simultaneous fresh runs converge to exactly one permanent thought and one rejection',async t=>{
  const f=fixture(t);
  await Promise.all([runSyntheticPermanentSmoke(f.db),runSyntheticPermanentSmoke(f.db)]);
  assert.equal((await getSyntheticPermanentSmoke(f.db)).ok,true);
  assert.equal(f.raw.prepare('SELECT count(*) n FROM memory_thoughts').get().n,1);
  assert.equal(f.raw.prepare('SELECT count(*) n FROM memory_raw_captures').get().n,2);
});

test('independent readback detects drift without repairing or promoting staged rows',async t=>{
  const f=fixture(t);
  const initial=await runSyntheticPermanentSmoke(f.db);
  assert.equal(initial.ok,true);
  f.raw.prepare('UPDATE memory_jobs SET checkpoint=NULL WHERE id=?').run(initial.rejected.event_id);
  const before=f.writes.length;
  const check=await getSyntheticPermanentSmoke(f.db);
  assert.equal(check.ok,false);
  assert.equal(check.phase,'pending');
  assert.deepEqual(check.accepted,initial.accepted);
  assert.equal(check.rejected,null);
  assert.equal(f.writes.length,before);
  assert.equal((await runSyntheticPermanentSmoke(f.db)).ok,false); // never reclaims completed jobs
});
