import test from 'node:test';
import assert from 'node:assert/strict';
import {eventEligibility, prepareCapturedSource, sourceSpans, SOURCE_POLICY} from '../source_evidence.mjs';

const event = {id:'raw:id', session_id:'session-1', turn_id:'turn-1',
  source:'codex://fixture', actor:'user', scope:'test', recorded_at:'2027-01-01T00:00:00Z',
  metadata:{role:'user',channel:'final'}, prompt:'One. Two!',
};
const prepare = (value) => prepareCapturedSource({source:JSON.stringify(value)});
test('automatic source exclusions and precedence match canonical policy',()=>{
  const cases = [
    [{actor:'tool:fixture',metadata:{role:'user',channel:'reasoning'}},'automatic_codex_tool'],
    [{metadata:{role:'assistant',channel:'analysis',payload_type:'tool_call'}},'automatic_codex_private_channel'],
    [{metadata:{role:'assistant',response_item_type:'function_call_output'}},'automatic_codex_tool_payload'],
    [{metadata:{role:'system'}},'automatic_codex_non_dialogue_role'],
    [{actor:'developer',metadata:{role:'user'}},'automatic_codex_non_dialogue_role'],
    [{source:'other',metadata:{transport:'codex-desktop',channel:'reasoning'}},'automatic_codex_private_channel'],
    [{source:'other',metadata:'{"ingest_provider":"codex","role":"tool"}'},'automatic_codex_tool'],
    [{channel:'analysis',metadata:{channel:'final'}},null],
    [{channel:'analysis',metadata:{channel:null}},null],
    [{source:'manual://fixture',actor:'tool',metadata:{role:'tool'}},null],
    [{prompt:'A code snippet says function_call.',metadata:{role:'user',channel:'final'}},null],
  ];
  for (const [changes,reason] of cases) {
    const raw={...event,...changes};
    assert.deepEqual(eventEligibility(raw),{allowed:reason===null,reason,policy:SOURCE_POLICY});
    const result=prepare(raw);
    assert.equal(result.eligible,reason===null);
    assert.equal(result.reason,reason);
    if (reason) assert.deepEqual(result.fragments,[]);
  }
});
test('lossless PreparedSupport selects Python code-point spans and merges trailing whitespace',()=>{
  const prompt='  One🙂. \nTwo?\u001cThree\n\n 💡four;\t  ';
  const expected=[
    {start:0,end:9,text:'  One🙂. \n'},
    {start:9,end:14,text:'Two?\u001c'},
    {start:14,end:21,text:'Three\n\n'},
    {start:21,end:31,text:' 💡four;\t  '},
  ];
  assert.deepEqual(sourceSpans(prompt),expected);
  const result=prepare({...event,prompt});
  assert.equal(result.eligible,true);
  assert.equal(result.fragments.map(f=>f.text).join(''),prompt);
  assert.deepEqual(result.fragments.map(f=>JSON.parse(f.id)),
    expected.map(({start,end})=>['raw:id',start,end]));
  assert.deepEqual(sourceSpans('   '),[{start:0,end:3,text:'   '}]);
});
test('invalid or oversized capture and oversized catalog fail closed, never silently truncate',()=>{
  for (const invalid of [{prompt:null},{prompt:'\u001c\u0085'},{recorded_at:null},{metadata:[]},{metadata:'broken'},{id:'x\0'}]) {
    assert.equal(prepare({...event,...invalid}).reason,'invalid_raw_event');
  }
  assert.equal(prepareCapturedSource({source:'not an event'}).reason,'invalid_raw_event');
  assert.equal(prepare({...event,prompt:'🙂'.repeat(20000)}).reason,'invalid_raw_event');
  const many=prepare({...event,prompt:'Sentence. '.repeat(200)});
  assert.equal(many.reason,'prepared_source_exceeds_bounds');
  assert.deepEqual(many.fragments,[]);
});
