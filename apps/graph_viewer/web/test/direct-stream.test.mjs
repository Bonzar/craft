import test from 'node:test';
import assert from 'node:assert/strict';
import {readDirectStream} from '../src/direct-stream.mjs';

const target='https://192.0.2.7/__mvt/api/v1/events-direct';
const makeResponse=parts=>new Response(new ReadableStream({start(controller){
  for(const p of parts) controller.enqueue(new TextEncoder().encode(p));
  controller.close();
}}),{headers:{'Content-Type':'text/event-stream'}});

test('only same-origin owner CSRF and one scoped header, incremental events',async()=>{
  const calls=[],seen=[],epochs=[],expected='c'.repeat(64);
  const fetcher=async(path,request)=>{
    calls.push([path,request]);
    return calls.length===1?Response.json({ticket:'a'.repeat(43),stream_url:target,epoch:expected}):
      makeResponse(['event: hel','lo\ndata: {}\n\nevent: res','et\ndata: {}\n\n',
                    'event: heartbeat\ndata: {}\r\n\r\nevent: changed\ndata: {}\n\n']);
  };
  await readDirectStream({fetcher,csrf:'owner-csrf',allowedOrigin:'https://192.0.2.7',onEvent:(e,epoch)=>{seen.push(e);epochs.push(epoch)}});
  assert.equal(calls[0][0],'/api/v1/events-ticket');
  assert.equal(calls[0][1].credentials,'same-origin');
  assert.deepEqual(calls[0][1].headers,{'X-CSRF-Token':'owner-csrf'});
  assert.equal(calls[1][0],target);
  assert.equal(calls[1][1].credentials,'omit');
  assert.deepEqual(calls[1][1].headers,{Authorization:'Bearer '+'a'.repeat(43),Accept:'text/event-stream'});
  assert.deepEqual(seen,['hello','reset','heartbeat','changed']);
  assert.deepEqual(epochs,[expected,expected,expected,expected]);
});

test('refuses unexpected origin before transferring ticket and rejects private payload',async()=>{
  let calls=0;
  await assert.rejects(readDirectStream({fetcher:async()=>{calls++;return Response.json({
    ticket:'a'.repeat(43),stream_url:'https://other.invalid/__mvt/api/v1/events-direct'})},
    csrf:'x',allowedOrigin:'https://192.0.2.7',onEvent:()=>assert.fail()}),/Unexpected stream target/);
  assert.equal(calls,1);
  let sent=0;
  await assert.rejects(readDirectStream({fetcher:async()=>!sent++?
    Response.json({ticket:'a'.repeat(43),stream_url:target}):makeResponse(['event: hello\ndata: {"text":"private"}\n\n']),
    csrf:'x',allowedOrigin:'https://192.0.2.7',onEvent:()=>assert.fail()}),/Invalid stream event/);
});

test('diagnostic distinguishes visibly separated delivery from coalescing',async()=>{
  const {proveDirectStream}=await import('../src/direct-stream-proof.mjs');
  const hello='event: hello\ndata: {}\n\nevent: reset\ndata: {}\n\n';
  const beat='event: heartbeat\ndata: {}\n\n';
  async function proof(parts,times){
    let requests=0;
    const seen=[];
    const result=await proveDirectStream({
      csrf:'x',allowedOrigin:'https://192.0.2.7',now:()=>times.shift(),
      fetcher:async()=>!requests++?Response.json({ticket:'a'.repeat(43),stream_url:target}):makeResponse(parts),
      update:value=>seen.push(value),
    });
    assert.equal(requests,2);
    assert.deepEqual(seen.at(-1),result);
    return result;
  }
  assert.deepEqual(await proof([hello,beat],[0,225,226,15010]),{phase:'proven',helloMs:225,heartbeatMs:15010});
  assert.deepEqual(await proof([hello+beat],[0,225,226,227]),{phase:'not_proven'});
});

test('standalone proof shell has only exact origin and content hash in its policy',async()=>{
  const {renderProof}=await import('../src/build-direct-stream-proof.mjs');
  const {createHash}=await import('node:crypto');
  const {html,policy}=await renderProof('https://192.0.2.7');
  const script=html.match(/<script>([\s\S]*)<\/script>/)[1];
  assert.ok(policy.includes("'sha256-"+createHash('sha256').update(script).digest('base64')+"'"));
  assert.ok(policy.includes("connect-src 'self' https://192.0.2.7;"));
  assert.doesNotMatch(html,/https?:\/\/[^"' ]*(analytics|google|cdn)/);
  assert.ok(html.includes('Проверить поток'));
  await assert.rejects(renderProof('https://192.0.2.7/help'));
});
