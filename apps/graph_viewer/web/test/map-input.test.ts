import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMapTap, moveMapTap, mapTapIsCurrent } from '../src/interaction.ts';
import { captionHasNativeOverflow } from '../src/caption-layout.ts';
const stamp={provider_id:'source-A',revision:'r1',generation:4};
test('a map tap uses maximum excursion, so an out-and-back drag never becomes a click',()=>{
  const tap=createMapTap(stamp,10,20);moveMapTap(tap,16,20);assert.equal(mapTapIsCurrent(tap,stamp),true);
  moveMapTap(tap,30,20);moveMapTap(tap,10,20);assert.equal(mapTapIsCurrent(tap,stamp),false);
});
test('explicit pointer cancellation remains cancelled and no source, revision or projection can reuse a pressed tap',()=>{
  const tap=createMapTap(stamp,0,0);assert.equal(mapTapIsCurrent(tap,{...stamp}),true);
  assert.equal(mapTapIsCurrent(tap,{...stamp,provider_id:'source-B'}),false);
  assert.equal(mapTapIsCurrent(tap,{...stamp,revision:'r2'}),false);
  assert.equal(mapTapIsCurrent(tap,{...stamp,generation:5}),false);
  tap.cancelled=true;moveMapTap(tap,0,0);assert.equal(mapTapIsCurrent(tap,stamp),false);
});
test('scrollable caption policy measures real overflow rather than merely overflow:auto',()=>{
  const short={scrollHeight:50,clientHeight:50,scrollWidth:150,clientWidth:150,overflowY:'auto',overflowX:'hidden'};
  assert.equal(captionHasNativeOverflow(short),false);
  assert.equal(captionHasNativeOverflow({...short,scrollHeight:100}),true);
  assert.equal(captionHasNativeOverflow({...short,scrollWidth:250}),false);
  assert.equal(captionHasNativeOverflow({...short,scrollWidth:250,overflowX:'auto'}),true);
  assert.equal(captionHasNativeOverflow({...short,scrollHeight:51}),false);
});
