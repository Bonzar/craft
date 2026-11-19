import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sameSliceContext, contextForSelector } from '../src/slice-context.ts';
import { temporalSelector } from '../src/logic.ts';
const current={selector:temporalSelector('current','','UTC',false,'separate'),day:'2026-01-05',zone:'UTC'};
const past={selector:temporalSelector('known_then','2026-01-05','UTC',true,'separate'),day:'2026-01-05',zone:'UTC'};
test('manual date, perspective, timezone, history and undated changes each create a different reading context',()=>{
  const variants=[
    {...past,selector:temporalSelector('known_then','2026-01-06','UTC',true,'separate'),day:'2026-01-06'},
    {...past,selector:temporalSelector('known_now','2026-01-05','UTC',true,'separate')},
    {...past,selector:temporalSelector('known_then','2026-01-05','Europe/Moscow',true,'separate'),zone:'Europe/Moscow'},
    {...past,selector:{...past.selector,history:false}},
    {...past,selector:{...past.selector,undated:'only' as const}},
  ];
  for(const next of variants)assert.equal(sameSliceContext(past,next),false);
  assert.equal(sameSliceContext(current,past),false);
});
test('live revision replacement and same-selector recovery retain the same reading context',()=>{
  assert.equal(sameSliceContext(past,{...past,selector:{...past.selector}}),true);
  assert.equal(sameSliceContext(past,{...past,selector:{...past.selector,at:'2026-01-05T23:59:59.999+00:00'}}),true);
});
test('bookmark selectors use their exact instant and recover the matching available day and timezone',()=>{
  const fromMoscow={...current,zone:'Europe/Moscow'};
  const selected=contextForSelector(fromMoscow,past.selector);
  assert.equal(selected.zone,'UTC');assert.equal(selected.day,'2026-01-05');
  assert.equal(selected.selector.at,past.selector.at);
  assert.equal(sameSliceContext(selected,past),true);
});
