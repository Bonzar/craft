import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {ownerGraphReconciler,readOwnerGraphWire} from '../src/owner-graph-wire.mjs';
const e='e'.repeat(64),nextEpoch='f'.repeat(64),a='a'.repeat(32),b='b'.repeat(32),c='c'.repeat(32);
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const names=['nodes.bin','edges.bin','topics.json','identities.json'];
async function baseline(recv,{epoch=e,rev=a,cursor='12',corrupt=false}={}){
  const data=[Buffer.alloc(16),Buffer.alloc(0),Buffer.from(JSON.stringify({revision:rev})),Buffer.from('["stable"]')];
  const m={owner_live_epoch:epoch,revision:rev,live_cursor:cursor,complete_graph:true,
    nodes:{byte_length:16},edges:{byte_length:0},counts:{nodes:1}};
  await recv.accept('snapshot-start',{epoch,revision:rev,cursor,manifest:m});
  const segments={};
  for(let i=0;i<names.length;i++){
    const [name,raw]=[names[i],data[i]];
    if(raw.length)await recv.accept('snapshot-chunk',{name,revision:rev,offset:0,base64:raw.toString('base64')});
    segments[name]={bytes:raw.length,sha256:corrupt&&i===3?'0'.repeat(64):sha(raw)};
  }
  assert.notEqual(recv.checkpoint()?.revision,rev,'unaccepted staged snapshot must not become checkpoint');
  await recv.accept('snapshot-end',{epoch,revision:rev,cursor,segments});
}
const greet=(r,epoch=e)=>r.accept('hello',{epoch,protocol:'graph-v1'});
test('cold stream accepts only one complete checksummed baseline, then ordered deltas including removals',async()=>{
  const work=[];
  const r=ownerGraphReconciler({sha256:sha,onSnapshot:v=>work.push(['baseline',v.ids,v.cursor]),
    onDelta:async v=>work.push(['delta',v.remove_node_ordinals])});
  await greet(r);
  await baseline(r);
  await r.accept('delta',{epoch:e,base_revision:a,revision:b,cursor:'17',remove_node_ordinals:[0]});
  await r.accept('caught-up',{epoch:e,revision:b,cursor:'19'});
  assert.deepEqual(work,[['baseline',['stable'],'12'],['delta',[0]]]);
  assert.deepEqual(r.checkpoint(),{epoch:e,revision:b,cursor:'19'});
  await assert.rejects(r.accept('delta',{epoch:e,base_revision:a,revision:c,cursor:'20'}),/contiguous/);
  await assert.rejects(r.accept('delta',{epoch:e,base_revision:b,revision:c,cursor:'18'}),/contiguous/);
  assert.equal(r.checkpoint().cursor,'19');
});
test('bad chunks and checksum never emit partial or new cached records',async()=>{
  const r=ownerGraphReconciler({sha256:sha,onSnapshot:()=>assert.fail('partial snapshot'),onDelta:()=>{}});
  await greet(r);await assert.rejects(baseline(r,{corrupt:true}),/checksum/);
  assert.equal(r.checkpoint(),null);
  await assert.rejects(r.accept('delta',{epoch:e,base_revision:a,revision:b,cursor:'13'}),/interleaved/);
});
test('warm revision replays without baseline; changed epoch clears before restart on same stream',async()=>{
  const work=[];
  const r=ownerGraphReconciler({checkpoint:{epoch:e,revision:a,cursor:'12'},sha256:sha,
    onSnapshot:v=>work.push(v.revision),onDelta:v=>work.push(v.revision),onReset:v=>work.push(v.epoch)});
  await greet(r);await r.accept('delta',{epoch:e,base_revision:a,revision:b,cursor:'14'});
  await r.accept('reset',{epoch:nextEpoch,reason:'epoch_changed'});
  assert.equal(r.checkpoint(),null);
  await assert.rejects(r.accept('delta',{epoch:e,base_revision:b,revision:c,cursor:'15'}),/epoch/);
  await baseline(r,{epoch:nextEpoch,rev:c,cursor:'1'});
  assert.deepEqual(work,[b,nextEpoch,c]);
  assert.deepEqual(r.checkpoint(),{epoch:nextEpoch,revision:c,cursor:'1'});
});
test('a rejected renderer apply cannot move a delta checkpoint',async()=>{
  const r=ownerGraphReconciler({checkpoint:{epoch:e,revision:a,cursor:'12'},sha256:sha,
    onSnapshot:()=>{},onDelta:async()=>{throw new Error('stale view')}});
  await greet(r);
  await assert.rejects(r.accept('delta',{epoch:e,base_revision:a,revision:b,cursor:'14'}),/stale/);
  assert.deepEqual(r.checkpoint(),{epoch:e,revision:a,cursor:'12'});
});
test('transports exactly one same-origin request and dispatches verified frame names in order',async()=>{
  let requests=0;
  const events=[];
  const stream=new Response('event: hello\ndata: {"protocol":"graph-v1","epoch":"'+e+'"}\n\nevent: heartbeat\ndata: {}\n\n',
    {headers:{'Content-Type':'text/event-stream'}});
  await readOwnerGraphWire({selector:{mode:'current',history:false},checkpoint:{epoch:e,revision:a,cursor:'12'},
    fetcher(path,options){requests++;const u=new URL(path,'https://owner.example');assert.equal(u.pathname,'/api/v1/events');
      assert.equal(u.searchParams.get('wire'),'graph-v1');assert.equal(u.searchParams.get('after'),'12');
      assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');return stream;},
    onEvent:(name)=>events.push(name)});
  assert.equal(requests,1);assert.deepEqual(events,['hello','heartbeat']);
});
