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
  assert.deepEqual(updates,['reset']);
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
