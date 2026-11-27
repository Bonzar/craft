import './test_device_enrollment.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const script=await readFile(new URL('./queue-dashboard.js',import.meta.url),'utf8');

function harness({stage='test',control=null,session={authenticated:true},prepare,put,standalone=false,sessionPolicy=null,processingPolicy=null,observe=false,clear=null,clearWrite,sourceEpoch,startupPacked=false}={}){
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
  const events=[],urls=[],writes=[],windowHandlers={};
  const panel=new Element('dialog'),status=new Element(),container=new Element();
  const launcher=new Element('button'),closer=new Element('button'),copyStatus=new Element();
  const controlPanel=new Element(), controlButton=new Element('button'), controlState=new Element(), controlFeedback=new Element();
  const sessionPanel=new Element('details'),sessionOptions=new Element(),sessionFeedback=new Element(),sessionSave=new Element('button');
  const itemFeedback=new Element(),removed=[];
  const sessionWrites=[];
  const mutations=[];
  const clearCalls=[];
  const clearDom=Object.fromEntries(['database-clear','clear-open','clear-instance','clear-confirmation',
    'clear-label','clear-typed','clear-commit','clear-cancel','clear-feedback'].map(k=>['#queue-'+k,new Element()]));

  const mapping={...clearDom,'#queue-item-feedback':itemFeedback,'#queue-session-policy':sessionPanel,'#queue-session-options':sessionOptions,'#queue-session-feedback':sessionFeedback,'#queue-session-save':sessionSave,'#queue-processing':controlPanel,'#queue-processing-toggle':controlButton,
    '#queue-processing-state':controlState,'#queue-processing-feedback':controlFeedback,'#visual-queue':panel,'#visual-queue-open':launcher,'.queue-meta':status,
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
  let observed,targets;
  class IO{
    constructor(callback){observed=callback;targets=new Set();}
    observe(e){targets.add(e);}
    unobserve(e){targets.delete(e);}
    disconnect(){targets.clear();}
  }
  runInNewContext(script,{document:doc,EventSource,AbortController,...(startupPacked?{releaseFirstOwnerSnapshot(){}}:{}),...(observe?{IntersectionObserver:IO}:{}),addEventListener(name,fn){windowHandlers[name]=fn;},
    navigator:{clipboard:{writeText:async id=>writes.push(id)}},
    setInterval:fn=>{scheduled=fn;return 1;},clearInterval(){scheduled=null;},
    fetch:async (url,options)=>{
      assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');
      urls.push(url);
      if(url.startsWith('/api/v1/processing/database/clear-v2')){
        const args=JSON.parse(options.body);
        clearCalls.push({url,args,method:options.method,csrf:options.headers['x-csrf-token']});
        assert.equal(url,'/api/v1/processing/database/clear-v2');
        if(clearWrite)return clearWrite();
        clear={...clear,revision:'b'.repeat(32)};
        data=data.map(q=>({...q,total:0,next_offset:null,items:[]}));
        return Response.json({stage,revision:clear.revision,cleared:true,capture_rearm:"waiting"});
      }
      if(url==='/api/v1/processing/items/source-preview/prepare'){
        assert.equal(options.method,'POST');return Response.json({stage,csrf_token:'123.'+'a'.repeat(64)});
      }
      if(url==='/api/v1/processing/items/source-preview'){
        assert.equal(options.method,'PUT');const body=JSON.parse(options.body);
        return Response.json({stage,queue_id:'source',item_id:body.item_id,source_role:'user',source_preview:'<script>текст</script>'});
      }
      if(url==='/api/v1/processing/items/remove/prepare'){
        assert.equal(options.method,'POST');
        return Response.json({stage,csrf_token:'123.'+'a'.repeat(64)});
      }
      if(url==='/api/v1/processing/items/remove'){
        assert.equal(options.method,'PUT');
        assert.equal(options.headers['x-csrf-token'],'123.'+'a'.repeat(64));
        const args=JSON.parse(options.body);removed.push(args);
        return Response.json({stage,queue_id:args.queue_id,item_id:args.item_id,removed:true,effect:args.queue_id==='source'?'source_and_queue':'queue_only'});
      }
      if(url==='/api/v1/processing/sessions/prepare'){
        assert.equal(options.method,'POST');
        if(JSON.parse(options.body).format==='policy-v1')
          return processingPolicy?Response.json({...processingPolicy,csrf_token:'123.'+'a'.repeat(64)}):Response.json({}, {status:404});
        return sessionPolicy?Response.json({...sessionPolicy,csrf_token:'123.'+'a'.repeat(64)}):Response.json({}, {status:404});
      }
      if(url==='/api/v1/processing/sessions'){
        const payload=JSON.parse(options.body);
        sessionWrites.push({payload,token:options.headers['x-csrf-token']});
        if(payload.policy&&processingPolicy){
          processingPolicy={...processingPolicy,revision:'f'.repeat(32),policy:payload.policy};
          return Response.json(processingPolicy);
        }
        assert.equal(options.method,'PUT');
        sessionPolicy={...sessionPolicy,revision:'c'.repeat(32),selected_session_ids:payload.selected_session_ids};
        return Response.json(sessionPolicy);
      }
      if(url==='/api/v1/session')return Response.json(session);
      if(url==='/api/v1/processing/pause/prepare'){
        assert.equal(options.method,'POST');
        assert.equal(options.body,'{}');
        return prepare?prepare(control):Response.json({}, {status:404});
      }
      if(url==='/api/v1/processing/pause'){
        const payload=JSON.parse(options.body);
        mutations.push({method:options.method,token:options.headers['x-csrf-token'],payload});
        if(put)return put(payload);
        const next={stage,paused:payload.paused,revision:(payload.paused?'b':'c').repeat(32)};
        control={...next,can_change:control.can_change};
        return Response.json(next);
      }
      const offset=Number(url.split('offset=')[1]);
      return Response.json({stage,release_generation:'e'.repeat(64),owner_live_epoch:sourceEpoch,database_clear:clear,capabilities:{standalone_pause:standalone,standalone_queue_sessions:Boolean(sessionPolicy||processingPolicy),owner_database_clear_post:clear?.can_clear===true},processing_control:control,release_version:'assertion-memory-v1.2.3',queues:offset===0?data:
        [{id:'source',status:'active',total:51,items:[{id:'second-page',status:'ready',phase:'extract',position:51}]}]});
    }});
  const signal=async()=>{events.at(-1).listeners.queue_changed();await delay();};
  return {clearNode:k=>clearDom['#queue-'+k],clearCalls,setClear:x=>{clear=x;},doc, panel, status, container, launcher, closer, copyStatus, writes, events, urls, text, find, longId,
    controlPanel,controlButton,controlState,controlFeedback,mutations,sessionPanel,sessionOptions,sessionFeedback,sessionSave,sessionWrites,itemFeedback,removed,intersect:()=>observed([...targets].map(target=>({target,isIntersecting:true}))),timer:()=>scheduled,async ownerEvent(name,detail){windowHandlers['owner-live-'+name]({detail});await delay();},setData:x=>{data=x;},signal,async open(){launcher.listeners.click();await delay();},delay};
}

test('independent owner queue sits above canvas failure and below sign-in',async()=>{
  const [dashboard,graph,queueHtml]=await Promise.all([
    readFile(new URL('./queue-dashboard.css',import.meta.url),'utf8'),
    readFile(new URL('../../apps/graph_viewer/web/src/style.css',import.meta.url),'utf8'),
    readFile(new URL('./queue-dashboard.html',import.meta.url),'utf8'),
  ]);
  const rule=(css,selector)=>css.slice(css.indexOf(selector+'{')+selector.length+1).split('}')[0];
  const layer=(css,selector)=>Number(rule(css,selector).match(/z-index:(\d+)/)?.[1]);
  assert.match(rule(graph,'.loading-overlay'),/pointer-events:auto/);
  assert.ok(layer(dashboard,'#visual-queue-open')>layer(graph,'.loading-overlay'));
  assert.ok(layer(dashboard,'#visual-queue-open')<layer(graph,'.login-overlay'));
  assert.match(dashboard,/body:has\(#login:not\(\[hidden\]\)\) #visual-queue-open\{display:none\}/);
  assert.match(queueHtml,/^<button id="visual-queue-open" /);
  // Dashboard harness has no canvas or Viewer; consumes only the owner session and queue API.
  const app=harness({stage:'test',standalone:true,session:{authenticated:true,csrf_token:csrf},
    control:{paused:false,revision:'a'.repeat(32),can_change:true},
    sessionPolicy:{stage:'test',revision:'b'.repeat(32),selected_session_ids:[],sessions:[]}});
  await app.open();await app.delay();
  assert.equal(app.panel.open,true);
  assert.equal(app.controlButton.hidden,false);
  assert.equal(app.sessionPanel.hidden,false);
  assert.equal(app.mutations.length,0);
});
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
  assert.equal(h.urls.filter(url=>url.startsWith('/api/v1/capabilities')).length,3);
  assert.match(h.urls.at(-1),/offset=50$/);
  assert.ok(h.urls.filter(url=>!url.endsWith('/processing/sessions/prepare')).every(url=>url.startsWith('/api/v1/capabilities?queue_status=1')));
});


const original='a'.repeat(32), csrf='sample_csrf_for_synthetic_owner_only';
test('pause state can be read without CSRF but pseudo-session never enables mutation',async()=>{
  const h=harness({control:{paused:true,revision:original,can_change:true}});
  await h.open();
  assert.equal(h.controlPanel.hidden,false);
  assert.equal(h.controlState.textContent,'TEST · Обработка на паузе');
  assert.equal(h.controlButton.hidden,true);
  await h.controlButton.listeners.click();
  assert.deepEqual(h.mutations,[]);
  const unknown=harness({control:{paused:false,revision:'unproven',can_change:true},session:{authenticated:true,csrf_token:csrf}});
  await unknown.open();
  assert.equal(unknown.controlPanel.hidden,true);
  assert.deepEqual(unknown.urls.filter(url=>!url.endsWith('/processing/sessions/prepare')).length,1);
});

test('pause and resume set explicit stage and revision with current owner CSRF',async()=>{
  const h=harness({stage:'prod',control:{paused:false,revision:original,can_change:true},session:{authenticated:true,csrf_token:csrf}});
  await h.open();assert.equal(h.controlButton.hidden,false);
  await h.controlButton.listeners.click();await h.delay();
  assert.deepEqual(h.mutations,[{method:'PUT',token:csrf,payload:{stage:'prod',paused:true,expected_revision:original}}]);
  assert.equal(h.controlState.textContent,'PROD · Обработка на паузе');
  await h.controlButton.listeners.click();await h.delay();
  assert.deepEqual(h.mutations.at(-1).payload,{stage:'prod',paused:false,expected_revision:'b'.repeat(32)});
  assert.equal(h.controlState.textContent,'PROD · Обработка не на паузе');
  assert.match(h.text(h.container.items[0]),/Вопросы 0 Активна/);
});

test('conflict, async acceptance and foreign-stage replies are never rendered as success',async()=>{
  for(const rejected of [()=>Response.json({}, {status:409}),()=>Response.json({paused:true}, {status:202}),
    ()=>Response.json({stage:'prod',paused:true,revision:'b'.repeat(32)})]){
    const h=harness({control:{paused:false,revision:original,can_change:true},session:{authenticated:true,csrf_token:csrf},put:rejected});
    await h.open();await h.controlButton.listeners.click();await h.delay();
    assert.equal(h.mutations.length,1);
    assert.equal(h.controlState.textContent,'TEST · Обработка не на паузе');
    assert.doesNotMatch(h.controlFeedback.textContent,/Пауза включена/);
  }
});

test('native TEST button uses POST prepare and never the pseudo-session token',async()=>{
  const ncsrf='1650000000.'+'d'.repeat(64);
  const h=harness({control:{paused:false,revision:original,can_change:false},
    prepare:({paused,revision})=>Response.json({stage:'test',paused,revision,csrf_token:ncsrf})});
  await h.open();
  assert.equal(h.controlButton.hidden,false);
  assert.ok(!h.urls.includes('/api/v1/session'));
  await h.controlButton.listeners.click();
  await h.delay();
  assert.deepEqual(h.mutations,[{method:'PUT',token:ncsrf,payload:{stage:'test',paused:true,expected_revision:original}}]);
  assert.equal(h.controlState.textContent,'TEST · Обработка на паузе');
  await h.controlButton.listeners.click();
  await h.delay();
  assert.deepEqual(h.mutations.at(-1).payload,{stage:'test',paused:false,expected_revision:'b'.repeat(32)});
  assert.equal(h.controlState.textContent,'TEST · Обработка не на паузе');
});

test('native prepare does not grant control on foreign lane or mismatched revision',async()=>{
  const ncsrf='1650000000.'+'d'.repeat(64);
  const prepare=()=>Response.json({stage:'test',paused:false,revision:'e'.repeat(32),csrf_token:ncsrf});
  const control={paused:false,revision:original,can_change:false};
  const test=harness({control,prepare}), prod=harness({stage:'prod',control,prepare});
  await test.open();
  await prod.open();
  assert.equal(test.controlButton.hidden,true);
  assert.equal(prod.controlButton.hidden,true);
  assert.ok(!prod.urls.includes('/api/v1/processing/pause/prepare'));
  await test.controlButton.listeners.click();
  assert.equal(test.mutations.length,0);
});

test('standalone PROD pause requires capability and matching confirmed prepare',async()=>{
  const ncsrf='1650000000.'+'d'.repeat(64);
  const control={paused:false,revision:original,can_change:false};
  const prepare=({paused,revision})=>Response.json({stage:'prod',paused,revision,csrf_token:ncsrf});
  const good=harness({stage:'prod',standalone:true,control,prepare});
  const off=harness({stage:'prod',control,prepare});
  const foreign=harness({stage:'prod',standalone:true,control,
    prepare:()=>Response.json({stage:'test',paused:false,revision:original,csrf_token:ncsrf})});
  await Promise.all([good.open(),off.open(),foreign.open()]);
  assert.equal(good.controlButton.hidden,false);
  assert.equal(off.controlButton.hidden,true);
  assert.equal(foreign.controlButton.hidden,true);
  assert.ok(!off.urls.includes('/api/v1/processing/pause/prepare'));
  await good.controlButton.listeners.click();
  await good.delay();
  assert.deepEqual(good.mutations,[{method:'PUT',token:ncsrf,
    payload:{stage:'prod',paused:true,expected_revision:original}}]);
  assert.match(good.text(good.container.items[0]),/Вопросы 0 Активна/);
});


test('queue editor changes exact TEST scheduler selection and displays held reason',async()=>{
  const opt={standalone:true,sessionPolicy:{stage:'test',revision:'b'.repeat(32),selected_session_ids:[],sessions:[{id:'session-one',queued:2,eligible:true},{id:'unavailable',queued:0,eligible:false}]}};
  const h=harness(opt);h.setData([{id:'source',status:'active',total:1,items:[{id:'job',status:'ready',phase:'extract',hold_reason:'session_excluded',session_id:'session-one'}]}]);
  await h.open();await h.delay();
  assert.equal(h.sessionPanel.hidden,false);
  const box=h.sessionOptions.items[0].items[0];
  assert.equal(box.checked,false);
  assert.equal(h.sessionOptions.items[1].items[0].disabled,true);
  assert.match(h.text(h.container),/Сессия не выбрана для автообработки/);
  box.checked=true;box.listeners.change();
  assert.equal(h.sessionSave.disabled,false);
  await h.sessionSave.listeners.click();
  assert.equal(h.sessionWrites.length,1);
  assert.deepEqual(h.sessionWrites[0].payload,{stage:'test',selected_session_ids:['session-one'],expected_revision:'b'.repeat(32)});
  assert.equal(h.sessionWrites[0].token,'123.'+'a'.repeat(64));
  assert.equal(h.sessionFeedback.textContent,'Выбор сохранён.');
});

test('PROD dashboard does not try TEST scheduler management',async()=>{
  const h=harness({stage:'prod',standalone:true});await h.open();
  assert.equal(h.sessionPanel.hidden,true);
  assert.ok(!h.urls.some(url=>url.includes('/processing/sessions')));
});


test('PROD standalone prepares its own selection, preview and deletion without a separate feature flag',async()=>{
  const stage='prod',revision='b'.repeat(32);
  const h=harness({stage,standalone:true,sessionPolicy:{stage,revision,selected_session_ids:[],sessions:[{id:'same-lane',queued:1,eligible:true}]}});
  h.setData([{id:'source',status:'active',total:1,items:[{id:'job',status:'ready',phase:'extract',preview_revision:revision,remove_revision:revision}]}]);
  await h.open();
  const box=h.sessionOptions.items[0].items[0];box.checked=true;box.listeners.change();
  await h.sessionSave.listeners.click();
  assert.deepEqual(h.sessionWrites[0].payload,{stage,selected_session_ids:['same-lane'],expected_revision:revision});
  const action=label=>h.find(h.container,e=>e.tag==='button'&&e.textContent===label);
  await action('Показать текст реплики').listeners.click();
  assert.match(h.text(h.container),/<script>текст<\/script>/);
  action('Удалить реплику и задачу').listeners.click();
  await action('Подтвердить удаление').listeners.click();
  assert.deepEqual(h.removed,[{stage,queue_id:'source',item_id:'job',expected_revision:revision}]);
});

const ownerPolicy=()=>({stage:'test',revision:'b'.repeat(32),selected_session_ids:[],sessions:[]});

test('source plain excerpt and deletion require item-specific confirmation',async()=>{
  const h=harness({sessionPolicy:ownerPolicy()});h.setData([{id:'source',status:'active',total:2,items:[
    {id:'ordinary',status:'ready',phase:'extract',preview_revision:'c'.repeat(32),remove_revision:'a'.repeat(32)},
    {id:'running',status:'running',phase:'extract',remove_revision:'b'.repeat(32)}]}]);
  await h.open();
  const cards=h.container.items[1].items[2].items;
  const action=(card,label)=>h.find(card,e=>e.tag==='button'&&e.textContent===label);
  assert.ok(!h.urls.some(url=>url.includes('/source-preview')));
  await action(cards[0],'Показать текст реплики').listeners.click();
  assert.match(h.text(cards[0]),/Ты:.*<script>текст<\/script>/);
  await h.signal();
  assert.equal(h.urls.filter(url=>url.includes('/source-preview')).length,2);
  assert.equal(action(cards[1],'Удалить реплику и задачу'),undefined);
  assert.match(h.text(cards[1]),/обработка реплики уже началась/);
  assert.ok(cards[0].items.some(e=>e.tag!=='details'&&action(e,'Удалить реплику и задачу')));
  action(cards[0],'Удалить реплику и задачу').listeners.click();
  assert.equal(h.removed.length,0);
  action(cards[0],'Отмена').listeners.click();
  assert.equal(h.removed.length,0);
  action(cards[0],'Удалить реплику и задачу').listeners.click();
  await action(cards[0],'Подтвердить удаление').listeners.click();
  assert.deepEqual(h.removed,[{stage:'test',queue_id:'source',item_id:'ordinary',expected_revision:'a'.repeat(32)}]);
  assert.match(h.itemFeedback.textContent,/Исходный чат не изменён/);
});


test('maintenance removal confirms queue only and published source cannot be removed',async()=>{
  const h=harness({sessionPolicy:ownerPolicy()});h.setData([
    {id:'source',status:'active',total:1,items:[{id:'scoped',status:'ready',phase:'audit',remove_revision:'a'.repeat(32)}]},
    {id:'maintenance',status:'active',total:1,items:[{id:'maint',status:'ready',phase:'refresh',remove_revision:'b'.repeat(32)}]}]);
  await h.open();
  const action=(card,label)=>h.find(card,e=>e.tag==='button'&&e.textContent===label);
  assert.equal(action(h.container.items[1],'Удалить реплику и задачу'),undefined);
  action(h.container.items[2],'Удалить из очереди').listeners.click();
  assert.match(h.text(h.container.items[2]),/Удалить только задачу из очереди/);
  await action(h.container.items[2],'Подтвердить удаление').listeners.click();
  assert.deepEqual(h.removed,[{stage:'test',queue_id:'maintenance',item_id:'maint',expected_revision:'b'.repeat(32)}]);
  assert.match(h.itemFeedback.textContent,/Реплики и граф не изменены/);
});


test('viewport preview reads are serialized, capped and not repeated by refresh',async()=>{
  const h=harness({observe:true,sessionPolicy:ownerPolicy()});h.setData([{id:'source',status:'active',total:8,items:Array.from({length:8},(_,idx)=>({
    id:'job'+idx,status:'ready',phase:'extract',preview_revision:String(idx).repeat(32)
  }))}]);
  await h.open();
  assert.ok(!h.urls.some(url=>url.includes('/source-preview')));
  h.intersect();
  for(let i=0;i<10;i++)await h.delay();
  assert.equal(h.urls.filter(url=>url.endsWith('/source-preview')).length,6);
  await h.signal();
  h.intersect();
  await h.delay();
  assert.equal(h.urls.filter(url=>url.endsWith('/source-preview')).length,6);
  const extra=h.find(h.container,e=>e.textContent==='Показать текст реплики');
  await extra.listeners.click();
  assert.equal(h.urls.filter(url=>url.endsWith('/source-preview')).length,7);
});

test('native TEST fallback probes once without a advertised standalone capability; no owner proof means no item controls', async()=>{
 const h=harness({observe:true});
 h.setData([{id:'source',status:'active',total:1,items:[{id:'x',status:'ready',phase:'extract',remove_revision:'b'.repeat(32),preview_revision:'c'.repeat(32)}]}]);
 await h.open();await h.signal();
 assert.equal(h.urls.filter(x=>x.endsWith('/processing/sessions/prepare')).length,1);
 assert.equal(h.sessionPanel.hidden,true);
 assert.doesNotMatch(h.text(h.container),/Удалить реплику|Показать текст/);
});

for(const stage of ['test','prod'])test(stage+' shows one source batch card with ordered, individually protected previews',async()=>{
  const h=harness({stage,capabilities:{standalone_pause:true,standalone_queue_sessions:true},
    sessionPolicy:{stage,revision:'b'.repeat(32),selected_session_ids:[],sessions:[]}});
  h.setData([{id:'source',status:'active',total:1,source_count:2,items:[
    {id:'batch:'+'c'.repeat(64),status:'ready',phase:'extract',position:1,member_count:2,
     members:[{id:'first',preview_revision:'d'.repeat(32)},{id:'second',preview_revision:'e'.repeat(32)}]}]}]);
  await h.open();
  const cards=h.container.items[1].items[2].items;
  assert.equal(cards.length,1);
  assert.match(h.text(cards[0]),/Пакет реплик · 2/);
  assert.match(h.text(h.container),/Реплик: 2/);
  assert.doesNotMatch(h.text(cards[0]),/Удалить/);
  const members=h.find(cards[0],e=>e.tag==='ol').items;
  assert.equal(members.length,2);
  for(const entry of members){
    await h.find(entry,e=>e.tag==='button').listeners.click();
    assert.match(h.text(entry),/Ты:.*<script>текст<\/script>/);
  }
  assert.equal(h.urls.filter(x=>x.includes('/source-preview')).length,4);
});

for(const stage of ['test','prod'])test(stage+' saves an owner processing policy with all/exclusion/date and then include selection',async()=>{
  const h=harness({stage,standalone:true,control:{paused:false,revision:'b'.repeat(32),can_change:true},
    processingPolicy:{stage,revision:'b'.repeat(32),policy:{mode:'all',included_session_ids:[],
      excluded_session_ids:[],added_from:null,added_through:null},sessions:[{id:'native',eligible:true,queued:2}]}});
  await h.open();
  assert.equal(h.sessionPanel.hidden,false);
  assert.match(h.text(h.sessionOptions),/Все сессии, кроме исключённых/);
  const row=h.find(h.sessionOptions,e=>e.type==='checkbox');
  row.checked=true;row.listeners.change();
  const [from,through]=h.sessionOptions.items[0].items.slice(1).map(e=>e.items[0]);
  from.value='2026-11-26';from.listeners.change();
  through.value='2026-11-25';through.listeners.change();
  assert.equal(h.sessionSave.disabled,true);
  through.value='2026-11-26';through.listeners.change();
  assert.equal(h.sessionSave.disabled,false);
  await h.sessionSave.listeners.click();
  assert.deepEqual(h.sessionWrites[0].payload,{stage,expected_revision:'b'.repeat(32),
    policy:{mode:'all',included_session_ids:[],excluded_session_ids:['native'],
      added_from:'2026-11-26T00:00:00Z',added_through:'2026-11-26T23:59:59.999999Z'}});
  const mode=h.find(h.sessionOptions,e=>e.tag==='select');
  mode.value='include';mode.listeners.change();
  const include=h.find(h.sessionOptions,e=>e.type==='checkbox');
  include.checked=true;include.listeners.change();
  await h.sessionSave.listeners.click();
  assert.deepEqual(h.sessionWrites[1].payload.policy.included_session_ids,['native']);
  assert.deepEqual(h.sessionWrites[1].payload.policy.excluded_session_ids,[]);
});

for(const stage of ['test','prod'])test(stage+' clear sends one owner-CSRF POST after explicit confirmation only',async()=>{
  const token='owner_session_'+'a'.repeat(32);
  const phrase='ОЧИСТИТЬ '+stage.toUpperCase();
  const h=harness({stage,session:{authenticated:true,csrf_token:token},clear:{can_clear:true}});
  await h.open();
  const get=h.clearNode;
  assert.equal(get('clear-open').disabled,false);
  assert.match(get('clear-instance').textContent,new RegExp(stage.toUpperCase()));
  get('clear-open').listeners.click();
  assert.equal(h.doc.activeElement,get('clear-typed'));
  assert.deepEqual(h.clearCalls,[]);
  get('clear-typed').value='ОЧИСТИТЬ НЕ ТО';
  await get('clear-commit').listeners.click();
  get('clear-cancel').listeners.click();
  assert.deepEqual(h.clearCalls,[]);
  get('clear-open').listeners.click();
  get('clear-typed').value=phrase;
  await get('clear-commit').listeners.click();
  assert.deepEqual(h.clearCalls,[{url:'/api/v1/processing/database/clear-v2',method:'POST',args:{confirmation:phrase},csrf:token}]);
  assert.match(get('clear-feedback').textContent,/очищена/);
});

test('absent owner/session or withdrawn capability cannot write; uncertain POST is not retried',async()=>{
  const inactive=harness({session:{authenticated:false},clear:{can_clear:true}});
  await inactive.open();
  inactive.clearNode('clear-open').listeners.click();
  inactive.clearNode('clear-typed').value='ОЧИСТИТЬ TEST';
  await inactive.clearNode('clear-commit').listeners.click();
  assert.deepEqual(inactive.clearCalls,[]);
  const token='owner_session_'+'a'.repeat(32);
  const h=harness({session:{authenticated:true,csrf_token:token},clear:{can_clear:true},clearWrite:()=>Response.json({}, {status:503})});
  await h.open();
  const get=h.clearNode;
  get('clear-open').listeners.click();
  h.setClear({can_clear:false});await h.signal();
  assert.equal(get('clear-confirmation').hidden,true);
  assert.deepEqual(h.clearCalls,[]);
  h.setClear({can_clear:true});await h.signal();
  get('clear-open').listeners.click();
  get('clear-typed').value='ОЧИСТИТЬ TEST';
  await get('clear-commit').listeners.click();
  assert.equal(h.clearCalls.length,1);
  assert.match(get('clear-feedback').textContent,/не подтверждён/);
});

test('unadvertised clear stays hidden; capture resumes only when separately acknowledged',async()=>{
  const old=harness();
  await old.open();
  assert.equal(old.clearNode('clear-open').disabled,true);
  assert.deepEqual(old.clearCalls,[]);
  const h=harness({clear:{can_clear:true,capture_rearm:'waiting'}});
  await h.open();
  assert.match(h.clearNode('clear-instance').textContent,/Ожидаем повторного подтверждения/);
  h.setClear({can_clear:true,capture_rearm:'confirmed'});await h.signal();
  assert.match(h.clearNode('clear-instance').textContent,/Получено подтверждение/);
  assert.deepEqual(h.clearCalls,[]);
});

test('initial authoritative stream primes the queue, push changes update it with no poll',async()=>{
  const epoch='a'.repeat(64), q=harness({sourceEpoch:epoch});
  const reads=()=>q.urls.filter(x=>x.includes('queue_status=1')).length;
  assert.equal(reads(),0);
  await q.ownerEvent('snapshot',{epoch,queue:true});
  assert.equal(reads(),1);
  await q.ownerEvent('connection','live');
  await q.open();assert.equal(reads(),1);assert.equal(q.events.length,0);
  q.timer()();await q.delay();assert.equal(reads(),1);
  q.setData([{id:'source',status:'active',total:1,items:[{id:'new-stream-row',status:'ready',phase:'extract',position:1}]}]);
  await q.ownerEvent('snapshot',{epoch,queue:true});
  assert.equal(reads(),2);assert.match(q.text(q.container),/new-stream-row/);
  await q.ownerEvent('invalidate');assert.doesNotMatch(q.text(q.container),/new-stream-row/);
});

test('early packed queue click and poll wait for registered snapshot or explicit offline',async()=>{
  const epoch='a'.repeat(64);
  const q=harness({sourceEpoch:epoch,startupPacked:true});
  const count=(u)=>u.urls.filter(x=>x.includes('queue_status=1')).length;
  await q.open();q.timer()();await q.delay();
  assert.equal(count(q),0);assert.equal(q.events.length,0);
  await q.ownerEvent('startup');await q.ownerEvent('connection','connecting');
  q.timer()();await q.delay();
  assert.equal(count(q),0);assert.equal(q.events.length,0);
  await q.ownerEvent('connection','registered');
  assert.equal(count(q),0,'registration alone does not drop the epoch fence');
  await q.ownerEvent('snapshot',{epoch,queue:true});
  assert.equal(count(q),1);assert.equal(q.events.length,0);
  q.timer()();await q.delay();assert.equal(count(q),1);

  const offline=harness({startupPacked:true});
  await offline.open();offline.timer()();await offline.delay();
  assert.equal(count(offline),0);assert.equal(offline.events.length,0);
  await offline.ownerEvent('offline');
  assert.equal(count(offline),1);assert.equal(offline.events.length,1);
});

test('packed and native Graph load a validated owner snapshot without waiting for Live registration',async()=>{
  const {ownerLiveMethods}=await import('./visual-overlay.mjs');
  const {firstOwnerSnapshot}=await import('../../apps/graph_viewer/web/src/owner-live-controller.mjs');
  const native=await readFile(new URL('../../apps/graph_viewer/web/src/main.ts',import.meta.url),'utf8');
  for(const [source,first,next,ownerBoot,ownerStop,ownerScene,ownerOpen] of [
    [ownerLiveMethods,'bootstrapReleaseOwnerGraph(){','\nstopReleaseOwnerLive()', 'bootstrapReleaseOwnerGraph','stopReleaseOwnerLive','ensureReleaseScene','startReleaseOwnerLive'],
    [native,'private async bootstrapOwnerGraph():Promise<void>{','\n  private startDirectOwnerLive', 'bootstrapOwnerGraph','stopOwnerLive','ensureLiveScene','startDirectOwnerLive'],
  ]){
    const start=source.indexOf(first), end=source.indexOf(next,start);
    assert.ok(start>=0&&end>start);
    const body=source.slice(start+first.length,end).trimEnd().slice(0,-1);
    const boot=runInNewContext('(async function(){'+body+'})',{firstOwnerSnapshot,releaseFirstOwnerSnapshot:firstOwnerSnapshot,window:{dispatchEvent(){}},dispatchEvent(){},CustomEvent:class{}});
    const calls=[];
    const app={authenticated:true,[ownerStop](){calls.push('stop');},[ownerOpen](){assert.fail('Live must follow, not gate, the first accepted snapshot');},
      [ownerScene](){calls.push('scene');},setConnection(){calls.push('snapshot badge');},
      async loadGraph(){calls.push('snapshot');}};
    await boot.call(app);
    assert.deepEqual(calls,['stop','snapshot badge','scene','snapshot'],ownerBoot);
  }
});
