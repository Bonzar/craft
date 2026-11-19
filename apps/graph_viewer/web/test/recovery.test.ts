import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SnapshotRecovery } from '../src/recovery.ts';
test('simultaneous expired-snapshot failures share one graph bootstrap and do not queue ordinal replays',async()=>{
  const recovery=new SnapshotRecovery();let calls=0;let finish:()=>void=()=>{};
  const bootstrap=()=>{calls++;return new Promise<void>(resolve=>{finish=resolve;});};
  const one=recovery.run(bootstrap);const two=recovery.run(bootstrap);
  assert.equal(calls,1);assert.equal(one,two);
  finish();await Promise.all([one,two]);
  assert.equal(calls,1);
});
test('a failed bootstrap releases recovery so an explicit later retry can recover the source',async()=>{
  const recovery=new SnapshotRecovery();let calls=0;
  await assert.rejects(recovery.run(async()=>{calls++;throw new Error('source unavailable');}));
  await recovery.run(async()=>{calls++;});
  assert.equal(calls,2);
});
