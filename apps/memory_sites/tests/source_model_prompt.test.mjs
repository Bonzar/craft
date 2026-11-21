import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createResponsesModel} from '../model_https.mjs';
import {CANONICAL_MODEL_INSTRUCTIONS, prepareInitialSourcePrompts} from '../source_model_prompt.mjs';

const event = {
  id:'synthetic-id-not-for-model', session_id:'synthetic-session-not-for-model', turn_id:'turn-example',
  actor:'fixture-user', source:'fixture://greenhouse/0', scope:'fixture-scope',
  recorded_at:'2026-11-15T10:03:01.000000+00:00',
  prompt:'🪴 Журнал учебной теплицы. Заслонка открывается после проверки датчика. Запасной ключ у Саши.',
  metadata:{role:'user'},
};
const draftOne = {text:'  Сначала проверить датчик, затем открыть заслонку. ',question:null,support_fragments:[1,1]};
const draftTwo = {text:'Запасной ключ у Саши.',question:'У кого ключ?',support_fragments:[2]};
const variants = [[], [draftOne], [draftOne,draftTwo]].map(thoughts => JSON.stringify({thoughts,needs_detail:null}));
const ordinary = {verdict:'accept',text:'Supported by the source.'};
const independent = {...ordinary,rejected_drafts:[],missing_meaning:null};
const candidates = [ordinary,ordinary,independent].map(reply => JSON.stringify(reply));

function oracle() {
  return spawnSync('python3', ['-c', `
import json, sys, tempfile
from pathlib import Path
from prototypes.assertion_memory.model import SEMANTIC_BASE_INSTRUCTIONS, format_prompt
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
payload = json.load(sys.stdin)
result = []
for construct, review in zip(payload['construct'], payload['review']):
    with tempfile.TemporaryDirectory() as temp, MemoryStore(Path(temp) / 'test.sqlite3') as store:
        calls = []
        replies = iter((construct, review))
        def model(request):
            calls.append({'task': request.task, 'prompt': format_prompt(request), 'schema': request.response_schema})
            return next(replies)
        engine = ThoughtEngine(ThoughtStore(store), model)
        raw = payload['event']
        text, _, origin, records, bases = engine._source_packet(raw)
        engine._prepare_source(records, bases, text, origin)
        result.append(calls)
print(json.dumps({'instructions': SEMANTIC_BASE_INSTRUCTIONS, 'expect': result}, ensure_ascii=False))
`], {
    cwd:fileURLToPath(new URL('../../../',import.meta.url)),
    input:JSON.stringify({event,construct:variants,review:candidates}),encoding:'utf8',
  });
}

test('construct and zero/single/multiple draft Verify requests match actual Python engine wire bytes', () => {
  const python = oracle();
  assert.equal(python.status,0,python.error?.message ?? python.stderr);
  const expected = JSON.parse(python.stdout);
  assert.equal(CANONICAL_MODEL_INSTRUCTIONS,expected.instructions);
  const prepared = prepareInitialSourcePrompts({source:JSON.stringify(event)});
  const data = ({contract: _contract,...wire}) => wire;
  for (const [index, reply] of variants.entries()) {
    const actual = [prepared.construct,prepared.review(reply)];
    for (const request of actual) {
      assert.equal(request.prompt.includes(event.id),false);
      assert.equal(request.prompt.includes(event.session_id),false);
      assert.equal(request.prompt.includes(event.source),true);
    }
    // Use equality booleans to avoid exceptionally long string dumps on drift.
    assert.equal(data(actual[0]).prompt === expected.expect[index][0].prompt,true,'construct prompt');
    assert.equal(data(actual[1]).prompt === expected.expect[index][1].prompt,true,'verify prompt');
    assert.deepEqual(actual.map(({schema})=>schema),expected.expect[index].map(({schema})=>schema));
    assert.deepEqual(actual[1].contract,index === 2 ? {sourceDraftCount:2} : {});
  }
});

test('assembled requests use separate configured writer/reviewer routes with canonical data', async () => {
  const prepared = prepareInitialSourcePrompts({source:JSON.stringify(event)});
  const sent = [];
  const provider = createResponsesModel({
    apiKey:'synthetic-key',constructModel:'synthetic-writer',reviewModel:'synthetic-reviewer',
    instructions:CANONICAL_MODEL_INSTRUCTIONS,
    fetchImpl: async (_url,init) => {
      const body = JSON.parse(init.body);
      sent.push(body);
      return Response.json({status:'completed',output:[{type:'message',role:'assistant',status:'completed',
        content:[{type:'output_text',text:body.model === 'synthetic-writer' ? variants[2] : candidates[2]}]}]});
    },
  });
  const constructReply = await provider(prepared.construct);
  assert.equal(await provider(prepared.review(constructReply)),candidates[2]);
  assert.deepEqual(sent.map(x=>x.model),['synthetic-writer','synthetic-reviewer']);
  assert.deepEqual(sent.map(x=>x.text.format.name),['construct_thought','verify_thought']);
});

test('source context, clocks, private reasoning and unoffered grounds are rejected before request assembly', () => {
  const source = JSON.stringify(event);
  assert.throws(() => prepareInitialSourcePrompts({source,context:[]}), TypeError);
  for (const metadata of [{source_message_at:null},{timezone:'UTC'},{role:'user',channel:'analysis'}]) {
    assert.throws(() => prepareInitialSourcePrompts({source:JSON.stringify({...event,metadata})}), TypeError);
  }
  const prepared = prepareInitialSourcePrompts({source});
  assert.throws(() => prepared.review(JSON.stringify({thoughts:[{...draftOne,support_fragments:[3]}]})),
    /existing prepared fragment options/);
  assert.throws(() => prepared.review(JSON.stringify({thoughts:[],needs_detail:{question:'Which sensor?',text:'Missing.'}})),
    /outside the initial adapter/);
});
