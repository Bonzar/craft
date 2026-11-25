import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const script=await readFile(new URL('./queue-dashboard.js',import.meta.url),'utf8');

function harness(){
  class Element {
    constructor(tag=''){this.tag=tag;this.items=[];this.listeners={};this.open=false;this.dataset={};}
    addEventListener(name,fn){this.listeners[name]=fn;}
    append(...nodes){this.items.push(...nodes);}
    replaceChildren(...nodes){this.items=nodes;}
    setAttribute(name,value){this[name]=value;}
    focus(){doc.activeElement=this;}
    close(){this.open=false;this.listeners.close();}
    showModal(){this.open=true;}
    querySelector(key){return mapping[key];}
  }
  const events=[],urls=[],writes=[];
  const panel=new Element('dialog'),status=new Element(),container=new Element();
  const launcher=new Element('button'),closer=new Element('button'),copyStatus=new Element();
  const mapping={'#visual-queue':panel,'#visual-queue-open':launcher,'.queue-meta':status,
    '.queue-items':container,'.queue-copy-status':copyStatus,'#visual-queue-close':closer};
  const doc={querySelector:k=>mapping[k],createElement:t=>new Element(t)};
  const longId='65d14fb3a09c427fb6c3f8f161d0036c8c9afa083';
  let scheduled,data=[{id:'source',status:'active',total:51,next_offset:50,items:[{id:longId,status:'ready',phase:'extract',position:1}]},
    {id:'interactive',status:'active',total:0,items:[]},{id:'maintenance',status:'paused',total:0,items:[]}];
  const text=e=>[e.textContent,...e.items.flatMap(text)].filter(Boolean).join(' ');
  const find=(e,p)=>p(e)?e:e.items.map(x=>find(x,p)).find(Boolean);
  const delay=()=>new Promise(resolve=>setImmediate(resolve));
  class EventSource {
    constructor(path,options){assert.equal(path,'/api/v1/events?channel=dashboard');assert.equal(options.withCredentials,true);this.listeners={};events.push(this);}
    addEventListener(event,callback){this.listeners[event]=callback;}
    close(){this.closed=true;}
  }
  runInNewContext(script,{document:doc,EventSource,AbortController,addEventListener(){},
    navigator:{clipboard:{writeText:async id=>writes.push(id)}},
    setInterval:fn=>{scheduled=fn;return 1;},clearInterval(){scheduled=null;},
    fetch:async (url,options)=>{
      assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');
      urls.push(url);
      const offset=Number(url.split('offset=')[1]);
      return Response.json({stage:'test',release_version:'assertion-memory-v1.2.3',queues:offset===0?data:
        [{id:'source',status:'active',total:51,items:[{id:'second-page',status:'ready',phase:'extract',position:51}]}]});
    }});
  const signal=async()=>{events.at(-1).listeners.queue_changed();await delay();};
  return {doc, panel, status, container, launcher, closer, copyStatus, writes, events, urls, text, find, longId,
    timer:()=>scheduled,setData:x=>{data=x;},signal,async open(){launcher.listeners.click();await delay();},delay};
}

test('full-screen modal has three labeled lanes, card chips and real positions',async()=>{
  const h=harness();
  assert.equal(h.urls.length,0);
  await h.open();
  assert.equal(h.panel.open,true);
  assert.equal(h.status.textContent,'TEST · База assertion-memory-v1.2.3');
  assert.deepEqual(Array.from(h.container.items,x=>x['aria-label']),['Вопросы','Реплики','Связи и пересчёт']);
  const row=h.find(h.container,e=>e.className==='queue-card');
  assert.equal(row.tag,'article');
  assert.match(h.text(row),/Ожидает № 1 Извлечение/);
  assert.match(h.text(h.container.items[2]),/Приостановлено Нет данных о задачах/);
  const summary=h.find(row,e=>e.tag==='summary');
  assert.doesNotMatch(summary.textContent,new RegExp(h.longId));
  assert.match(summary.textContent,/…/);
  const copy=h.find(row,e=>e.tag==='button');
  await copy.listeners.click();
  assert.deepEqual(h.writes,[h.longId]);
  assert.equal(h.copyStatus.textContent,'ID скопирован.');
  h.closer.listeners.click();
  assert.equal(h.panel.open,false);
  assert.equal(h.doc.activeElement,h.launcher);
  assert.equal(h.events[0].closed,true);
  assert.equal(h.timer(),null);
});

test('SSE reconciles persisted phases, preserves disclosure and focus, and clears stale position',async()=>{
  const h=harness();await h.open();
  const previous=h.find(h.container,e=>e.tag==='details');
  previous.open=true;
  previous.items[0].focus();
  h.setData([{id:'source',status:'active',total:1,items:[{id:h.longId,status:'running',phase:'partial_source',position:1}]},
    {id:'maintenance',status:'active',total:1,items:[{id:'y',status:'ready',phase:'consolidate',position:0}]}]);
  await h.signal();
  const now=h.find(h.container.items[1],e=>e.tag==='details');
  assert.equal(now.open,true);
  assert.equal(h.doc.activeElement,now.items[0]);
  assert.match(h.text(h.container),/В работе Частично сохранённый источник/);
  assert.match(h.text(h.container),/Поиск связей/);
  assert.doesNotMatch(h.text(h.container),/№/);
  h.setData([{id:'source',status:'active',total:0,items:[]}]);
  await h.signal();
  assert.match(h.text(h.container.items[1]),/Нет задач/);
  assert.doesNotMatch(h.text(h.container),new RegExp(h.longId));
});

test('paging one lane does not advance other lanes or reorder their cards',async()=>{
  const h=harness();await h.open();
  const more=h.find(h.container.items[1],e=>e.textContent==='Ещё задачи');
  more.listeners.click();
  await h.delay();
  assert.match(h.text(h.container.items[1]),/№ 51/);
  assert.match(h.text(h.container.items[2]),/Приостановлено/);
  assert.equal(h.urls.length,3);
  assert.match(h.urls.at(-1),/offset=50$/);
  assert.ok(h.urls.every(url=>url.startsWith('/api/v1/capabilities?queue_status=1')));
});
