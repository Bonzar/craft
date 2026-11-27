import test from 'node:test';
import assert from 'node:assert/strict';
import {preserveOwnerGraphPositions} from '../src/owner-graph-position.mjs';

test('retained stable nodes and both endpoints stay bit-exact after ordinal remap; new nodes get only new placement',()=>{
  const previous={scope:'owner/filter',epoch:'old',ids:['a','removed','b'],
    positions:Float32Array.of(-0,15,200, 8,12,21, -42,31,37)};
  const next={scope:previous.scope,epoch:previous.epoch,ids:['b','new','a',null],
    positions:Float32Array.of(2000,3000,4000, 7,8,9, 500,600,700, 80,90,100)};
  const camera=Object.freeze({position:[22,-7,4],rotation:[0,0,0,1],zoom:3});
  const oldEdge=Float32Array.of(...previous.positions.subarray(0,3),...previous.positions.subarray(6,9));
  const result=preserveOwnerGraphPositions(previous,next);
  const sameEdge=Float32Array.of(...result.subarray(6,9),...result.subarray(0,3));
  assert.deepEqual(new Uint32Array(sameEdge.buffer),new Uint32Array(oldEdge.buffer));
  assert.deepEqual([...result.subarray(3,6)],[7,8,9]);
  assert.equal(next.ids.includes('removed'),false);
  assert.deepEqual(camera,{position:[22,-7,4],rotation:[0,0,0,1],zoom:3});
  assert.deepEqual([...next.positions.subarray(0,3)],[2000,3000,4000]);
});

test('new epoch and owner/selector fence prevent retention across contexts; tombstones never retain positions',()=>{
  const prior={scope:'owner1',epoch:'first',ids:['a'],positions:Float32Array.of(1,2,3)};
  const next={...prior,positions:Float32Array.of(10,20,30)};
  for(const other of [{...next,epoch:'second'},{...next,scope:'owner2'},{...next,ids:[null]}])
    assert.equal(preserveOwnerGraphPositions(prior,other),next.positions);
  assert.throws(()=>preserveOwnerGraphPositions(prior,{...next,ids:['a','a'],positions:new Float32Array(6)}),/stable identity/);
  assert.throws(()=>preserveOwnerGraphPositions(prior,{...next,positions:Float32Array.of(NaN,2,3)}),/stable identity/);
});
