import { test } from 'node:test';
import assert from 'node:assert/strict';
import { canReuseSnapshot } from '../src/reset-policy.ts';
const active={provider_id:'source-A',revision:'immutable-r1',selector:{mode:'current' as const,at:null,history:false,undated:'separate' as const}};
test('redundant same-revision reset keeps its accepted scene only within the same provider and selector',()=>{
  assert.equal(canReuseSnapshot(active,{...active,selector:{...active.selector}}),true);
  assert.equal(canReuseSnapshot(active,{...active,provider_id:'source-B'}),false);
  assert.equal(canReuseSnapshot(active,{...active,selector:{...active.selector,history:true}}),false);
  assert.equal(canReuseSnapshot(active,{...active,selector:{mode:'known_then',at:'2026-01-05T23:59:59.999Z',history:false,undated:'separate'}}),false);
});
test('a genuinely newer snapshot is never discarded even when source counts and geometry may happen to match',()=>{
  assert.equal(canReuseSnapshot(active,{...active,revision:'immutable-r2'}),false);
});
