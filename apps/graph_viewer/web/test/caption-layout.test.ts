import { test } from 'node:test';
import assert from 'node:assert/strict';
import { captionBox } from '../src/caption-layout.ts';
test('full source topic captions stay inside both horizontal viewport edges at phone width',()=>{
  for(const anchorX of [0,15,360,375]){
    const box=captionBox(anchorX-150,300,300,180,375,812,205);
    assert.ok(box.x>=12);
    assert.ok(box.x+box.width<=363);
    assert.equal(box.width,300);
  }
});
test('long wrapped full-title captions get scrollable space below controls instead of clipping outside short screens',()=>{
  const box=captionBox(-100,-90,300,300,320,480,205);
  assert.equal(box.x,12);
  assert.equal(box.width,296);
  assert.equal(box.y,205);
  assert.equal(box.height,200);
  assert.ok(box.y+box.height<=405);
});
