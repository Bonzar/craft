;(() => {
  const panel=document.querySelector('#visual-queue');
  if(!panel)return;
  const launcher=document.querySelector('#visual-queue-open');
  const status=panel.querySelector('.queue-meta');
  const container=panel.querySelector('.queue-items');
  const copyStatus=panel.querySelector('.queue-copy-status');
  const controlPanel=panel.querySelector('#queue-processing');
  const controlButton=panel.querySelector('#queue-processing-toggle');
  const controlState=panel.querySelector('#queue-processing-state');
  const controlFeedback=panel.querySelector('#queue-processing-feedback');
  const names={source:'Реплики',maintenance:'Связи и пересчёт',interactive:'Вопросы'};
  const ordered=['interactive','source','maintenance'];
  const states={running:'В работе',ready:'Ожидает',waiting_origin:'Ждёт источник',delayed:'Отложено',paused:'Приостановлено',expired:'Исчерпаны попытки',active:'Активна',unavailable:'Недоступна',disabled:'Выключена'};
  const phases={extract:'Извлечение',audit:'Проверка',link:'Связывание',refresh:'Перепроверка вывода',consolidate:'Поиск связей',partial_source:'Частично сохранённый источник',query:'Ответ на вопрос'};
  let stream,timer,loading=false,dirty=false,generation=0,lastSignal=0, fetchAbort;
  let control, ownerReady=false, ownerCheck, inFlight=false, controlEpoch=0, nativeReady=false, probedRevision=null;
  const rev=value=>typeof value==='string'&&/^[0-9a-f]{32}$/.test(value);
  const snapshotControl=(stage,value)=>['test','prod'].includes(stage)&&value&&
    typeof value.paused==='boolean'&&rev(value.revision)&&typeof value.can_change==='boolean'
    ? {stage,paused:value.paused,revision:value.revision,canChange:value.can_change}:null;
  const offsets=new Map(), rendered=new Map(), lanes=new Map();
  const element=(tag,txt='',name)=>{const e=document.createElement(tag);if(name)e.className=name;if(txt)e.textContent=txt;return e;};
  const labels=(values,k,fallback)=>Object.hasOwn(values,k)?values[k]:fallback;
  const action=(title,fn)=>{const e=element('button',title);e.type='button';e.addEventListener('click',fn);return e;};
  const shorten=id=>id.length>19?id.slice(0,9)+'…'+id.slice(-6):id;
  const move=(id,n)=>{offsets.set(id,n);void refresh();};
  function card(item,previous){
    const root=element('article','','queue-card');
    const row=element('div','','queue-card-top');
    const chip=element('span',labels(states,item.status,'Состояние неизвестно'),'queue-chip');
    if(Object.hasOwn(states,item.status))chip.dataset.status=item.status;
    row.append(chip);
    if(Number.isSafeInteger(item.position)&&item.position>0&&item.status!=='running'){
      row.append(element('span','№ '+item.position,'queue-position'));
    }
    const title=element('h3',labels(phases,item.phase,'Этап не указан'));
    const detail=element('details');
    detail.open=Boolean(previous?.details.open);
    const summary=element('summary','ID '+shorten(item.id));
    const raw=element('code',item.id);
    const copy=action('Скопировать ID',async()=>{
      try {await navigator.clipboard.writeText(item.id);copyStatus.textContent='ID скопирован.';}
      catch {copyStatus.textContent='Копирование недоступно. ID можно выделить в карточке.';}
    });
    detail.append(summary,raw,copy);
    root.append(row,title,detail);
    return {root,details:detail,summary,copy};
  }
  function render(id,queue,offset){
    let target=lanes.get(id);
    if(!target){
      target=element('section','','queue-lane');target.setAttribute('aria-label',names[id]);
      lanes.set(id,target);container.append(target);
    }
    const signature=JSON.stringify([queue,offset]);
    if(rendered.get(id)?.signature===signature)return;
    const previous=rendered.get(id)?.cards;
    const active=document.activeElement;
    let restored;
    const cards=new Map();
    const heading=element('div','','queue-lane-head');
    heading.append(element('h2',names[id]));
    if(Number.isSafeInteger(queue?.total)&&queue.total>=0)heading.append(element('span',String(queue.total),'queue-total'));
    const label=labels(states,queue?.status,'Недоступна');
    const state=element('p',label,'queue-lane-status');
    const grid=element('div','','queue-cards');
    const items=Array.isArray(queue?.items)?queue.items.filter(x=>x&&typeof x.id==='string'&&x.id):[];
    for(const item of items){
      const before=previous?.get(item.id),view=card(item,before);
      if(before?.summary===active)restored=view.summary;
      if(before?.copy===active)restored=view.copy;
      cards.set(item.id,view);grid.append(view.root);
    }
    if(!items.length){
      const genuineEmpty=queue?.status==='active'&&queue.total===0;
      grid.append(element('p',genuineEmpty?'Нет задач.':queue?.status==='active'?'На этой странице нет задач.':'Нет данных о задачах.','queue-empty'));
    }
    const nav=element('div','','queue-nav');
    if(offset>0)nav.append(action('К началу',()=>move(id,0)));
    if(Number.isSafeInteger(queue?.next_offset)&&queue.next_offset>offset)nav.append(action('Ещё задачи',()=>move(id,queue.next_offset)));
    target.replaceChildren(heading,state,grid,nav);
    if(restored)restored.focus();
    rendered.set(id,{signature,cards});
  }
  function drawControl(){
    controlPanel.hidden=!control;
    controlButton.hidden=!(control?.canChange&&ownerReady||control?.stage==='test'&&nativeReady);
    controlButton.disabled=controlButton.hidden||inFlight;
    if(!control)return;
    const stage=control.stage.toUpperCase();
    controlState.textContent=stage+' · '+(control.paused?'Обработка на паузе':'Обработка не на паузе');
    controlButton.textContent=control.paused?'Возобновить обработку':'Приостановить обработку';
  }
  async function csrf(){
    const response=await fetch('/api/v1/session',{cache:'no-store',credentials:'same-origin',redirect:'error',
      headers:{accept:'application/json'}});
    if(response.status!==200||!response.headers.get('content-type')?.includes('application/json'))return null;
    const value=await response.json();
    return value?.authenticated===true&&typeof value.csrf_token==='string'&&
      /^[A-Za-z0-9_-]{20,160}$/.test(value.csrf_token)?value.csrf_token:null;
  }
  async function prepareNative(before){
    if(before.stage!=='test')return null;
    const response=await fetch('/api/v1/processing/pause/prepare',{
      method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',
      headers:{accept:'application/json','content-type':'application/json'},body:'{}'
    });
    if(response.status!==200||!response.headers.get('content-type')?.includes('application/json'))return null;
    const value=await response.json();
    return value?.stage==='test'&&value.paused===before.paused&&value.revision===before.revision&&
      typeof value.csrf_token==='string'&&/^[0-9]{1,12}\.[0-9a-f]{64}$/.test(value.csrf_token)?value.csrf_token:null;
  }
  function acceptControl(data){
    const current=generation, lane=data?.stage;
    const next=snapshotControl(lane,data?.processing_control);
    if(control?.stage!==next?.stage||!next?.canChange){ownerReady=false;ownerCheck=null;}
    if(control?.revision!==next?.revision||next?.stage!=='test'||next?.canChange)nativeReady=false;
    control=next;
    drawControl();
    if(next?.stage==='test'&&!next.canChange&&probedRevision!==next.revision){
      probedRevision=next.revision;
      void prepareNative(next).then(token=>{
        if(generation===current&&panel.open&&control?.revision===next.revision&&!control.canChange){
          nativeReady=Boolean(token);drawControl();
        }
      }).catch(()=>{});
    }
    if(next?.canChange&&!ownerReady&&!ownerCheck){
      const check=csrf().then(token=>{
        if(generation===current&&panel.open&&control?.stage===lane&&control.canChange){
          ownerReady=Boolean(token);drawControl();
        }
      }).catch(()=>{}).finally(()=>{if(ownerCheck===check)ownerCheck=null;});
      ownerCheck=check;
    }
  }
  controlButton.addEventListener('click',async()=>{
    if(!panel.open||inFlight||!(control?.canChange&&ownerReady||control?.stage==='test'&&nativeReady))return;
    const current=generation, before=control, next=!before.paused;
    inFlight=true;controlEpoch++;drawControl();
    controlFeedback.textContent='Сохраняем настройку…';
    try{
      const native=!before.canChange;
      const token=await (native?prepareNative(before):csrf());
      if(generation!==current||!panel.open)return;
      if(!token){ownerReady=false;nativeReady=false;throw Error();}
      if(control?.revision!==before.revision||control?.stage!==before.stage||control?.canChange!==before.canChange){
        controlFeedback.textContent='Состояние изменилось. Проверьте его перед повтором.';
        return;
      }
      const result=await fetch('/api/v1/processing/pause',{
        method:'PUT',credentials:'same-origin',redirect:'error',cache:'no-store',
        headers:{accept:'application/json','content-type':'application/json','x-csrf-token':token},
        body:JSON.stringify({stage:before.stage,paused:next,expected_revision:before.revision})
      });
      if(generation!==current||!panel.open)return;
      if(result.status===409){
        ownerReady=false;nativeReady=false;
        controlFeedback.textContent='Состояние изменилось. Загружаем актуальное.';
        return;
      }
      if(result.status===401||result.status===403){ownerReady=false;nativeReady=false;}
      if(result.status!==200||!result.headers.get('content-type')?.includes('application/json'))throw Error();
      const value=await result.json();
      if(generation!==current||!panel.open)return;
      if(value?.stage!==before.stage||value.paused!==next||!rev(value.revision)||value.revision===before.revision)throw Error();
      control={stage:value.stage,paused:value.paused,revision:value.revision,canChange:before.canChange};
      controlFeedback.textContent=value.paused?'Пауза включена.':'Пауза снята.';
    }catch{
      if(generation===current&&panel.open){
        ownerReady=false;nativeReady=false;
        controlFeedback.textContent='Не удалось подтвердить изменение. Проверяем актуальное состояние.';
      }
    }finally{
      inFlight=false;controlEpoch++;
      if(panel.open){drawControl();void refresh();}
    }
  });
  async function snapshot(offset,signal){
    const result=await fetch('/api/v1/capabilities?queue_status=1&limit=50&offset='+offset,
      {cache:'no-store',credentials:'same-origin',redirect:'error',signal,headers:{accept:'application/json'}});
    if(!result.ok||!result.headers.get('content-type')?.includes('application/json'))throw Error();
    const data=await result.json();
    if(!['test','prod'].includes(data.stage)||!Array.isArray(data.queues))throw Error();
    return data;
  }
  async function refresh(){
    if(!panel.open)return;
    if(loading){dirty=true;return;}
    loading=true;
    const current=generation, requested=new Map(offsets), epoch=controlEpoch;
    const abort=new AbortController();
    fetchAbort=abort;
    try{
      const pages=new Map(await Promise.all([...new Set(ordered.map(k=>requested.get(k)??0))].map(async n=>[n,await snapshot(n,abort.signal)])));
      if(!panel.open||current!==generation)return;
      const base=pages.values().next().value;
      if([...pages.values()].some(x=>x.stage!==base.stage||x.release_version!==base.release_version))throw Error();
      const version=typeof base.release_version==='string'&&base.release_version?' · База '+base.release_version:'';
      const label=base.stage.toUpperCase()+version;
      if(status.textContent!==label)status.textContent=label;
      if(!inFlight&&epoch===controlEpoch)acceptControl(base);
      for(const id of ordered){
        const offset=requested.get(id)??0;
        if((offsets.get(id)??0)===offset)render(id,pages.get(offset).queues.find(q=>q?.id===id),offset);
      }
    }catch{
      if(panel.open&&current===generation){
        const label=rendered.size?'Не удалось обновить. Показаны последние полученные данные.':'Не удалось загрузить очереди.';
        if(status.textContent!==label)status.textContent=label;
        if(!inFlight&&epoch===controlEpoch)acceptControl(null);
      }
    }finally{
      loading=false;fetchAbort=null;
      if(dirty){dirty=false;void refresh();}
    }
  }
  function stop(){
    generation++;dirty=false;fetchAbort?.abort();stream?.close();stream=null;
    if(timer)clearInterval(timer);timer=null;
  }
  launcher.addEventListener('click',()=>{
    if(panel.open)return;
    panel.showModal();
    offsets.clear();rendered.clear();lanes.clear();container.replaceChildren();
    status.textContent='Подключаемся…';lastSignal=0;
    control=null;ownerReady=false;ownerCheck=null;nativeReady=false;probedRevision=null;controlFeedback.textContent='';drawControl();
    if(typeof EventSource!=='undefined'){
      stream=new EventSource('/api/v1/events?channel=dashboard',{withCredentials:true});
      const received=()=>{lastSignal=Date.now();void refresh();};
      stream.addEventListener('resync',received);
      stream.addEventListener('queue_changed',received);
    }
    timer=setInterval(()=>{if(Date.now()-lastSignal>2500)void refresh();},3000);
    void refresh();
  });
  panel.querySelector('#visual-queue-close').addEventListener('click',()=>panel.close());
  panel.addEventListener('close',()=>{stop();launcher.focus();});
  addEventListener('pagehide',stop,{once:true});
})();
