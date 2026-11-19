import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickShell, boundedShellIndices, readingPolicy, OccupiedRects } from '../src/ux-policy.ts';
const shell=(id:string,index:number,radius:number,pixels:number)=>({topic_id:id,topic_index:index,center:[0,0,-10] as [number,number,number],radius,screen:{x:100,y:100,radius:pixels}});
function direction(x:number):[number,number,number]{const length=Math.hypot(x,1);return[x/length,0,-1/length];}
test('actual bounded shell rim resolves its stable source ID while overlapping interiors prefer the smallest shell',()=>{
  const candidates=[shell('parent',9,2,51),shell('child',2,1,25)];
  const rim=pickShell(candidates,[0,0,0],direction(49/250),[149,100],375);
  assert.deepEqual(rim,{kind:'shell',topic_id:'parent',topic_index:9});
  const inside=pickShell(candidates,[0,0,0],[0,0,-1],[100,100],375);
  assert.deepEqual(inside,{kind:'shell',topic_id:'child',topic_index:2});
  assert.deepEqual(pickShell(candidates.toReversed(),[0,0,0],[0,0,-1],[100,100],375),inside);
});
test('indistinguishable overlapping source shells offer honest source choices and huge ancestors do not swallow blank taps',()=>{
  const ambiguous=pickShell([shell('A',1,2,51),shell('B',2,2,51)],[0,0,0],[0,0,-1],[100,100],375);
  assert.deepEqual(ambiguous,{kind:'ambiguous',topic_ids:['A','B']});
  assert.equal(pickShell([shell('large',7,9,900)],[0,0,0],[0,0,-1],[100,100],375),null);
});
test('shell decoration remains small and disjoint while selected source shell is retained once',()=>{
  assert.deepEqual(Array.from(boundedShellIndices(new Uint32Array([2,2,4,5,6]),5,3)),[2,4,5]);
  assert.deepEqual(Array.from(boundedShellIndices(new Uint32Array([2,4,5,6]),9,3)),[2,4,5,9]);
  assert.deepEqual(readingPolicy(true,375),{near_previews:0,compact_labels:3,unselected_shells:3});
  assert.deepEqual(readingPolicy(false,1440),{near_previews:1,compact_labels:6,unselected_shells:6});
});
test('one shared occupied-rectangle budget refuses caption overlap with controls or a dock and can choose an edge alternative',()=>{
  const occupancy=new OccupiedRects([[0,0,375,190],[12,590,363,800]]);
  const placed=occupancy.place([[10,600,260,730],[12,210,180,250]]);
  assert.deepEqual(placed,[12,210,180,250]);
  assert.equal(occupancy.place([[20,220,170,260]]),null);
  assert.deepEqual(occupancy.place([[200,270,360,310]]),[200,270,360,310]);
});
