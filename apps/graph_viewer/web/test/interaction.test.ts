import { test } from 'node:test';
import assert from 'node:assert/strict';
import { interactionIsCurrent, sourceResponseIsCurrent, pinSnapshotCallback, pinRevisionCallback, memberVisibilityLabel, groupIncidentRepresentatives } from '../src/interaction.ts';
test('a pointer press from an old source revision or LOD generation cannot select a new display target',()=>{
  const down={revision:'source-r1',generation:4};
  assert.equal(interactionIsCurrent(down,'source-r1',4),true);
  assert.equal(interactionIsCurrent(down,'source-r1',5),false);
  assert.equal(interactionIsCurrent(down,'source-r2',4),false);
});
test('source-topic member and internal-edge callbacks cannot reinterpret saved ordinals in a later revision',()=>{
  let revision='r1';const opened:number[]=[];let stale=0;
  const action=pinRevisionCallback('r1',()=>revision,ordinal=>opened.push(ordinal),()=>stale++);
  action(7);revision='r2';action(7);
  assert.deepEqual(opened,[7]);
  assert.equal(stale,1);
});
test('source-topic member list explicitly distinguishes status-hidden members from eligible members',()=>{
  assert.match(memberVisibilityLabel(3,false),/скрыто в срезе/);
  assert.match(memberVisibilityLabel(3,true),/в срезе/);
  assert.doesNotMatch(memberVisibilityLabel(3,true),/скрыто/);
});
test('selected adjacency preserves multiplicity when several real edges meet the same collapsed representative',()=>{
  const result=groupIncidentRepresentatives(new Uint32Array([0,1,0,2,0,3]),new Int32Array([0,4,4,3]));
  assert.deepEqual(Array.from(result.endpoints),[0,4,0,3]);
  assert.deepEqual(Array.from(result.multiplicities),[2,1]);
  assert.equal(result.edgeCount,3);
});

test('a saved membership action cannot open a matching topic ID in another provider or revision',()=>{
  let current={provider_id:'source-A',revision:'r1'};const opened:string[]=[];let stale=0;
  const action=pinSnapshotCallback(current,()=>current,id=>opened.push(id),()=>stale++);
  action('topic-A');current={provider_id:'source-B',revision:'r1'};action('topic-A');
  current={provider_id:'source-A',revision:'r2'};action('topic-A');
  assert.deepEqual(opened,['topic-A']);assert.equal(stale,2);
});

test('a delayed old card response cannot write a fresh scene or reopen after the reader was closed',async()=>{
  const bound={provider_id:'source-A',revision:'r1'};let current=bound;let aborted=false;
  let release:(id:string)=>void=()=>{};const response=new Promise<string>(resolve=>{release=resolve;});const writes:string[]=[];
  const opening=response.then(id=>{if(sourceResponseIsCurrent(bound,current,aborted))writes.push(id);});
  current={...bound,revision:'r2'};release('old-card');await opening;assert.deepEqual(writes,[]);
  current=bound;aborted=true;assert.equal(sourceResponseIsCurrent(bound,current,aborted),false);
});
