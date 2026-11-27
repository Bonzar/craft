import test from 'node:test';
import assert from 'node:assert/strict';
import {firstOwnerSnapshot,openOwnerLive} from '../src/owner-live-controller.mjs';

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

function startupFixture({holdOffline=false}={}) {
  const epoch='a'.repeat(64),order=[],connections=[],timers=[];
  const document=new EventTarget(),page=new EventTarget();
  document.visibilityState='visible';
  let time=0,finishOffline;
  const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve()};
  const boot=firstOwnerSnapshot(id=>{
    order.push([id===null?'offline snapshot':'graph+queue',id,time]);
    if(id===null&&holdOffline)return new Promise(resolve=>{finishOffline=resolve});
  });
  const control=openOwnerLive({allowedOrigin:'https://example.invalid',csrf:'fixture',visibility:document,page,now:()=>time,
    onBootstrap:id=>boot.accept(id),
    onStartupFailure:()=>{order.push(['offline',time]);boot.fallback();return boot.ready;},
    onState:label=>order.push([label,time]),onRefresh:reason=>order.push([reason,time]),
    schedule:(fn,ms)=>{const timer={fn,at:time+ms};timers.push(timer);return timer;},
    cancel:timer=>{if(timer)timer.dead=true;},
    read:options=>{order.push(['stream requested',time]);return new Promise((resolve,reject)=>connections.push({options,resolve,reject}));}
  });
  async function advance(until){
    for(;;){
      const timer=timers.filter(t=>!t.dead&&t.at<=until).sort((a,b)=>a.at-b.at)[0];
      if(!timer)break;
      time=timer.at;timer.dead=true;timer.fn();await settle();
    }
    time=until;await settle();
  }
  return {control,epoch,order,connections,advance,settle,document,page,boot,finishOffline:()=>finishOffline()};
}

test('slow valid startup remains connecting and does not read either view at 1500 ms',async()=>{
  const f=startupFixture(),send=f.connections[0].options.onEvent;
  await f.advance(1500);
  assert.deepEqual(f.order,[['connecting',0],['stream requested',0]]);
  f.document.visibilityState='hidden';f.document.dispatchEvent(new Event('visibilitychange'));
  f.document.visibilityState='visible';f.document.dispatchEvent(new Event('visibilitychange'));
  const shown=new Event('pageshow');Object.defineProperty(shown,'persisted',{value:true});f.page.dispatchEvent(shown);
  await f.advance(1999);
  assert.equal(f.order.length,2,'foreground startup must not fetch the graph or queue');
  send('hello',f.epoch);send('heartbeat',f.epoch);await f.advance(2000);
  assert.equal(f.order.length,2,'no live publication or snapshot before reset');
  send('reset',f.epoch);await f.boot.ready;
  assert.deepEqual(f.order.slice(-2),[['registered',2000],['graph+queue',f.epoch,2000]]);
  await f.advance(10000);
  assert.equal(f.order.filter(r=>r[0].includes('snapshot')||r[0]==='graph+queue').length,1);
  assert.equal(f.connections.length,1);
  f.control.close();
});

test('transient disconnect waits for retry, final deadline explicitly settles offline once',async()=>{
  const f=startupFixture(),first=f.connections[0];
  first.reject(new Error('temporary network failure'));await f.settle();
  await f.advance(1500);
  assert.equal(f.connections.length,2);
  assert.equal(f.order.some(r=>r[0]==='disconnected'||r[0].includes('snapshot')),false);
  const retry=f.connections[1].options,send=retry.onEvent;
  await f.advance(10000);await f.boot.ready;
  assert.deepEqual(f.order.slice(-2),[['offline',10000],['offline snapshot',null,10000]]);
  assert.equal(retry.signal.aborted,true);
  send('hello',f.epoch);send('reset',f.epoch);send('heartbeat',f.epoch);
  await f.advance(30000);
  assert.equal(f.order.some(r=>r[0]==='registered'||r[0]==='live'),false);
  assert.equal(f.order.filter(r=>r[0]==='offline snapshot').length,1);
});

test('definitive stream protocol rejection marks explicit offline before one snapshot',async()=>{
  const f=startupFixture();
  await f.advance(75);
  f.connections[0].reject(Object.assign(new Error('invalid SSE'),{name:'OwnerStreamTerminalError'}));
  await f.boot.ready;
  assert.deepEqual(f.order.slice(-2),[['offline',75],['offline snapshot',null,75]]);
  assert.equal(f.connections[0].options.signal.aborted,true);
  await f.settle();await f.advance(30000);
  assert.equal(f.connections.length,2,'protocol failure can recover via a new attempt only');
  f.control.close();
});

test('offline snapshot settles before new connection; fresh hello/reset/heartbeat only catches up in place',async()=>{
  const f=startupFixture({holdOffline:true}),old=f.connections[0].options;
  await f.advance(10000);
  assert.deepEqual(f.order.slice(-2),[['offline',10000],['offline snapshot',null,10000]]);
  assert.equal(old.signal.aborted,true);
  await f.advance(13000);assert.equal(f.connections.length,1);
  old.onEvent('hello',f.epoch);old.onEvent('reset',f.epoch);old.onEvent('heartbeat',f.epoch);
  f.finishOffline();await f.boot.ready;await f.settle();await f.advance(14000);
  assert.equal(f.connections.length,2);
  assert.equal(f.order.filter(r=>r[0]==='connecting').length,1,'offline snapshot must not display Connecting from retry');
  const recent=f.connections[1].options;assert.notEqual(recent.signal,old.signal);
  recent.onEvent('hello',f.epoch);await f.advance(14501);recent.onEvent('heartbeat',f.epoch);
  assert.equal(f.order.some(r=>r[0]==='live'),false,'heartbeat cannot skip fresh reset');
  recent.onEvent('reset',f.epoch);await f.advance(15001);recent.onEvent('heartbeat',f.epoch);await f.settle();
  assert.deepEqual(f.order.slice(-2),[['live',15001],['changed',15001]]);
  assert.equal(f.order.filter(r=>r[0]==='offline snapshot'||r[0]==='graph+queue').length,1);
  f.control.close();
});

test('permanent authorization denial stays offline and does not retry blindly',async()=>{
  const f=startupFixture();
  f.connections[0].reject(Object.assign(new Error('fixture authorization denial'),
    {name:'OwnerStreamTerminalError',authorizationDenied:true}));
  await f.boot.ready;await f.advance(30000);
  assert.equal(f.connections.length,1);
  assert.equal(f.connections[0].options.signal.aborted,true);
  assert.deepEqual(f.order.slice(-2),[['offline',0],['offline snapshot',null,0]]);
});

test('closing at logout while offline snapshot is settling cancels any recovery attempt',async()=>{
  const f=startupFixture({holdOffline:true});
  await f.advance(10000);f.control.close();f.finishOffline();await f.boot.ready;
  await f.advance(30000);
  assert.equal(f.connections.length,1);
  assert.equal(f.order.filter(r=>r[0]==='offline snapshot').length,1);
});

test('401/403 on a recovery attempt stops that path without repaint or another snapshot',async()=>{
  const f=startupFixture();
  await f.advance(10000);await f.boot.ready;await f.settle();await f.advance(11000);
  assert.equal(f.connections.length,2);
  f.connections[1].reject(Object.assign(new Error('fixture auth denial'),{name:'OwnerStreamTerminalError',authorizationDenied:true}));
  await f.settle();await f.advance(30000);
  assert.equal(f.connections.length,2);
  assert.equal(f.order.filter(r=>r[0]==='offline snapshot').length,1);
  assert.equal(f.order.some(r=>r[0]==='live'||r[0]==='disconnected'),false);
});
