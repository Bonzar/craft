import test from 'node:test';
import assert from 'node:assert/strict';
import {openOwnerGraphSession} from '../src/owner-graph-session.mjs';
const e='a'.repeat(64),r='b'.repeat(32),next='c'.repeat(32);
const warm={epoch:e,revision:r,cursor:'5'};
const settle=async()=>{for(let i=0;i<24;i++)await Promise.resolve()};
test('warm view paints with the single stream pending; delta waits for renderer and persistence is exact',async()=>{
  const order=[],writes=[];
  let paint,releaseDelta,send;
  const c=openOwnerGraphSession({selector:{mode:'current'},scope:'scope',verifiedEpoch:e,
    cache:{take:async()=>warm,save:async(...args)=>writes.push(args)},
    onCached:async value=>{order.push('cached paint');await new Promise(resolve=>paint=resolve);return value;},
    onReset:async()=>{},onSnapshot:async()=>assert.fail('unexpected baseline'),
    onDelta:async value=>{order.push('render delta');await new Promise(resolve=>releaseDelta=resolve);return value;},
    read:async ({checkpoint,onEvent,signal})=>{
      assert.equal(checkpoint,warm);order.push('start stream');send=onEvent;
      await new Promise(resolve=>signal.addEventListener('abort',resolve));
    },
    onState:state=>order.push(state)});
  await settle();
  assert.deepEqual(order,['snapshot-connecting','start stream','cached paint']);
  const h=send('hello',{epoch:e,protocol:'graph-v1'});
  await settle();assert.equal(writes.length,0);
  paint();await c.initial;await h;
  const delta=send('delta',{epoch:e,revision:next,base_revision:r,cursor:'6'});
  await settle();assert.equal(writes.length,0);assert.equal(order.at(-1),'render delta');
  releaseDelta();await delta;assert.equal(writes[0][1].cursor,'6');
  await send('caught-up',{epoch:e,revision:next,cursor:'6'});
  assert.equal(order.at(-1),'live');
  c.close();
});
test('broken local storage takes one cold stream and permanent auth denial never retries',async()=>{
  let req=0,clear=0;
  const states=[],c=openOwnerGraphSession({selector:{mode:'current'},scope:null,
    cache:{take:async()=>null,clear:async()=>{clear++;}},onState:x=>states.push(x),
    read:async ({checkpoint})=>{req++;assert.equal(checkpoint,null);throw Object.assign(new Error('denied'),{status:401});},
    schedule:()=>assert.fail('must not retry auth denial')});
  await assert.rejects(c.initial,/denied/);
  await settle();
  assert.deepEqual([req,clear,states],[1,1,['connecting','unauthorized']]);
});

test('missing or different authenticated epoch never paints or sends saved checkpoint',async()=>{
  for(const verifiedEpoch of [null,'e'.repeat(64)]){
    let reads=0,clears=0,requests=0;
    const c=openOwnerGraphSession({selector:{mode:'current'},scope:'scope',verifiedEpoch,
      cache:{take:async()=>{reads++;return warm;},clear:async()=>{clears++;}},
      onCached:async()=>assert.fail('stale graph painted'),
      read:async ({checkpoint,signal})=>{
        requests++;assert.equal(checkpoint,null);
        await new Promise(resolve=>signal.addEventListener('abort',resolve));
      }});
    await settle();
    assert.deepEqual([reads,clears,requests],verifiedEpoch?[1,1,1]:[0,0,1]);
    c.close();
  }
});
test('a newer authenticated hello hides fast warm paint before a blocked persistent purge',async()=>{
  const order=[];let send,unblock;
  const changed='e'.repeat(64);
  const c=openOwnerGraphSession({selector:{mode:'current'},scope:'scope',verifiedEpoch:e,
    cache:{take:async()=>warm,clear:async()=>{order.push('purge');await new Promise(resolve=>unblock=resolve);}},
    onCached:async value=>{order.push('warm shown');return value;},
    onReset:async value=>{assert.equal(value.epoch,changed);order.push('warm hidden');},
    onState:label=>order.push(label),
    read:async ({checkpoint,onEvent,signal})=>{
      assert.equal(checkpoint,warm);send=onEvent;
      await new Promise(resolve=>signal.addEventListener('abort',resolve));
    }});
  await c.initial;
  assert.deepEqual(order,['snapshot-connecting','warm shown']);
  const reset=send('hello',{protocol:'graph-v1',epoch:changed});
  await settle();
  assert.deepEqual(order,['snapshot-connecting','warm shown','warm hidden','purge']);
  unblock();await reset;c.close();
  assert.equal(order.includes('live'),false);
});
