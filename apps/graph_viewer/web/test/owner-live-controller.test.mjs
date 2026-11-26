import test from 'node:test';
import assert from 'node:assert/strict';
import {openOwnerLive} from '../src/owner-live-controller.mjs';

test('only independent frames on current connection mark live; reload and retry are bounded',async()=>{
  let clock=0; const connections=[],states=[],updates=[],timers=[];
  const controller=openOwnerLive({
    allowedOrigin:'https://192.0.2.7',csrf:'x',now:()=>clock,
    onState:s=>states.push(s),onRefresh:x=>updates.push(x),
    schedule:(fn,delay)=>{const item={fn,delay};timers.push(item);return item},
    cancel:t=>{if(t)t.dead=true},
    read:options=>new Promise(resolve=>connections.push({options,resolve})),
  });
  const first=connections[0];
  first.options.onEvent('hello');
  first.options.onEvent('reset');
  clock=10;first.options.onEvent('heartbeat');
  assert.deepEqual(states,['connecting']);
  clock=502;first.options.onEvent('heartbeat');
  assert.deepEqual(states,['connecting','live']);
  assert.deepEqual(updates,['changed']);
  first.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(states.at(-1),'disconnected');
  const retry=timers.find(t=>t.delay===1000&&!t.dead);
  assert.ok(retry);retry.fn();
  assert.equal(connections.length,2);
  first.options.onEvent('heartbeat');
  assert.equal(states.at(-1),'connecting');
  controller.close();
  assert.equal(connections[1].options.signal.aborted,true);
  assert.ok(timers.filter(t=>t.delay===18000).every(t=>t.dead));
});

test('foreground and reconnect catch up in place; a verified new epoch invalidates synchronously',async()=>{
  let clock=0, rev=1, acceptedRev=1, invalidations=0, oldSelection='same', rejectOld;
  const epoch1='a'.repeat(64),epoch2='b'.repeat(64),timers=[],connections=[],calls=[];
  const document=new EventTarget();document.visibilityState='visible';
  const page=new EventTarget();
  const tick=async()=>{for(let i=0;i<6;i++)await Promise.resolve()};
  const take=delay=>{const task=timers.find(t=>t.delay===delay&&!t.dead&&!t.done);assert.ok(task);task.done=true;task.fn()};
  const controller=openOwnerLive({
    allowedOrigin:'https://192.0.2.7',csrf:'x',now:()=>clock,initialEpoch:epoch1,
    visibility:document,page,onState:()=>{},
    onInvalidate(){invalidations++;oldSelection=null;rejectOld?.()},
    onRefresh:async reason=>{
      calls.push(reason);
      if(rev===4)await new Promise(resolve=>rejectOld=resolve);
      acceptedRev=rev;
    },
    schedule:(fn,delay)=>{const item={fn,delay};timers.push(item);return item},
    cancel:t=>{if(t)t.dead=true},
    read:options=>new Promise(resolve=>connections.push({options,resolve})),
  });
  const first=connections[0];
  first.options.onEvent('hello',epoch1);first.options.onEvent('reset',epoch1);
  await tick();
  assert.deepEqual([invalidations,acceptedRev,oldSelection],[0,1,'same']);
  document.visibilityState='hidden';document.dispatchEvent(new Event('visibilitychange'));
  rev=2;
  document.visibilityState='visible';document.dispatchEvent(new Event('visibilitychange'));
  const shown=new Event('pageshow');Object.defineProperty(shown,'persisted',{value:true});page.dispatchEvent(shown);
  take(0);await tick();
  assert.deepEqual(calls,['changed','changed']);
  assert.deepEqual([invalidations,acceptedRev,oldSelection],[0,2,'same']);
  // Reconnect with a missed mutation: first reset is only a soft catch-up.
  first.resolve();await tick();rev=3;take(1000);
  connections[1].options.onEvent('hello',epoch1);connections[1].options.onEvent('reset',epoch1);await tick();
  assert.deepEqual([invalidations,acceptedRev,oldSelection],[0,3,'same']);
  // An in-stream clear invalidates immediately, even behind an older fetch.
  rev=4;connections[1].options.onEvent('changed',epoch1);await tick();
  rev=5;connections[1].options.onEvent('reset',epoch1);
  assert.deepEqual([invalidations,oldSelection],[1,null]);
  await tick();assert.equal(acceptedRev,5);
  connections[1].resolve();await tick();take(2000);
  connections[2].options.onEvent('hello',epoch2);connections[2].options.onEvent('reset',epoch2);await tick();
  assert.equal(invalidations,1,'reconnect after already witnessed clear need not clear twice');
  // A clear that happened during the disconnect is detected by comparing epochs.
  connections[2].resolve();await tick();take(4000);
  connections[3].options.onEvent('hello','c'.repeat(64));connections[3].options.onEvent('reset','c'.repeat(64));await tick();
  assert.equal(invalidations,2);
  controller.close();
  page.dispatchEvent(shown);
  assert.equal(timers.filter(t=>t.delay===0&&!t.dead&&!t.done).length,0);
});

test('first authoritative handshake starts reads before heartbeat and never waits for rendering',async()=>{
  const {firstOwnerSnapshot}=await import('../src/owner-live-controller.mjs');
  const order=[],timers=[],epoch='a'.repeat(64);
  let frame,time=0,finish;
  const boot=firstOwnerSnapshot(id=>{order.push(['graph+queue',id,time]);return new Promise(r=>{finish=r});},{
    schedule:(fn,ms)=>{const item={fn,ms};timers.push(item);return item},
    cancel:item=>{if(item)item.cancelled=true}
  });
  const c=openOwnerLive({allowedOrigin:'https://192.0.2.7',csrf:'x',now:()=>time,
    onBootstrap:id=>boot.accept(id),onState:value=>order.push([value,time]),onRefresh:(reason,id)=>order.push([reason,id,time]),
    read:({onEvent})=>{order.push(['transport opened',time]);frame=onEvent;return new Promise(()=>{});}
  });
  assert.deepEqual(order,[['connecting',0],['transport opened',0]]);
  time=70;frame('hello',epoch);await Promise.resolve();assert.equal(order.length,2);
  time=71;frame('reset',epoch);await Promise.resolve();
  assert.deepEqual(order.at(-1),['graph+queue',epoch,71]);
  assert.equal(timers[0].cancelled,true);
  assert.equal(boot.accept('b'.repeat(64)),false);
  time=580;frame('heartbeat',epoch);assert.deepEqual(order.at(-1),['live',580]);
  time=620;finish();await boot.ready;
  time=700;frame('changed',epoch);assert.deepEqual(order.at(-1),['changed',epoch,700]);
  c.close();
});

test('blocked stream falls back once without accepting late or forged authority',async()=>{
  const {firstOwnerSnapshot}=await import('../src/owner-live-controller.mjs');
  let timeout, reads=[];
  const f=firstOwnerSnapshot(epoch=>reads.push(epoch),{schedule:fn=>{timeout=fn;return 1;},cancel(){}});
  assert.equal(f.accept('not-real'),false);
  timeout();await f.ready;
  assert.deepEqual(reads,[null]);assert.equal(f.accept('a'.repeat(64)),false);
});
