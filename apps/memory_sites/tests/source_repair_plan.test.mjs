import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {planIndependentSourceReview,planSourceRepair,sourceContinuationIsFresh} from '../source_repair_plan.mjs';

const drafts = [
  {text:'Ключ у Саши.',question:null},
  {text:'Датчик проверен.',question:'Когда проверен?'},
  {text:'Заслонка закрыта.',question:null},
];
const accept = {verdict:'accept',text:'Supported.',rejected_drafts:[],missing_meaning:null};
const reject = {verdict:'reject',text:'Exact review failed.',
  rejected_drafts:[{draft:2,text:'  Unsupported closure. '},{draft:1,text:'The timestamp is not supplied.'}],
  missing_meaning:'Only open after verification.'};
const crossCases = [
  {drafts,review:accept},
  {drafts,review:reject},
  {drafts,review:{...accept,verdict:'reject',missing_meaning:'Missing prerequisite.',rejected_drafts:[]}},
  {drafts:[],review:accept},
  {drafts:[],review:{...accept,verdict:'reject',missing_meaning:'Empty repair lost useful source meaning.'}},
];
const repairDrafts = [drafts[0],{text:drafts[0].text,question:'У кого ключ?'},drafts[2],drafts[2]];
const checkpoints = (() => {
  const head = {id:'thought-pin',version:2,origin:{event_id:'event-a'}};
  const fixture = {
    eventId:'event-a',extractedIds:['thought-pin'],
    checkpoint:{accepted:[{thought_id:'thought-pin',version:2}], context_ids:['old-context'],
      feedback:[{role:'missing_meaning',text:'Missing prerequisite.'}]},
    heads:[head],contextEvents:[{id:'old-context'}],
  };
  return [
    fixture,
    {...fixture,extractedIds:['other-id']},
    {...fixture,heads:[{...head,version:3}]},
    {...fixture,heads:[{...head,origin:{event_id:'event-b'}}]},
    {...fixture,heads:[null]},
    {...fixture,contextEvents:[null]},
    {...fixture,checkpoint:{...fixture.checkpoint,context_ids:[]},contextEvents:[]},
  ];
})();

test('decisions, repair coverage/dedup and checkpoint pin outcomes match original Python engine and worker',()=>{
  const python = spawnSync('python3',['-c',`
import json, sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_engine_contract import validate_response
from prototypes.assertion_memory.thought_worker import ThoughtWorker
fixture=json.load(sys.stdin)
out=[]
for case in fixture['cases']:
    pairs=[(draft, idx) for idx, draft in enumerate(case['drafts'])]
    validate_response('Verify thought',case['review'],source_draft_count=len(pairs))
    accepted,feedback=ThoughtEngine._source_review_outcome(pairs,case['review'])
    repair=None
    if feedback:
        engine=object.__new__(ThoughtEngine)
        catalog=SimpleNamespace(wire=[])
        engine._emit=lambda *args,**kwargs: None
        def fake_construct(catalog, input, instructions):
            records.extend(input)
            return [(row, index) for index,row in enumerate(fixture['repair'])], list(range(len(fixture['repair'])))
        records=[]
        with patch.object(engine, '_construct_drafts', fake_construct), patch.object(engine, '_review_source_drafts', lambda *args: fixture['accept']):
            repaired=engine._continue_source_draft_repair(catalog,{},accepted,feedback)
        repair=[records,[index for _,index in repaired]]
    out.append([[index for _,index in accepted],feedback,repair])
fresh=[]
for row in fixture['checkpoints']:
    @contextmanager
    def transaction():
        class Conn:
            def execute(self,*args):
                return self
            def fetchone(self):
                return [json.dumps(row['checkpoint'])]
        yield Conn()
    def lookup(entries, identity):
        value=next((x for x in entries if x and x['id']==identity),None)
        return SimpleNamespace(**value) if value else None
    worker=object.__new__(ThoughtWorker)
    worker.queue=SimpleNamespace(store=SimpleNamespace(transaction=transaction))
    worker.queue.get_event=lambda identity:lookup(row['contextEvents'],identity)
    worker.engine=SimpleNamespace(thoughts=SimpleNamespace(get=lambda identity:lookup(row['heads'],identity)))
    claim=SimpleNamespace(job=SimpleNamespace(id='job',event_id=row['eventId'],extracted_ids=row['extractedIds']))
    try:
        worker._load_source_draft_checkpoint(claim)
        fresh.append(True)
    except RuntimeError:
        fresh.append(False)
print(json.dumps([out,fresh],ensure_ascii=False))
`],{
    cwd:fileURLToPath(new URL('../../../',import.meta.url)),encoding:'utf8',
    input:JSON.stringify({cases:crossCases,accept,repair:repairDrafts,checkpoints}),
  });
  assert.equal(python.status,0,python.error?.message ?? python.stderr);
  const [expected,fresh] = JSON.parse(python.stdout);
  const actual = crossCases.map(({drafts,review})=>{
    const planned = planIndependentSourceReview(drafts,JSON.stringify(review));
    const {acceptedIndexes,feedback,next} = planned;
    if (next==='complete') return [acceptedIndexes,feedback,null];
    const repair=planSourceRepair(acceptedIndexes.map(index=>drafts[index]),feedback);
    return [acceptedIndexes,feedback,[repair.extraRecords,repair.selectNewIndexes(repairDrafts)]];
  });
  assert.deepEqual(actual,expected);
  assert.deepEqual(checkpoints.map(sourceContinuationIsFresh),fresh);
});

test('rejects invalid verifier structure, empty repair, and malformed checkpoint without planning',()=>{
  assert.throws(()=>planIndependentSourceReview(drafts,JSON.stringify({...accept,rejected_drafts:[{draft:3,text:'No basis.'}]})));
  assert.throws(()=>planIndependentSourceReview(drafts,JSON.stringify({...accept,verdict:'drill',open_support:0})));
  assert.throws(()=>planIndependentSourceReview(drafts,JSON.stringify({verdict:'accept',text:'Ordinary, not independent.'})));
  assert.throws(()=>planSourceRepair([],[]));
  assert.throws(()=>planSourceRepair([],reject.rejected_drafts));
  const [fixture]=checkpoints;
  assert.equal(sourceContinuationIsFresh({...fixture,checkpoint:{...fixture.checkpoint,feedback:[]}}),false);
});
