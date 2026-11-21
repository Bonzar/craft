import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {DatabaseSync} from 'node:sqlite';
import {createThoughtHistoryReader} from '../mcp_thought_history.mjs';
import {createRawSourceReader} from '../mcp_raw_source.mjs';
const emitted=spawnSync('python',['-m','apps.memory_sites.tests.mcp_thought_history_oracle'],{
  cwd:fileURLToPath(new URL('../../../',import.meta.url)),encoding:'utf8',
});
assert.equal(emitted.status,0,emitted.stderr);
const fixture=JSON.parse(emitted.stdout);

function database(t,{proofSchema=true,seedProofs=true}={}) {
  const raw=new DatabaseSync(':memory:');
  const migrations=['0001_graph','0002_queue','0003_publications','0004_raw_capture',
    '0005_semantic_thoughts','0006_live_source_commit'];
  if (proofSchema) migrations.push('0007_thought_reuse_checks');
  for (const file of migrations) {
    const migration=readFileSync(new URL('../drizzle/'+file+'.sql',import.meta.url),'utf8');
    for (const statement of migration.split('--> statement-breakpoint')) raw.exec(statement);
  }
  const capture=raw.prepare(`INSERT INTO memory_raw_captures
    (event_id,session_id,message_id,receipt,raw_json,raw_sha256,captured_at) VALUES (?,?,?,'test',?,?,0)`);
  for(const source of fixture.raw)capture.run(source.id,source.session_id,source.turn_id,JSON.stringify(source),'a'.repeat(64));
  const version=raw.prepare('INSERT INTO memory_thought_versions VALUES (?,?,?,?)');
  for(const thought of fixture.versions)version.run(thought.thought_id,thought.version,JSON.stringify(thought.payload),thought.content_hash);
  if(proofSchema&&seedProofs) {
    const question=raw.prepare('INSERT INTO memory_thought_reuse_checks VALUES (?,?,?,?,?)');
    for(const q of fixture.questions)question.run(q.thought_id,q.version,q.question,q.frontier,q.proofs);
  }
  const db={
    prepare(sql) {return {sql,args:[],bind(...args){return {...this,args};}};},
    async batch(statements) {
      raw.exec('BEGIN');
      try {
        const result=statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');return result;
      } catch(error) {raw.exec('ROLLBACK');throw error;}
    },
  };
  t.after(()=>raw.close());
  return {raw,db};
}

test('canonical dispatcher parity incl alternate questions and pinned past version under current updates',async t=>{
  const {db,raw}=database(t);
  const read=createThoughtHistoryReader(db,{authorizedScope:'fictional-allowed',authorize:()=>true});
  for(const [args,expected] of fixture.expected)assert.deepEqual(await read(args),expected,JSON.stringify(args));
  const mostRecent=fixture.versions.find(v=>v.thought_id==='answer-fixture'&&v.version===2);
  raw.prepare('INSERT INTO memory_thought_versions VALUES (?,?,?,?)').run('answer-fixture',3,
    JSON.stringify({...mostRecent.payload,version:3,text:'A later version.'}),'a'.repeat(64));
  const pinned=fixture.expected.find(([args])=>args.snapshot_id===1&&args.section==='versions');
  assert.deepEqual(await read(pinned[0]),pinned[1]);
  assert.equal((await read({view_id:'answer-fixture'})).snapshot_id,3);
});

test('linked Unicode span exactly returns ёж; direct and transitive foreign scopes deny',async t=>{
  const {db}=database(t);
  const trusted={authorizedScope:'fictional-allowed',authorize:()=>true};
  const read=createThoughtHistoryReader(db,trusted);
  const rawRead=createRawSourceReader(db,trusted);
  assert.deepEqual(await rawRead(fixture.span[0]),fixture.span[1]);
  assert.equal(fixture.span[1].span.text,'ёж');
  for(const view_id of fixture.denied)await assert.rejects(read({view_id}),/Unknown thought/);
  await assert.rejects(createThoughtHistoryReader(db,{...trusted,authorize:()=>false})({view_id:'answer-fixture'}),/Unknown thought/);
});

test('without question applicability projection, fail closed rather than invent questions',async t=>{
  const {db}=database(t,{proofSchema:false});
  const read=createThoughtHistoryReader(db,{authorizedScope:'fictional-allowed',authorize:()=>true});
  await assert.rejects(read({view_id:'answer-fixture'}),/proof schema/);
});


test('real D1-form migration matches Python; default, empty and foreign proofs retain correct scope and version',async t=>{
  const {db,raw}=database(t,{seedProofs:false});
  const columns=raw.prepare('PRAGMA table_info(memory_thought_reuse_checks)').all().map(row=>({...row}));
  assert.deepEqual(columns,fixture.proof_schema);
  const read=createThoughtHistoryReader(db,{authorizedScope:'fictional-allowed',authorize:()=>true});
  const input={view_id:'answer-fixture',version_id:1,snapshot_id:1};
  const questions=async()=>JSON.parse((await read(input)).content)[0].questions;
  assert.deepEqual(await questions(),['Что сказано?']);
  const insert=raw.prepare('INSERT INTO memory_thought_reuse_checks VALUES (?,?,?,?,?)');
  const actual=fixture.questions.find(row=>row.thought_id==='answer-fixture'&&row.version===1);
  insert.run(actual.thought_id,actual.version,actual.question,actual.frontier,actual.proofs);
  insert.run('answer-fixture',1,'Foreign context proof','foreign-context',
    JSON.stringify([{frontier:'foreign-context',context:{scope:'fictional-denied'},checked_at:null}]));
  insert.run('answer-fixture',2,'Foreign version proof','foreign-version',actual.proofs);
  insert.run('foreign-fixture',1,'Foreign thought proof','foreign-thought',actual.proofs);
  assert.deepEqual(await questions(),['Какое животное?','Что сказано?']);
  raw.prepare('INSERT INTO memory_thought_reuse_checks (thought_id,version,question,frontier) VALUES (?,?,?,?)')
    .run('answer-fixture',1,'Legacy empty witnesses','legacy');
  assert.equal(raw.prepare(`SELECT proofs FROM memory_thought_reuse_checks WHERE question='Legacy empty witnesses'`).get().proofs,'[]');
  assert.deepEqual(await questions(),['Legacy empty witnesses','Какое животное?','Что сказано?']);
  assert.throws(()=>insert.run(actual.thought_id,actual.version,actual.question,'duplicate',actual.proofs),/UNIQUE constraint/);
  await assert.rejects(read({view_id:'foreign-fixture'}),/Unknown thought/);
});
