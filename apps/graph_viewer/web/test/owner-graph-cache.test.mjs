import test from 'node:test';
import assert from 'node:assert/strict';
import {ownerGraphCache,ownerGraphCheckpointKey} from '../src/owner-graph-cache.mjs';
const auth={origin:'https://owner.invalid',stage:'test',owner:'a'.repeat(64),provider:'fixture',selector:{mode:'current'}};
const key=ownerGraphCheckpointKey(auth);
function fixture(cursor='2',epoch='e'.repeat(64)){return {epoch,revision:'f'.repeat(32),cursor,ids:['id1'],
  graph:{positions:Float32Array.of(1,2,3),flags:Uint32Array.of(0),edges:new Uint32Array(),edgeActive:new Uint8Array(),raw:'forbidden text'},
  visualPositions:Float32Array.of(4,5,6),text:'forbidden text',csrf:'secret',topics:['anything']};}
function store(){
  let stored;
  return {change(fn){const v=fn(stored);if(v.put)stored=v.put;else if(v.delete)stored=undefined;return Promise.resolve(v.result)},current:()=>stored};
}
test('atomic cache keeps only geometry and exact scope; mismatched owner removes it',async()=>{
  const storage=store(),c=ownerGraphCache(storage);
  assert.equal(await c.save(key,fixture()),true);
  const valid=await c.take(key);assert.deepEqual([...valid.visualPositions],[4,5,6]);
  assert.equal(Object.hasOwn(valid,'text'),false);assert.equal(Object.hasOwn(valid.graph,'raw'),false);
  assert.equal(Object.hasOwn(valid,'csrf'),false);assert.equal(Object.hasOwn(valid,'topics'),false);
  const wrong=ownerGraphCheckpointKey({...auth,owner:'b'.repeat(64)});
  assert.equal(await c.take(wrong),null);assert.equal(storage.current(),undefined);
  assert.equal(await c.take(key),null);
});
test('scope covers lane/provider/canonical filter and old same-epoch saved cursors cannot overwrite',async()=>{
  assert.notEqual(key,ownerGraphCheckpointKey({...auth,stage:'prod'}));
  assert.notEqual(key,ownerGraphCheckpointKey({...auth,provider:'other'}));
  assert.notEqual(key,ownerGraphCheckpointKey({...auth,selector:{mode:'current',session_id:'different'}}));
  assert.equal(ownerGraphCheckpointKey({...auth,owner:'secret'}),null);
  const c=ownerGraphCache(store());await c.save(key,fixture('12'));
  assert.equal(await c.save(key,fixture('11')),false);
  assert.equal((await c.take(key)).cursor,'12');
  assert.equal(await c.save(key,fixture('1','1'.repeat(64))),true);
  assert.equal((await c.take(key)).cursor,'1');
  await c.clear();assert.equal(await c.take(key),null);
});
test('storage error or storage disabled is ordinary cold start',async()=>{
  for(const blocked of [null,{change(){throw new Error('not allowed')}}]){
    const c=ownerGraphCache(blocked);
    assert.equal(await c.take(key),null);assert.equal(await c.save(key,fixture()),false);
    await c.clear();
  }
});
