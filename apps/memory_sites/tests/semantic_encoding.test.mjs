import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {encodeCanonicalThought,pinCanonicalRawBasis,pinCanonicalThoughtBasis} from '../semantic_encoding.mjs';

const serializedByPython = spawnSync('python',['-c',`
from dataclasses import asdict
import json
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.thought_store import ThoughtStore, Thought, _hash, _json
clock='2027-01-01T00:00:00.000000+00:00'
with MemoryStore(':memory:') as store:
 q=CaptureQueue(store)
 t=ThoughtStore(store)
 event=q.capture_event('session','turn','The 🍀 fixture.',source='test',message_id='turn',
   metadata={'role':'user','message_id':'turn','nested':{'𐀀':'astral','\ue000':'BMP'}},recorded_at=clock)
 with store.transaction(write=True) as db:
  basis=t._pin_basis(db,{'event_id':event.id,'span':{'start':4,'end':5},'meaning':'🍀'})
  first=Thought('fixture-oracle',1,'Synthetic 🍀 thought.',None,{'kind':'fixture'},[basis],{},clock)
  t._insert(db,first)
  head=Thought(first.id,2,'Updated 🍀 thought.','What changed?',first.origin,first.bases,{'integer':37},clock)
  t._insert(db,head)
  pinned=t._pin_basis(db,{'thought_id':first.id,'version':1,'observed_head_version':2,'span':{'start':10,'end':11}})
  output=dict(event=asdict(event),basis=basis,first=asdict(first),head=asdict(head),pinned=pinned,
    encoded=_json(asdict(first)),content_hash=_hash(asdict(first)))
  print(json.dumps(output,ensure_ascii=False))
`],{cwd:fileURLToPath(new URL('../../../',import.meta.url)),encoding:'utf8'});
assert.equal(serializedByPython.status,0,serializedByPython.stderr);
const fixture=JSON.parse(serializedByPython.stdout);

test('raw basis uses canonical escaped SQLite metadata and Unicode evidence offsets',async()=>{
  const {event,basis}=fixture;
  const input={event_id:event.id,span:{start:4,end:5},meaning:'🍀'};
  assert.deepEqual(await pinCanonicalRawBasis(input,event),basis);
  assert.deepEqual(await pinCanonicalRawBasis(basis,event),basis);
  await assert.rejects(pinCanonicalRawBasis(basis,{...event,prompt:'The 🍁 fixture.'}),/hash/);
  await assert.rejects(pinCanonicalRawBasis({...input,span:{start:4,end:99}},event),/span/);
  await assert.rejects(pinCanonicalRawBasis(input,{...event,source:'codex://fixture',metadata:{channel:'analysis'}}),/raw evidence/);
  await assert.rejects(pinCanonicalRawBasis({...input,event_id:'foreign'},event),/raw evidence/);
});

test('historical pin is bound to separately observed active head',async()=>{
  const {first,head,pinned}=fixture;
  const input={thought_id:first.id,version:1,observed_head_version:2,span:{start:10,end:11}};
  assert.deepEqual(await pinCanonicalThoughtBasis(input,{selected:first,currentHead:head}),pinned);
  assert.equal('observed_head_version' in pinned,false);
  await assert.rejects(pinCanonicalThoughtBasis({...input,observed_head_version:1},{selected:first,currentHead:head}),/head/);
  await assert.rejects(pinCanonicalThoughtBasis(pinned,{selected:first,currentHead:head}),/head/);
  await assert.rejects(pinCanonicalThoughtBasis(input,{selected:first,currentHead:{...head,id:'foreign'}}),/evidence/);
});

test('logical Thought compact sorted bytes and SHA agree with original Python',async()=>{
  assert.deepEqual(await encodeCanonicalThought(fixture.first),{
    payload:fixture.encoded,content_hash:fixture.content_hash,
  });
  await assert.rejects(encodeCanonicalThought({...fixture.first,metadata:{numeric:1.25}}),/unsupported number/);
  await assert.rejects(encodeCanonicalThought({...fixture.first,bases:[{event_id:fixture.event.id}]}),/pinned/);
  await assert.rejects(encodeCanonicalThought({...fixture.first,text:'x'.repeat(256*1024)}),/size/);
});
