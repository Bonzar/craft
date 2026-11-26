import assert from 'node:assert/strict';
import {test} from 'node:test';
import {LiveFallback} from '../src/live-fallback.mjs';

class Visibility extends EventTarget {
  hidden=false;
  change(hidden:boolean):void{this.hidden=hidden;this.dispatchEvent(new Event('visibilitychange'));}
}
const delay=(ms=0)=>new Promise<void>(resolve=>setTimeout(resolve,ms));

test('poller stops hidden-tab requests and resumes with one outstanding probe',async()=>{
  const doc=new Visibility();
  const original=globalThis.document;
  Object.defineProperty(globalThis,'document',{value:doc,configurable:true});
  const fallback=new LiveFallback();
  let active=0,peak=0,probes=0;
  let begun:()=>void=()=>{};
  try{
    doc.change(true);
    fallback.start(signal=>new Promise<void>(resolve=>{
      probes++;active++;peak=Math.max(peak,active);
      signal.addEventListener('abort',()=>{active--;resolve();},{once:true});
      begun();
    }),error=>assert.fail(String(error)),1);
    await delay(10);
    assert.equal(probes,0);
    const first=new Promise<void>(resolve=>begun=resolve);
    doc.change(false);
    await first;
    assert.equal(probes,1);
    doc.change(true);
    await delay(10);
    assert.equal(active,0);assert.equal(probes,1);
    const again=new Promise<void>(resolve=>begun=resolve);
    doc.change(false);
    await again;
    assert.equal(probes,2);assert.equal(peak,1);
    fallback.stop();assert.equal(active,0);
    doc.change(true);doc.change(false);await delay(10);
    assert.equal(probes,2);
  }finally{
    fallback.stop();
    Object.defineProperty(globalThis,'document',{value:original,configurable:true});
  }
});
