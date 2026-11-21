import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {createRawSessionReads} from '../mcp_raw_sessions.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const oracle=fileURLToPath(new URL('./mcp_raw_sessions_oracle.py',import.meta.url));
const fixture=JSON.parse(execFileSync('python',[oracle],{cwd:root,env:{...process.env,PYTHONPATH:root},encoding:'utf8'}));
function setup(){
  const sqlite=new DatabaseSync(':memory:');
  for(const migration of ['0002_queue.sql','0004_raw_capture.sql'])sqlite.exec(readFileSync(new URL('../drizzle/'+migration,import.meta.url),'utf8'));
  const d1={prepare(sql){const result=(...params)=>({async all(){return {results:sqlite.prepare(sql).all(...params).map(r=>({...r}))}}});return {bind:result,...result()};},
    batch:statements=>Promise.all(statements.map(s=>s.all()))};
  return {api:createRawSessionReads(d1),add(seed){
    for(const [raw,p] of seed)sqlite.prepare('INSERT INTO memory_raw_captures VALUES (?,?,?,?,?,?,?,?)').run(raw.id,raw.session_id,raw.turn_id,p,'fixture-receipt',JSON.stringify(raw),'0'.repeat(64),0);
  }};
}
test('canonical D1 session reads match original MCP for mixed native IDs, ordering, snapshots and budget',async()=>{
  const {api,add}=setup();
  for(const [name,args,expected,group] of fixture.expected){
    if(group==='empty')continue;
    if(!group&&fixture.expected[1][2]===expected)add(fixture.seed.slice(0,4));
    if(group==='budget')add(fixture.seed.slice(4));
    const result=await api[name](args);
    assert.deepEqual(result,expected,JSON.stringify({name,args}));
    assert.doesNotMatch(JSON.stringify(result),/NEVER EXPOSE|fixture-receipt/);
  }
});
test('empty store and strict argument failures never infer provenance',async()=>{
  const {api}=setup();
  assert.deepEqual(await api.memory_sessions(),fixture.expected[0][2]);
  await assert.rejects(api.memory_sessions({snapshot_id:-1}),/snapshot_id must be nonnegative/);
  await assert.rejects(api.memory_sessions({limit:201}),/limit between 1 and 200/);
  await assert.rejects(api.memory_session_messages({session_id:' '}),/nonempty ID/);
});
