import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const script=await readFile(new URL('./queue-dashboard.js',import.meta.url),'utf8');
test('queue reconciles real positions and phases on the same-origin SSE signal',async()=>{
  class Element {
    constructor(){this.items=[];this.listeners={};this.open=false;}
    addEventListener(name,fn){this.listeners[name]=fn;}
    append(node){this.items.push(node);}
    replaceChildren(){this.items=[];}
  }
  const events=[],urls=[],panel=new Element(),status=new Element(),container=new Element();
  let scheduled,tasks=[{id:'job',status:'ready',phase:'extract',position:1}];
  panel.querySelector=k=>k==='[role=status]'?status:container;
  const rendered=e=>[e.textContent,...e.items.flatMap(rendered)].join(' ');
  const wait=()=>new Promise(resolve=>setImmediate(resolve));
  class EventSource {
    constructor(path,options){assert.equal(path,'/api/v1/events?channel=dashboard');assert.equal(options.withCredentials,true);this.listeners={};events.push(this);}
    addEventListener(event,callback){this.listeners[event]=callback;}
    close(){this.closed=true;}
  }
  runInNewContext(script,{
    document:{querySelector:()=>panel,createElement:()=>new Element()},
    EventSource,addEventListener(){},setInterval:fn=>{scheduled=fn;return 1;},clearInterval(){},
    fetch:async (url,options)=>{
      assert.equal(options.credentials,'same-origin');
      urls.push(url);
      return Response.json({stage:'test',release_version:'assertion-memory-v1.2.3',queues:[
        {id:'source',status:'active',total:tasks.length,items:tasks}
      ]});
    }
  });
  panel.open=true;panel.listeners.toggle();await wait();
  assert.equal(status.textContent,'TEST · База assertion-memory-v1.2.3');
  assert.match(rendered(container),/№1 · Ожидает · Извлечение/);
  tasks=[{id:'job',status:'running',phase:'audit',position:null}];
  events[0].listeners.queue_changed();await wait();
  assert.match(rendered(container),/В работе · Проверка/);
  assert.doesNotMatch(rendered(container),/№1/);
  tasks=[];
  events[0].listeners.queue_changed();await wait();
  assert.match(rendered(container),/Пусто/);
  assert(!rendered(container).includes('job'));
  assert(urls.every(u=>u.startsWith('/api/v1/capabilities?queue_status=1')));
  assert.equal(typeof scheduled,'function');
  panel.open=false;panel.listeners.toggle();
  assert.equal(events[0].closed,true);
});
