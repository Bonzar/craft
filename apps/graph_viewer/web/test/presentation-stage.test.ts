import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reboundNodeOrdinal, topicIndicesForIds, visualStageMatches } from '../src/presentation-stage.ts';
test('full reset accepts a fresh ordinal only after stable source identity and new revision are verified',()=>{
  const target={provider_id:'source-A',revision:'new-R',id:'node-A'};
  const card={id:'node-A',revision:'new-R',ordinal:7};
  assert.equal(reboundNodeOrdinal(target,'source-A',card),7);
  assert.equal(reboundNodeOrdinal(target,'source-A',{...card,revision:'old-R',ordinal:0}),null);
  assert.equal(reboundNodeOrdinal(target,'source-A',{...card,id:'different-node'}),null);
  assert.equal(reboundNodeOrdinal(target,'source-B',card),null);
});
test('topic cut and selected shell rebind by stable topic ID rather than recycled membership index',()=>{
  const topics=[{id:'topic-B',index:1},{id:'topic-A',index:9}];
  assert.deepEqual(Array.from(topicIndicesForIds(['topic-A','removed-topic'],topics)),[9]);
});
test('atomic visual commit requires matching new LOD, fresh metrics and envelope revision together',()=>{
  const target={revision:'new-R',generation:4};
  const stage={lod:{revision:'new-R',generation:4},metrics:{revision:'new-R',lod_generation:4},envelopes:{revision:'new-R',lod_generation:4}};
  assert.equal(visualStageMatches(target,stage),true);
  assert.equal(visualStageMatches(target,{...stage,metrics:{revision:'old-R',lod_generation:3}}),false);
  assert.equal(visualStageMatches(target,{...stage,envelopes:{revision:'old-R',lod_generation:4}}),false);
});
