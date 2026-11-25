import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
import {versionableBundle} from './bundle.mjs';
const source = await readFile(new URL('admin-panel.js',import.meta.url),'utf8');
const base = {
  stage:'test',release_version:'assertion-memory-v0.1.9',global_order:false,
  capabilities:{enroll_device:true,reset_database:true},
  queues:[{id:'интерактивная',total:3,items:[{id:'первый',position:1},{id:'второй',position:2}],next_offset:2},
   {id:'фоновая',total:0,items:[]}],
};
class Element {
  constructor() { this.listeners={}; this.items=[]; this.value=''; }
  focus(){}
  addEventListener(event,fn) { this.listeners[event] = fn; }
  handle(event) { return this.listeners[event]?.({preventDefault(){}}); }
  setAttribute(){}
  replaceChildren() { this.items = []; }
  append(...els) { this.items.push(...els); for(const el of els) el.parentNode=this; }
  insertBefore(node,target) { this.items.splice(this.items.indexOf(target),0,node); node.parentNode=this; }
  showModal(){ this.open=true; }
  close(){ this.open=false; this.handle('close'); }
  querySelector(){ return (this.closeButton ??= new Element()); }
  reset(){}
}
const tick = () => new Promise(resolve=>setImmediate(resolve));
function simulate({status=base,challenge={confirmation_id:'proof',stage:'test',phrase:'ОЧИСТИТЬ TEST',expires_in:1}}={}) {
  const elements=new Map(),calls=[],navigated=[],timers=[];
  const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  async function fetch(url,options){
    assert.equal(options.credentials,'same-origin');
    assert.equal(options.redirect,'error');
    const body=options.body ? JSON.parse(options.body) : null;
    calls.push([url,body]);
    if (url.endsWith('offset=0')) return status ? Response.json(status) : Response.json({}, {status:403});
    if (url.endsWith('offset=2')) return Response.json({...base,queues:[{id:'интерактивная',total:3,items:[{id:'третий',position:3}]}]});
    if (url.endsWith('/reset/prepare')) return Response.json(challenge);
    if (url.endsWith('/devices')||url.endsWith('/reset')) return Response.json({ok:true});
    throw Error('Unexpected request');
  }
  runInNewContext(source,{fetch,document:{getElementById:element,createElement:()=>new Element()},location:{replace:x=>navigated.push(x)},setTimeout:fn=>timers.push(fn)-1,clearTimeout:id=>{timers[id]=undefined;}});
  return {calls,el:id=>element('admin-'+id),navigated,timers};
}
const labels = el => [el.textContent,...el.items.flatMap(labels)].filter(Boolean);
test('release contains existing worker, a fixed graph mode and integrated administration panel',async()=>{
  const visual=await versionableBundle(new URL('.',import.meta.url).pathname);
  const names=Object.keys(visual);
  assert.equal(names.length,4);
  const worker=names.find(x=>x.includes('worker'));
  assert.deepEqual(visual[worker],await readFile(new URL('ui/'+worker,import.meta.url)));
  const script=visual[names.find(x=>x.endsWith('.js')&&x!==worker)].toString();
  assert.match(script,/graphMode=`connections`/);
  assert.match(script,/e.hidden=true;e.disabled=true;e.id=`layout-toggle`/);
  assert.match(visual['index.html'].toString(),/id="topics-toggle" hidden/);
  assert.match(visual['index.html'].toString(),/id="memory-admin"/);
});
test('status shows Base version, lane and queues in server order, and pages independently',async()=>{
  const {calls,el}=simulate();
  await tick();
  assert.equal(el('badge').textContent,'TEST · assertion-memory-v0.1.9');
  const listed=labels(el('queues'));
  assert.ok(listed.indexOf('№1 · первый')<listed.indexOf('№2 · второй'));
  assert.ok(listed.includes('Пусто.'));
  const first=el('queues').items[1];
  await first.items.find(item=>item.textContent==='Следующие записи').handle('click');
  assert.ok(labels(first).includes('№3 · третий'));
  assert.equal(calls.length,2);
});
test('unknown stage or denied status disables all modifications',async()=>{
  for(const status of [null,{...base,stage:'unknown'},{...base,capabilities:{}}]){
    const {el,calls}=simulate({status});
    await tick();
    assert.equal(el('device-open').disabled,true);
    assert.equal(el('reset-open').disabled,true);
    await el('reset-open').handle('click');
    assert.equal(calls.length,1);
  }
});
test('device accepts name and exact fingerprint, never a raw key',async()=>{
  const {el,calls}=simulate();
  await tick();
  el('device-open').handle('click');
  el('device-name').value='Ноутбук';
  el('device-fingerprint').value='not-a-fingerprint';
  await el('device-form').handle('submit');
  assert.equal(calls.length,1);
  el('device-fingerprint').value='a'.repeat(64);
  await el('device-form').handle('submit');
  assert.deepEqual(calls[1],['/visual/admin/devices',{name:'Ноутбук',fingerprint:'a'.repeat(64)}]);
});
test('reset requires exact phrase, current stage and unexpired prepare, and does not reset on opening',async()=>{
  const {el,calls,navigated,timers}=simulate();
  await tick();
  await el('reset-open').handle('click');
  assert.deepEqual(calls[1],['/visual/admin/reset/prepare',{}]);
  assert.equal(el('reset-submit').disabled,true);
  el('reset-input').value='ОЧИСТИТЬ TEST ';
  await el('reset-form').handle('submit');
  assert.equal(calls.length,2);
  el('reset-input').value='ОЧИСТИТЬ TEST';
  timers[0]();
  await el('reset-form').handle('submit');
  assert.equal(calls.length,2);
  await el('reset-open').handle('click');
  el('reset-input').value='ОЧИСТИТЬ TEST';
  await el('reset-form').handle('submit');
  assert.deepEqual(calls[3],['/visual/admin/reset',{confirmation_id:'proof',confirmation:'ОЧИСТИТЬ TEST'}]);
  assert.deepEqual(navigated,['/']);
});
test('reset cannot use a confirmation from another stage',async()=>{
  const {el,calls}=simulate({challenge:{confirmation_id:'proof',stage:'prod',phrase:'ОЧИСТИТЬ PROD',expires_in:1}});
  await tick();
  await el('reset-open').handle('click');
  assert.equal(el('reset-input').disabled,true);
  el('reset-input').value='ОЧИСТИТЬ PROD';
  await el('reset-form').handle('submit');
  assert.equal(calls.length,2);
});
