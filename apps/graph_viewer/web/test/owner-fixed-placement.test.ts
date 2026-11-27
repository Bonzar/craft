import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutVisual3D} from '../src/visual-layout-3d.ts';

test('native layout keeps exact stored render coordinates and computes bounds from the same frame',()=>{
  const positions=Float32Array.of(-0,5,8, 90,12,3, 4,8,9);
  const layout=layoutVisual3D({sourcePositions:Float32Array.of(111,122,133, 150,160,170, 81,82,83),fixedPositions:positions,
    edges:Uint32Array.of(0,1,1,2),edgeActive:Uint8Array.of(1,1),themes:[{id:'fixture',index:0,members:[0,1,2]}]});
  assert.deepEqual(new Uint32Array(layout.positions.buffer),new Uint32Array(positions.buffer));
  assert.equal(layout.diagnostics.reusedNodes,3);
  assert.ok(layout.bounds.min[0]<=0&&layout.bounds.max[0]>=90);
});
