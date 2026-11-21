import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeProducerInstant, normalizeCaptureMessage, prepareCaptureReceipt, compareCaptureReplay} from '../capture_message.mjs';

// Captured from Python MemoryService over a synthetic in-memory database.
const simple={session_id:'fixture',message_id:'message-1',actor_id:'user',role:'user',text:'Ё🙂\n"box".',
  position:2,source:'3rd-party'};
const clocked={...simple,message_id:'message-2',actor_id:'speaker',text:'next',position:null,
  timezone:'Europe/Moscow',source_message_at:'2025-03-01T00:05:09.123456+03:00'};
const simpleId='event-0673110088c38eb952b978c35a6b9b8ad85c58c3e1bc54dc064b62cc2ac67caf';
const simpleReceipt='sha256:e388134b9dc3fc4872f18e1f67265b24ba95d5e396ef5b69b848185314811636';
const clockId='event-a2586c5ca7d4d8c0ad615f1a0101a658a4737e4d7c4af7bd9c5c181df0b0266a';
const clockReceipt='source-clock-v1:{"base": "sha256:23c58925b8904f5ec89d9cc00545cc9bc7572aaab282f4d280f8b29c1727175f", "clock": "sha256:7796724e0a34b6e5db324e3bae70b80ae9a350110b85fc55e5b922ff1515cf6c"}';
const normalize = async arg => normalizeCaptureMessage(arg);
function existing(c,r){return {event_id:r.event_id,payload:r.receipt,metadata:r.raw_metadata,position:c.position};}
test('event ID, base receipt and normalized six-digit clock receipt equal Python',async()=>{
  const plain=await normalize(simple), timed=await normalize(clocked);
  const a=await prepareCaptureReceipt(plain), b=await prepareCaptureReceipt(timed);
  assert.deepEqual(a,{receipt:simpleReceipt,event_id:simpleId,raw_metadata:{role:'user',message_id:'message-1'}});
  assert.equal(b.receipt,clockReceipt);
  assert.equal(b.event_id,clockId);
  assert.equal(b.raw_metadata.source_message_at,'2025-02-28T21:05:09.123456+00:00');
});
test('replay omitted order or clock, reject immutable mismatches and upgrade legacy digest',async()=>{
  const c=await normalize(simple);
  const old=existing(c,await prepareCaptureReceipt(c));
  assert.deepEqual(await compareCaptureReplay(await normalize({...simple,position:null}),old),
    {event_id:simpleId,legacy_rewrite:null});
  await assert.rejects(compareCaptureReplay(await normalize({...simple,position:9}),old),/ordinal/);
  await assert.rejects(compareCaptureReplay(await normalize({...simple,source:'different'}),old),/provenance/);
  const legacy='["fixture", "message-1", "user", "Ё🙂\\n\\"box\\".", "3rd-party", "default", "user", 2]';
  assert.deepEqual(await compareCaptureReplay(c,{...old,payload:legacy}),{event_id:simpleId,legacy_rewrite:simpleReceipt});
  const d=await normalize(clocked);
  const timed=existing(d,await prepareCaptureReceipt(d));
  assert.equal((await compareCaptureReplay(await normalize({...clocked,timezone:null,source_message_at:null}),timed)).event_id,clockId);
  assert.equal((await compareCaptureReplay(await normalize({...clocked,source_message_at:'2025-02-28T21:05:09.123456Z'}),timed)).event_id,clockId);
  await assert.rejects(compareCaptureReplay(await normalize({...clocked,timezone:'UTC'}),timed),/timezone/);
  await assert.rejects(compareCaptureReplay(await normalize({...clocked,source_message_at:'2025-02-28T21:05:09.123457Z'}),timed),/clock/);
  await assert.rejects(compareCaptureReplay(d,{...timed,metadata:{...timed.metadata,timezone:'UTC'}}),/timezone/);
  await assert.rejects(compareCaptureReplay(await normalize({...clocked,timezone:null}),{...timed,metadata:{...timed.metadata,timezone:'UTC'}}),/receipt contradicts/);
});
test('delivery does not bind identity; artifact receipts require authoritative exact references',async()=>{
  const sha='a'.repeat(64), id='artifact-'+sha;
  const ref={artifact_id:id,sha256:sha,size_bytes:123,mime_type:'image/png',not_interpreted:true};
  const c=await normalize({...simple,artifact_ids:[id,id],delivery:{stream_id:'s',epoch:1,seq:9}});
  assert.deepEqual(c.artifact_ids,[id]);
  assert.equal(c.event_id,simpleId);
  await assert.rejects(prepareCaptureReceipt(c),/descriptors/);
  const receipt=await prepareCaptureReceipt(c,{artifactRefs:[ref]});
  assert.equal(receipt.receipt.startsWith('source-clock-v1:'),true);
  assert.deepEqual(receipt.raw_metadata.artifact_refs,[ref]);
  const old=existing(c,receipt);
  await assert.rejects(compareCaptureReplay(await normalize(simple),old),/artifact references/);
  await assert.rejects(compareCaptureReplay(c,old,{artifactRefs:[{...ref,size_bytes:124}]}),/artifact references/);
});
test('malformed timestamp, producer proof, role and artifact IDs fail closed',async()=>{
  for(const date of ['2025-02-29T00:00:00Z','2024-02-29T24:00:00Z','2024-02-29T00:00:00+24:00','2024-02-29T00:00:00Z\n'])
    assert.throws(()=>normalizeProducerInstant(date));
  assert.equal(normalizeProducerInstant('0001-01-01T01:02:03.4+01:00'),'0001-01-01T00:02:03.400000+00:00');
  await assert.rejects(normalize({...clocked,source_message_clock_basis:{kind:'native-record'}}),/basis/);
  await assert.rejects(normalize({...simple,actor_id:'developer',role:'assistant'}),/actor/);
  await assert.rejects(normalize({...simple,position:true}),/ordinal/);
  await assert.rejects(normalize({...simple,artifact_ids:['artifact-'+'a'.repeat(64)+'\n']}),/artifact/);
});
