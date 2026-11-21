import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {requiresLinkVerification,selfCorrectionProof,reportedDecisionProof} from '../link_evidence.mjs';

test('synthetic predecessor evidence and strict negative cases agree with original Python',()=>{
  const endpoint=(event_id,quote,actor='owner',role='user')=>({actor,metadata:{event_id,
    interpretation:{status:'model_verified'},evidence_refs:[{event_id,start:0,end:[...quote].length,quote,role}]}});
  const source=endpoint('west','Переносим стойку на запад 😀');
  const target=endpoint('east','Стойка на востоке');
  const span=(event_id,end=4,role='user')=>({event_id,start:0,end,role,relation:'statement'});
  const proof={status:'accepted',basis:'reported_decision',decision_evidence:{
    source_refs:[span('west')],target_refs:[span('east')]}};
  const correction={status:'accepted',basis:'self_correction',correction_evidence:{source:span('west'),target:span('east')}};
  const cases=[
    {tag:'gate',args:['supersedes',source.metadata,target.metadata]},
    {tag:'gate',args:['supports',source.metadata,target.metadata]},
    {tag:'gate',args:['depends_on',{},{}]},
    {tag:'correction',args:[source,target,correction]},
    {tag:'correction',args:[source,{...target,actor:'other'},correction]},
    {tag:'reported',args:[source,target,proof,true]},
    {tag:'reported',args:[source,target,{...proof,status:'unresolved'},true]},
    {tag:'reported',args:[source,target,{...proof,decision_evidence:{...proof.decision_evidence,source_refs:[span('different')]}},true]},
    {tag:'reported',args:[source,target,{...proof,decision_evidence:{...proof.decision_evidence,source_refs:[span('west',99)]}},true]},
    {tag:'reported',args:[source,target,{...proof,decision_evidence:{...proof.decision_evidence,source_refs:[span('west',4,'assistant')]}},false]},
    {tag:'reported',args:[endpoint('west','Предлагаю шкафчики','owner','assistant'),target,proof,true]},
  ];
  const py=`
import json,sys
from prototypes.assertion_memory import link_proof as p
r=[]
for row in json.load(sys.stdin):
    a=row['args']; tag=row['tag']
    if tag=='gate': v=p.requires_link_verification(*a)
    elif tag=='correction': v=p.self_correction_proof(*a)
    else: v=p.reported_decision_proof(*a[:3],resolved=a[3])
    r.append(v)
print(json.dumps(r))
`;
  const cwd=fileURLToPath(new URL('../../../',import.meta.url));
  const expected=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',py],
    {cwd,encoding:'utf8',input:JSON.stringify(cases)}));
  const actual=cases.map(({tag,args:a})=>tag==='gate'?requiresLinkVerification(...a):
    tag==='correction'?selfCorrectionProof(...a):reportedDecisionProof(...a.slice(0,3),{resolved:a[3]}));
  assert.deepEqual(actual,expected);
  assert.equal(actual[5].decision_evidence.source_refs[0].event_id,'west');
  assert(!('quote' in actual[5].decision_evidence.source_refs[0]));
  assert.equal(actual[10],null);
});
