import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
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

test('first hello watchdog aborts one stalled request; warm position and persisted checkpoint stay intact',async()=>{
  const positions=new Float32Array([1,2,3]), phases=[],states=[];
  let timeout,requests=0,clears=0,retries=0;
  const c=openOwnerGraphSession({selector:{mode:'current'},scope:'scope',verifiedEpoch:e,
    cache:{take:async()=>({...warm,positions}),clear:async()=>{clears++;}},
    onCached:async value=>{assert.equal(value.positions,positions);return value;},
    onDiagnostic:(...args)=>phases.push(args),onState:s=>states.push(s),
    read:async ({signal,onHeaders})=>{requests++;onHeaders();
      await new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new Error('PRIVATE response')), {once:true}));
    },
    startDeadline:(run,ms)=>{timeout=run;assert.equal(ms,8000);return 1;},clearDeadline:()=>{},
    schedule:()=>++retries});
  await c.initial;
  timeout();await settle();
  assert.deepEqual(phases,[['request',false],['headers',false],['hello-timeout',true]]);
  assert.deepEqual(states,['snapshot-connecting','snapshot-offline']);
  assert.deepEqual([requests,retries,clears,[...positions]],[1,1,0,[1,2,3]]);
  c.close();
});
test('accepted wire baseline exposes renderer failure distinctly, never renderer text',async()=>{
  const phases=[],states=[];
  let watchdogCancelled=0, render=0;
  const c=openOwnerGraphSession({selector:{mode:'current'},
    onSnapshot:async()=>{render++;throw new Error('PRIVATE WebGL diagnostic body');},
    onDiagnostic:(...args)=>phases.push(args),onState:s=>states.push(s),
    startDeadline:()=>1,clearDeadline:()=>{watchdogCancelled++;},
    schedule:()=>1,
    read:async ({onEvent,onHeaders})=>{
      onHeaders();await onEvent('hello',{protocol:'graph-v1',epoch:e});
      const stamp={epoch:e,revision:r,cursor:'5'};
      await onEvent('snapshot-start',{...stamp,manifest:{owner_live_epoch:e,revision:r,live_cursor:'5',
        complete_graph:true,nodes:{byte_length:16},edges:{byte_length:0},counts:{nodes:1}}});
      const parts={'nodes.bin':Buffer.alloc(16),'edges.bin':Buffer.alloc(0),
        'topics.json':Buffer.from(JSON.stringify({revision:r})),'identities.json':Buffer.from('["test"]')};
      for(const [name,bytes] of Object.entries(parts))
        if(bytes.length)await onEvent('snapshot-chunk',{name,revision:r,offset:0,base64:bytes.toString('base64')});
      const segments=Object.fromEntries(Object.entries(parts).map(([name,bytes])=>
        [name,{bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}]));
      await onEvent('snapshot-end',{...stamp,segments});
    }});
  for(let i=0;i<200&&!states.includes('offline');i++)await new Promise(resolve=>setTimeout(resolve,1));
  assert.deepEqual(phases,[['request',false],['headers',false],['hello',false],['baseline-start',false],
    ['baseline-accepted',false],['baseline-render-failed',true]]);
  assert.equal(render,1);assert.ok(watchdogCancelled);assert.deepEqual(states,['connecting','offline']);
  c.close();await c.initial;
});


test('per-attempt transport timing keeps prior timeout distinct from next hello',async()=>{
  let clock=100, deadline,retry,reads=0;
  const trace=[];
  const c=openOwnerGraphSession({selector:{mode:'current'},now:()=>clock,
    onAttempt:timing=>trace.push(timing),
    startDeadline:fn=>{deadline=fn;return 1;},clearDeadline(){},
    schedule:fn=>{retry=fn;return 1;},cancel(){},
    read:async ({signal,onHeaders,onFirstFrame,onEvent})=>{
      if(++reads===1){
        clock=111;onHeaders();
        await new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(Error('synthetic private body')),{once:true}));
      }else{
        clock=8205;onHeaders();clock=8217;onFirstFrame();
        clock=8220;await onEvent('hello',{protocol:'graph-v1',epoch:e});
        await new Promise(resolve=>signal.addEventListener('abort',resolve,{once:true}));
      }
    }});
  await settle();clock=8100;deadline();await settle();
  const first=trace.at(-1);
  assert.deepEqual(first,{attempt:1,headers_ms:11,first_frame_ms:null,hello_ms:null,finished_ms:8000,outcome:'hello-timeout'});
  clock=8200;retry();await settle();
  assert.deepEqual(trace.at(-1),{attempt:2,headers_ms:5,first_frame_ms:17,hello_ms:20,finished_ms:null,outcome:null});
  assert.equal(first.finished_ms,8000);
  assert.equal(JSON.stringify(trace).includes(e),false);
  assert.equal(JSON.stringify(trace).includes('private body'),false);
  c.close();
});

test('rejected warm delta recovers with one cold SSE while retaining the same accepted view',async()=>{
  const reads=[],states=[],writes=[]; let retry,failures=0,hidden=0;
  const accepted={...warm,positions:new Float32Array([1,2,3])};
  const c=openOwnerGraphSession({selector:{mode:'current'},scope:'scope',verifiedEpoch:e,
    cache:{take:async()=>accepted,save:async (...args)=>writes.push(args),clear:async()=>{hidden++;}},
    onCached:async value=>{assert.equal(value,accepted);return value;},
    onDelta:async ()=>{failures++;throw Error('synthetic renderer rejection');},
    onReset:async()=>{hidden++;},onState:state=>states.push(state),
    startDeadline:()=>1,clearDeadline(){},schedule:fn=>{retry=fn;return 1;},cancel(){},
    read:async ({checkpoint,onEvent,signal})=>{
      reads.push(checkpoint);
      await onEvent('hello',{protocol:'graph-v1',epoch:e});
      if(reads.length===1)await onEvent('delta',{epoch:e,revision:next,base_revision:r,cursor:'6'});
      else await new Promise(resolve=>signal.addEventListener('abort',resolve,{once:true}));
    }});
  await c.initial;await settle();
  assert.equal(failures,1);assert.equal(states.at(-1),'snapshot-offline');
  retry();await settle();
  assert.deepEqual(reads,[accepted,null]);
  assert.equal(states.at(-1),'snapshot-connecting');
  assert.equal(hidden,0);assert.deepEqual(writes,[]);
  assert.deepEqual([...accepted.positions],[1,2,3]);
  c.close();
});

test('a warm renderer stays put when hello arrives but initial data stalls, then resumes on one new stream',async()=>{
  let callbacks,signal,fire,retry,reads=0;const timing=[],states=[],requests=[];
  const accepted={...warm,visualPositions:new Float32Array([1,2,3])};
  const c=openOwnerGraphSession({selector:{mode:'current'},scope:'scope',verifiedEpoch:e,
    cache:{take:async()=>accepted,clear:async()=>assert.fail('valid snapshot was cleared')},
    onCached:async value=>value,onReset:async()=>assert.fail('valid snapshot was hidden'),
    read:async x=>{
      ++reads;requests.push(x.checkpoint);callbacks=x;signal=x.signal;
      await new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));
    },
    onState:s=>states.push(s),onAttempt:x=>timing.push(x),
    startDeadline:(fn,ms)=>{fire={fn,ms};return fire;},clearDeadline:handle=>{if(fire===handle)fire=null;},
    schedule:fn=>{retry=fn;return 1;},cancel(){}});
  try{
    await c.initial;
    callbacks.onHeaders();callbacks.onFirstFrame();
    await callbacks.onEvent('hello',{epoch:e,protocol:'graph-v1'});
    assert.ok(fire?.ms>0,'hello without usable state remains bounded');
    await callbacks.onEvent('heartbeat',{});
    fire.fn();await settle();
    assert.equal(signal.aborted,true);
    assert.equal(timing.at(-1).outcome,'initial-state-timeout');
    assert.notEqual(timing.at(-1).hello_ms,null);
    assert.equal(states.at(-1),'snapshot-offline');
    retry();await settle();
    assert.equal(reads,2);
    assert.deepEqual(requests,[accepted,accepted]);
    assert.deepEqual([...accepted.visualPositions],[1,2,3]);
    assert.equal(states.includes('live'),false);
  }finally{c.close();}
});

test('valid progress extends first usable state timer but heartbeat alone does not, then caught-up ends it',async()=>{
  let receive,deadline; const states=[];
  const c=openOwnerGraphSession({selector:{mode:'current'},scope:'scope',verifiedEpoch:e,
    cache:{take:async()=>warm,save:async()=>{}},onCached:async x=>x,onDelta:async x=>x,
    read:async ({onEvent,signal})=>{receive=onEvent;await new Promise(resolve=>signal.addEventListener('abort',resolve,{once:true}));},
    onState:x=>states.push(x),
    startDeadline:fn=>{deadline={fn};return deadline;},clearDeadline:handle=>{if(deadline===handle)deadline=null;}});
  try{
    await c.initial;
    await receive('hello',{epoch:e,protocol:'graph-v1'});
    const afterHello=deadline;
    assert.ok(afterHello);
    await receive('heartbeat',{});
    assert.equal(deadline,afterHello);
    await receive('delta',{epoch:e,revision:next,base_revision:r,cursor:'6'});
    assert.ok(deadline);
    assert.notEqual(deadline,afterHello);
    await receive('caught-up',{epoch:e,revision:next,cursor:'6'});
    assert.equal(states.at(-1),'live');
    assert.equal(deadline,null);
  }finally{c.close();}
});
