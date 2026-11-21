import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {createRawSourceReader,UnavailableSourceSection} from '../mcp_raw_source.mjs';

const emitted=spawnSync('python',['-m','apps.memory_sites.tests.mcp_raw_source_oracle'],{
  cwd:fileURLToPath(new URL('../../../',import.meta.url)),encoding:'utf8',
});
assert.equal(emitted.status,0,emitted.stderr);
const oracle=JSON.parse(emitted.stdout);

function fixture(t) {
  const raw=new DatabaseSync(':memory:');
  for(const name of ['0002_queue','0004_raw_capture'])
    raw.exec(readFileSync(new URL('../drizzle/'+name+'.sql',import.meta.url),'utf8'));
  const insert=raw.prepare(`INSERT INTO memory_raw_captures
    (event_id,session_id,message_id,receipt,raw_json,raw_sha256,captured_at)
    VALUES (?,?,?,'fixture',?,?,0)`);
  for(const event of oracle.seed) {
    // Deliberately reorder metadata fields: D1 and Python serializers differ.
    const metadata=Object.fromEntries(Object.entries(event.metadata).reverse());
    insert.run(event.id,event.session_id,event.turn_id,JSON.stringify({...event,metadata}),'a'.repeat(64));
  }
  let queries=0;
  const db={
    prepare(sql){return {sql,args:[],bind(...args){return {...this,args};}};},
    async batch(statements) {
      queries+=statements.length;
      return statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
    },
  };
  t.after(()=>raw.close());
  return {db,queries:()=>queries};
}

test('exact original dispatcher parity on raw metadata, UTF-32 offsets, spans and response-budget continuation',async t=>{
  const {db}=fixture(t);
  const reader=createRawSourceReader(db,{authorizedScope:'allowed-fixture',authorize:async()=>true});
  for(const [args,expected] of oracle.expected) assert.deepEqual(await reader(args),expected,args.section??'span');
  assert.ok(oracle.expected.some(([args,result])=>args.section==='text'&&args.limit===12000&&result.end<args.limit));
  assert.ok(oracle.expected.some(([args,result])=>args.end===6000&&result.response_complete===false));
});

test('exact trusted scope and invocation authorization, no caller override or section fallback',async t=>{
  const f=fixture(t);
  const record_id=oracle.expected[0][0].record_id;
  assert.throws(()=>createRawSourceReader(f.db,{authorizedScope:'*',authorize:async()=>true}),/exact trusted scope/);
  const blocked=createRawSourceReader(f.db,{authorizedScope:'allowed-fixture',authorize:async()=>false});
  await assert.rejects(blocked({record_id,section:'text'}),/Unknown source/);
  assert.equal(f.queries(),0);
  const checks=[];
  const reader=createRawSourceReader(f.db,{authorizedScope:'allowed-fixture',authorize:async context=>{
    checks.push(context);
    return true;
  }});
  await assert.rejects(reader({record_id:oracle.denied,section:'text'}),/Unknown source/);
  assert.deepEqual(checks,[{record_id:oracle.denied,scope:'allowed-fixture',section:'text'}]);
  await assert.rejects(reader({record_id:oracle.denied,section:'text',scope:'other-fixture'}),/Invalid memory_source/);
  for(const section of ['overview','edges','references','extraction_history'])
    await assert.rejects(reader({record_id,section}),UnavailableSourceSection);
  assert.equal(f.queries(),1);
});
