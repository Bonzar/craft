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
  const sessionsPanel=panel.querySelector('#queue-session-policy');
  const sessionOptions=panel.querySelector('#queue-session-options');
  const sessionHint=panel.querySelector('#queue-session-hint');
  const sessionFeedback=panel.querySelector('#queue-session-feedback');
  const sessionSave=panel.querySelector('#queue-session-save');
  const itemFeedback=panel.querySelector('#queue-item-feedback');
  const deleting=new Set(), previews=new Map(), previewPending=new Set(), previewJobs=new Map();
  const autoSeen=new Set(), visiblePreviews=new Map();
  let previewObserver,previewEpoch=0;
  const maxAutoPreviews=6;
  function drainPreviews(){
    if(previewPending.size||!panel.open)return;
    while(visiblePreviews.size&&autoSeen.size<maxAutoPreviews){
      const [target,entry]=visiblePreviews.entries().next().value;
      visiblePreviews.delete(target);
      if(target.isConnected===false||previewJobs.get(target)!==entry||autoSeen.has(entry.key)||previews.has(entry.key))continue;
      autoSeen.add(entry.key);void entry.run();return;
    }
  }
  function watchPreview(target){
    if(typeof IntersectionObserver==='undefined')return;
    previewObserver??=new IntersectionObserver(entries=>{
      for(const e of entries){
        const job=previewJobs.get(e.target);
        if(e.isIntersecting&&job){visiblePreviews.set(e.target,job);}
        else visiblePreviews.delete(e.target);
      }
      drainPreviews();
    },{threshold:0.15});
    previewObserver.observe(target);
  }
  const hold={global_pause:'Глобальная пауза',session_excluded:'Сессия не выбрана для автообработки',incomplete_source:'Ожидает полного источника'};
  let sessionsState, policyDraft, sessionsDraft=new Set(), sessionsBusy=false, sessionsProbe=false, nativeProbeTried=false;
  let controlsReady=false, queueStage;
  const names={source:'Реплики',maintenance:'Связи и пересчёт',interactive:'Вопросы'};
  const ordered=['interactive','source','maintenance'];
  const states={running:'В работе',ready:'Ожидает',waiting_origin:'Ждёт источник',delayed:'Отложено',paused:'Приостановлено',expired:'Исчерпаны попытки',active:'Активна',unavailable:'Недоступна',disabled:'Выключена'};
  const phases={extract:'Извлечение',audit:'Проверка',link:'Связывание',refresh:'Перепроверка вывода',consolidate:'Поиск связей',partial_source:'Частично сохранённый источник',query:'Ответ на вопрос'};
  let stream,timer,loading=false,dirty=false,generation=0,lastSignal=0, fetchAbort;
  let control, ownerReady=false, ownerCheck, inFlight=false, controlEpoch=0, nativeReady=false, probedRevision=null;
  const rev=value=>typeof value==='string'&&/^[0-9a-f]{32}$/.test(value);
  const snapshotControl=(stage,value)=>['test','prod'].includes(stage)&&value&&
    typeof value.paused==='boolean'&&rev(value.revision)&&typeof value.can_change==='boolean'
    ? {stage,paused:value.paused,revision:value.revision,canChange:value.can_change,prepareAllowed:stage==='test'}:null;
  const offsets=new Map(), rendered=new Map(), lanes=new Map();
  const element=(tag,txt='',name)=>{const e=document.createElement(tag);if(name)e.className=name;if(txt)e.textContent=txt;return e;};
  const labels=(values,k,fallback)=>Object.hasOwn(values,k)?values[k]:fallback;
  const action=(title,fn)=>{const e=element('button',title);e.type='button';e.addEventListener('click',fn);return e;};
  const shorten=id=>id.length>19?id.slice(0,9)+'…'+id.slice(-6):id;
  const move=(id,n)=>{offsets.set(id,n);void refresh();};
  function card(item,previous,queueId){
    const root=element('article','','queue-card');
    const row=element('div','','queue-card-top');
    const chip=element('span',labels(states,item.status,'Состояние неизвестно'),'queue-chip');
    if(Object.hasOwn(states,item.status))chip.dataset.status=item.status;
    row.append(chip);
    if(Number.isSafeInteger(item.position)&&item.position>0&&item.status!=='running'){
      row.append(element('span','№ '+item.position,'queue-position'));
    }
    const members=queueId==='source'&&Array.isArray(item.members)?item.members:null;
    const title=element('h3',members?'Пакет реплик · '+members.length:labels(phases,item.phase,'Этап не указан'));
    const detail=element('details');
    detail.open=Boolean(previous?.details.open);
    const summary=element('summary','ID '+shorten(item.id));
    const raw=element('code',item.id);
    const copy=action('Скопировать ID',async()=>{
      try {await navigator.clipboard.writeText(item.id);copyStatus.textContent='ID скопирован.';}
      catch {copyStatus.textContent='Копирование недоступно. ID можно выделить в карточке.';}
    });
    detail.append(summary,raw,copy);
    let cardAction=null;
    if(controlsReady&&itemFeedback&&['source','maintenance'].includes(queueId)&&rev(item.remove_revision)&&item.status!=='running'&&(queueId!=='source'||item.phase==='extract')){
      const change=element('div','','queue-remove-action');
      const key=queueId+':'+item.id;
      const source=queueId==='source';
      const effect=source?'source_and_queue':'queue_only';
      const begin=action(members?'Удалить пакет из очереди':source?'Удалить реплику и задачу':'Удалить из очереди',()=>{
        if(deleting.has(key))return;
        const commit=action('Подтвердить удаление',async()=>{
          if(deleting.has(key)||!panel.open)return;
          const current=generation;deleting.add(key);commit.disabled=true;
          itemFeedback.textContent='Удаляем задачу из очереди…';
          const body={stage:queueStage,queue_id:queueId,item_id:item.id,expected_revision:item.remove_revision};
          const route='/api/v1/processing/items/remove';
          try{
            const proof=await sessionRequest(route+'/prepare','POST',body);
            if(current!==generation||!panel.open)return;
            if(proof?.stage!==body.stage||typeof proof.csrf_token!=='string'||!/^[0-9]{1,12}\.[0-9a-f]{64}$/.test(proof.csrf_token))throw Error();
            const value=await sessionRequest(route,'PUT',body,proof.csrf_token);
            if(current!==generation||!panel.open)return;
            if(!value){
              itemFeedback.textContent='Задача уже изменилась, выполняется или отсутствует. Обновляем очередь.';
              return;
            }
            if(value.stage!==body.stage||value.queue_id!==queueId||value.item_id!==item.id||value.removed!==true||value.effect!==effect)throw Error();
            itemFeedback.textContent=members?'Пакет и его реплики удалены из базы памяти. Исходный чат не изменён.':source?'Задача и сохранённая реплика удалены из базы. Исходный чат не изменён.':'Задача удалена из очереди. Реплики и граф не изменены.';
          }catch{if(current===generation&&panel.open)itemFeedback.textContent='Удаление не подтверждено. Проверьте обновлённую очередь.';}
          finally{
            deleting.delete(key);
            if(current===generation&&panel.open){change.replaceChildren(begin);void refresh();}
          }
        });
        const cancel=action('Отмена',()=>{change.replaceChildren(begin);begin.focus();});
        change.replaceChildren(element('p',members?'Удалить весь пакет и все его реплики из базы памяти? Сообщения в исходном чате останутся.':source?'Удалить задачу и реплику из базы памяти? Сообщение в исходном чате останется.':'Удалить только задачу из очереди? Реплики и граф останутся.'),commit,cancel);
        commit.focus();
      });
      change.append(begin);cardAction=change;
    }else if(!members&&controlsReady&&queueId==='source'&&(item.phase==='partial_source'||item.status==='running')){
      cardAction=element('p','Удаление недоступно: обработка реплики уже началась.','queue-lane-status');
    }
    root.append(row,title);
    function sourceExcerpt(source,auto){
      const excerpt=element('p','','queue-source-preview');
      const previewKey=JSON.stringify([source.id,source.preview_revision]);
      const show=value=>{
        const role=labels({user:'Ты',assistant:'Ассистент',tool:'Инструмент',system:'Система',developer:'Разработчик'},value.source_role,'');
        excerpt.replaceChildren(...(role?[element('strong',role+': ')]:[]),element('span',value.source_preview));
      };
      const load=async()=>{
        if(previewPending.size||previewPending.has(previewKey)||!panel.open)return;
        const current=generation,epoch=previewEpoch;
        const body={stage:queueStage,queue_id:'source',item_id:source.id,expected_revision:source.preview_revision};
        const route='/api/v1/processing/items/source-preview';
        previewPending.add(previewKey);open.disabled=true;
        try{
          const proof=await sessionRequest(route+'/prepare','POST',body);
          if(current!==generation||epoch!==previewEpoch||!panel.open)return;
          if(proof?.stage!==body.stage||typeof proof.csrf_token!=='string'||!/^[0-9]{1,12}\.[0-9a-f]{64}$/.test(proof.csrf_token))throw Error();
          const value=await sessionRequest(route,'PUT',body,proof.csrf_token);
          if(current!==generation||epoch!==previewEpoch||!panel.open)return;
          if(value?.stage!==body.stage||value.queue_id!=='source'||value.item_id!==source.id||
             typeof value.source_preview!=='string'||!value.source_preview.trim()||Array.from(value.source_preview).length>240)throw Error();
          if(previews.size>=50)previews.delete(previews.keys().next().value);
          previews.set(previewKey,value);show(value);
        }catch{
          if(current===generation&&epoch===previewEpoch&&panel.open){
            excerpt.replaceChildren(element('span','Текст недоступен. '),open);
          }
        }finally{previewPending.delete(previewKey);open.disabled=false;drainPreviews();}
      };
      const open=action('Показать текст реплики',load);
      if(previews.has(previewKey))show(previews.get(previewKey));else{
        excerpt.append(open);
        if(auto){previewJobs.set(root,{key:previewKey,run:load});watchPreview(root);}
      }
      return excerpt;
    }
    let group;
    if(members){
      group=element('details','','queue-batch-members');
      group.open=Boolean(previous?.members?.open);
      group.append(element('summary','Реплики в пакете'));
      const list=element('ol');
      for(const member of members){
        const entry=element('li');
        entry.append(controlsReady&&rev(member.preview_revision)?sourceExcerpt(member,false)
          :element('span','Текст недоступен'));
        list.append(entry);
      }
      group.append(list);
      root.append(element('p',labels(phases,item.phase,'Этап не указан'),'queue-lane-status'),group);
    }else if(controlsReady&&queueId==='source'&&rev(item.preview_revision))root.append(sourceExcerpt(item,true));
    if(Object.hasOwn(hold,item.hold_reason))root.append(element('p',hold[item.hold_reason],'queue-lane-status'));
    if(typeof item.session_id==='string'&&item.session_id)detail.append(element('p','Сессия: '+item.session_id));
    if(cardAction)root.append(cardAction);
    root.append(detail);
    return {root,details:detail,summary,copy,members:group};
  }
  function render(id,queue,offset){
    let target=lanes.get(id);
    if(!target){
      target=element('section','','queue-lane');target.setAttribute('aria-label',names[id]);
      lanes.set(id,target);container.append(target);
    }
    const signature=JSON.stringify([queue,offset,controlsReady]);
    if(rendered.get(id)?.signature===signature)return;
    const previous=rendered.get(id)?.cards;
    for(const view of previous?.values()??[]){
      previewObserver?.unobserve(view.root);previewJobs.delete(view.root);visiblePreviews.delete(view.root);
    }
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
      const before=previous?.get(item.id),view=card(item,before,id);
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
    if(id==='source'&&Number.isSafeInteger(queue?.source_count)&&queue.source_count!==queue.total){
      heading.append(element('span','Реплик: '+queue.source_count,'queue-lane-status'));
    }
    target.replaceChildren(heading,state,grid,nav);
    if(restored)restored.focus();
    rendered.set(id,{signature,cards});
  }
  const sessionUrl='/api/v1/processing/sessions';
  function parseSessions(value){
    if(value?.stage!==queueStage||!rev(value.revision)||!Array.isArray(value.sessions)||!Array.isArray(value.selected_session_ids)||
       value.selected_session_ids.length>50||!value.selected_session_ids.every(id=>typeof id==='string')||
       !value.sessions.every(x=>x&&typeof x.id==='string'&&Number.isSafeInteger(x.queued)&&x.queued>=0&&typeof x.eligible==='boolean'))throw Error();
    return value;
  }
  const policyPrepare={format:'policy-v1'};
  function parsePolicy(value){
    const p=value?.policy;
    parseSessions({...value,selected_session_ids:[]});
    const ids=x=>Array.isArray(x)&&x.length<=50&&x.every(id=>typeof id==='string')&&new Set(x).size===x.length;
    const date=x=>x===null || (typeof x==='string'&&/^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$/.test(x)&&Number.isFinite(Date.parse(x)));
    if(!p||!['all','include'].includes(p.mode)||!ids(p.included_session_ids)||!ids(p.excluded_session_ids)||
       (p.mode==='all'&&p.included_session_ids.length)||p.included_session_ids.some(x=>p.excluded_session_ids.includes(x))||
       !date(p.added_from)||!date(p.added_through))throw Error();
    return value;
  }
  function samePolicy(){
    const original=sessionsState?.policy;
    return original&&['mode','added_from','added_through'].every(k=>original[k]===policyDraft[k])&&
      ['included_session_ids','excluded_session_ids'].every(k=>original[k].length===policyDraft[k].length&&original[k].every(id=>policyDraft[k].includes(id)));
  }
  function drawPolicy(){
    const p=policyDraft;
    const summary=element('div','','queue-policy-fields');
    const modeLabel=element('label','Обрабатывать');
    const mode=element('select');mode.value=p.mode;mode.disabled=sessionsBusy;
    for(const [value,label] of [['all','Все сессии, кроме исключённых'],['include','Только выбранные сессии']]){
      const option=element('option',label);option.value=value;mode.append(option);
    }
    mode.value=p.mode;
    mode.addEventListener('change',()=>{
      p.mode=mode.value;
      if(p.mode==='all')p.included_session_ids=[];
      drawPolicy();
    });
    modeLabel.append(mode);summary.append(modeLabel);
    for(const [key,label,time] of [['added_from','Дата поступления с (UTC)','T00:00:00Z'],
                                  ['added_through','Дата поступления по (UTC)','T23:59:59.999999Z']]){
      const labelNode=element('label',label);
      const date=element('input');date.type='date';date.value=p[key]?.slice(0,10)??'';date.disabled=sessionsBusy;
      date.addEventListener('change',()=>{
        p[key]=date.value?date.value+time:null;
        sessionSave.disabled=sessionsBusy||samePolicy()||invalidRange();
        if(invalidRange())sessionFeedback.textContent='Конечная дата должна быть не раньше начальной.';
        else sessionFeedback.textContent='';
      });
      labelNode.append(date);summary.append(labelNode);
    }
    const listMode=p.mode==='all'?'excluded_session_ids':'included_session_ids';
    const chosen=new Set(p[listMode]), present=new Set();
    const rows=[...sessionsState.sessions];
    for(const row of rows)present.add(row.id);
    for(const id of chosen)if(!present.has(id))rows.push({id,queued:0,eligible:false});
    sessionOptions.replaceChildren(summary,element('p',p.mode==='all'?'Не обрабатывать отмеченные сессии.':'Обрабатывать только отмеченные сессии.'));
    for(const item of rows){
      const row=element('label','','queue-session-choice');
      const box=element('input');box.type='checkbox';box.checked=chosen.has(item.id);box.disabled=sessionsBusy||(!item.eligible&&!box.checked);
      box.addEventListener('change',()=>{
        const set=new Set(p[listMode]);
        if(box.checked&&set.size>=50){
          box.checked=false;sessionFeedback.textContent='Можно указать не больше 50 сессий.';return;
        }
        if(box.checked){
          set.add(item.id);
          if(p.mode==='include')p.excluded_session_ids=p.excluded_session_ids.filter(id=>id!==item.id);
        }else set.delete(item.id);
        p[listMode]=[...set];
        sessionSave.disabled=sessionsBusy||samePolicy()||invalidRange();
      });
      row.append(box,element('span',item.id+' · задач: '+item.queued));
      sessionOptions.append(row);
    }
    if(!rows.length)sessionOptions.append(element('p','Сессий с задачами пока нет.'));
    sessionSave.disabled=sessionsBusy||samePolicy()||invalidRange();
  }
  const invalidRange=()=>Boolean(policyDraft?.added_from&&policyDraft.added_through&&
    Date.parse(policyDraft.added_from)>Date.parse(policyDraft.added_through));
  function drawSessions(){
    if(!sessionsPanel)return;
    if(policyDraft)return drawPolicy();
    const rows=[...(sessionsState?.sessions??[])];
    const present=new Set(rows.map(x=>x.id));
    for(const id of sessionsDraft)if(!present.has(id))rows.push({id,queued:0,eligible:false});
    sessionOptions.replaceChildren();
    for(const item of rows){
      const label=element('label','','queue-session-choice');
      const box=element('input');box.type='checkbox';box.checked=sessionsDraft.has(item.id);
      box.disabled=sessionsBusy||(!item.eligible&&!box.checked);
      box.addEventListener('change',()=>{
        if(box.checked&&sessionsDraft.size>=50){
          box.checked=false;sessionFeedback.textContent='Можно выбрать не больше 50 сессий.';return;
        }
        if(box.checked)sessionsDraft.add(item.id);else sessionsDraft.delete(item.id);
        sessionSave.disabled=sessionsBusy||sameSessions();
      });
      label.append(box,element('span',item.id+' · задач: '+item.queued+(!item.eligible?' · новых подходящих задач нет':'')));
      sessionOptions.append(label);
    }
    if(!rows.length)sessionOptions.append(element('p','Сессий с задачами пока нет.'));
    sessionSave.disabled=sessionsBusy||!sessionsState||sameSessions();
  }
  const sameSessions=()=>policyDraft?samePolicy():sessionsState?.selected_session_ids.length===sessionsDraft.size&&
    sessionsState.selected_session_ids.every(x=>sessionsDraft.has(x));
  function useSessions(value){
    sessionsState=value?.policy?parsePolicy(value):parseSessions(value);
    policyDraft=value?.policy?{...value.policy,included_session_ids:[...value.policy.included_session_ids],
      excluded_session_ids:[...value.policy.excluded_session_ids]}:null;
    sessionsDraft=new Set(value.selected_session_ids??[]);
    if(sessionHint)sessionHint.textContent=policyDraft
      ?'Эти настройки определяют, какие новые задачи запускаются автоматически. Это не фильтр списка на экране. Даты включительны, в UTC.'
      :'Эта настройка меняет автообработку, а не только список на экране. Пустой выбор останавливает автоматический запуск новых задач реплик.';
    drawSessions();
  }
  async function sessionRequest(path,method,body,token,allowLegacy=false){
    const response=await fetch(path,{method,credentials:'same-origin',redirect:'error',cache:'no-store',
      headers:{accept:'application/json','content-type':'application/json',...(token?{'x-csrf-token':token}:{})},
      body:JSON.stringify(body)});
    if(response.status===409||allowLegacy&&[400,404].includes(response.status))return null;
    if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw Error();
    return response.json();
  }
  const clearPanel=panel.querySelector('#queue-database-clear');
  const clearOpen=panel.querySelector('#queue-clear-open');
  const clearConfirmation=panel.querySelector('#queue-clear-confirmation');
  const clearInstance=panel.querySelector('#queue-clear-instance');
  const clearLabel=panel.querySelector('#queue-clear-label');
  const clearTyped=panel.querySelector('#queue-clear-typed');
  const clearCommit=panel.querySelector('#queue-clear-commit');
  const clearCancel=panel.querySelector('#queue-clear-cancel');
  const clearFeedback=panel.querySelector('#queue-clear-feedback');
  const clearUrl='/api/v1/processing/database/clear-v2';
  let clearState=null, clearIntent=null, clearBusy=false;
  const sameClear=(a,b)=>Boolean(a&&b&&a.stage===b.stage&&b.canClear);
  const rearmText=status=>({
    waiting:'Ожидаем повторного подтверждения захвата от устройства.',
    confirmed:'Получено подтверждение повторного захвата от устройства.',
    unknown:'Повторный захват пока не подтверждён.'
  })[status]||'';
  function drawClear(){
    if(!clearPanel)return;
    clearPanel.hidden=!clearState&&!clearBusy;
    clearOpen.hidden=Boolean(clearIntent)||clearBusy;
    clearOpen.disabled=clearBusy||clearState?.canClear!==true;
    clearConfirmation.hidden=!clearIntent;
    if(clearState)clearInstance.textContent='Текущее окружение: '+clearState.stage.toUpperCase()+
      (clearState.canClear?'.':'. Очистка сейчас недоступна.')+' '+rearmText(clearState.captureRearm);
    clearTyped.disabled=clearBusy;
    clearCancel.disabled=clearBusy;
    clearCommit.disabled=clearBusy||!clearIntent||clearTyped.value!==clearIntent.phrase;
  }
  function closeClear(focus=false){
    clearIntent=null;
    if(clearTyped)clearTyped.value='';
    drawClear();
    if(focus&&!clearOpen?.disabled)clearOpen?.focus();
  }
  function acceptClear(data){
    if(!clearPanel)return;
    const item=data?.database_clear;
    const legacy=data?.owner_database_clear_csrf;
    const hasLegacy=data!=null&&Object.prototype.hasOwnProperty.call(data,'owner_database_clear_csrf');
    const acceptsLegacy=data?.stage==='prod'&&typeof legacy==='string'&&/^[0-9]{1,12}\.[0-9a-f]{64}$/.test(legacy);
    clearState=['test','prod'].includes(data?.stage)&&data?.capabilities?.owner_database_clear_post===true
      &&(!hasLegacy||acceptsLegacy)
      ? {stage:data.stage,canClear:true,captureRearm:item?.capture_rearm,
          legacyToken:acceptsLegacy?legacy:null}:null;
    if(!clearBusy&&clearIntent&&!sameClear(clearIntent,clearState)){
      clearFeedback.textContent='Состояние базы изменилось. Откройте подтверждение заново.';
      closeClear(true);
    }else drawClear();
  }
  clearOpen?.addEventListener('click',()=>{
    if(!panel.open||clearBusy||!clearState?.canClear||clearIntent)return;
    clearIntent={stage:clearState.stage,phrase:'ОЧИСТИТЬ '+clearState.stage.toUpperCase()};
    clearLabel.textContent='Введите «'+clearIntent.phrase+'» для подтверждения';
    clearFeedback.textContent='';drawClear();clearTyped.focus();
  });
  clearTyped?.addEventListener('input',drawClear);
  clearCancel?.addEventListener('click',()=>{
    if(clearBusy)return;
    clearFeedback.textContent='Очистка отменена.';
    closeClear(true);
  });
  clearCommit?.addEventListener('click',async()=>{
    const intent=clearIntent;
    if(clearBusy||!panel.open||!sameClear(intent,clearState)||clearTyped.value!==intent?.phrase)return;
    const current=generation;
    const active=()=>current===generation&&panel.open;
    let sent=false;
    clearBusy=true;
    clearFeedback.textContent='Проверяем состояние базы…';
    drawClear();
    const body={confirmation:intent.phrase};
    try{
      // Packed legacy PROD does not expose a Graph session CSRF. Only its
      // current owner-authorized queue snapshot may supply a clear token.
      const legacy=clearState.legacyToken!==null;
      const token=legacy?clearState.legacyToken:await csrf();
      if(!active()||clearIntent!==intent)return;
      if(!token||!sameClear(intent,clearState)||(clearState.legacyToken!==null)!==legacy){
        clearFeedback.textContent='Не удалось подтвердить доступ. Ничего не удалено.';
        return;
      }
      clearFeedback.textContent='Очищаем базу '+intent.stage.toUpperCase()+'…';
      sent=true;
      const receipt=await sessionRequest(clearUrl,legacy?'PUT':'POST',body,token);
      if(!active())return;
      if(!receipt){
        clearFeedback.textContent='База занята. Очистка не выполнена.';
        return;
      }
      if(receipt.stage!==intent.stage||receipt.cleared!==true)throw Error();
      previewEpoch++;previews.clear();previewPending.clear();autoSeen.clear();visiblePreviews.clear();
      previewObserver?.disconnect();previewObserver=null;previewJobs.clear();
      fetchAbort?.abort();
      offsets.clear();rendered.clear();lanes.clear();container.replaceChildren();
      clearFeedback.textContent='База '+intent.stage.toUpperCase()+' очищена. Настройки и доступы сохранены. '+rearmText(receipt.capture_rearm);
      globalThis.location?.reload();
    }catch{
      if(active())clearFeedback.textContent=sent
        ? 'Результат очистки не подтверждён. Проверьте состояние базы перед повтором.'
        : 'Не удалось подтвердить состояние базы. Запрос на очистку не отправлен.';
    }finally{
      clearBusy=false;
      clearState=null;
      closeClear();
      if(panel.open){if(active())panel.querySelector('#visual-queue-close')?.focus();void refresh();}
    }
  });
  async function loadSessions(advertised=false){
    if(!sessionsPanel||sessionsBusy||sessionsProbe)return;
    sessionsProbe=true;
    const current=generation;
    try{
      const proposed=advertised?await sessionRequest(sessionUrl+'/prepare','POST',policyPrepare,undefined,true):null;
      if(current!==generation||!panel.open)return;
      const value=proposed??await sessionRequest(sessionUrl+'/prepare','POST',{});
      if(current!==generation||!panel.open)return;
      useSessions(value);sessionsPanel.hidden=false;
      if(!controlsReady){controlsReady=true;void refresh();}
    }catch{if(advertised&&current===generation&&panel.open){sessionsPanel.hidden=false;sessionFeedback.textContent='Настройка сессий недоступна. Очередь не изменена.';}}
    finally{if(current===generation)sessionsProbe=false;}
  }
  sessionsPanel?.addEventListener('toggle',()=>{if(sessionsPanel.open&&sameSessions())void loadSessions(controlsReady);});
  sessionSave?.addEventListener('click',async()=>{
    if(sessionsBusy||!sessionsState||sameSessions()||invalidRange())return;
    const current=generation,before=sessionsState;
    const command=policyDraft?{policy:{...policyDraft}}:{selected_session_ids:[...sessionsDraft]};
    const prepareBody=policyDraft?policyPrepare:{};
    const parse=policyDraft?parsePolicy:parseSessions;
    sessionsBusy=true;drawSessions();
    sessionFeedback.textContent='Сохраняем настройку…';
    try{
      const proof=parse(await sessionRequest(sessionUrl+'/prepare','POST',prepareBody));
      if(current!==generation||!panel.open)return;
      if(proof.revision!==before.revision){
        useSessions(proof);sessionFeedback.textContent='Выбор уже изменился. Проверьте новый список и выберите заново.';return;
      }
      if(typeof proof.csrf_token!=='string'||!/^[0-9]{1,12}\.[0-9a-f]{64}$/.test(proof.csrf_token))throw Error();
      const done=await sessionRequest(sessionUrl,'PUT',{stage:queueStage,...command,expected_revision:before.revision},proof.csrf_token);
      if(current!==generation||!panel.open)return;
      if(!done){
        const refreshed=await sessionRequest(sessionUrl+'/prepare','POST',prepareBody);
        if(current!==generation||!panel.open)return;
        useSessions(refreshed);
        sessionFeedback.textContent='Выбор уже изменился. Проверьте новый список и выберите заново.';return;
      }
      useSessions(done);
      sessionFeedback.textContent='Выбор сохранён.';
    }catch{if(current===generation&&panel.open)sessionFeedback.textContent='Сохранение не подтверждено. Проверьте список перед повтором.';}
    finally{if(current===generation&&panel.open){sessionsBusy=false;drawSessions();void refresh();}}
  });
  function drawControl(){
    controlPanel.hidden=!control;
    controlButton.hidden=!(control?.canChange&&ownerReady||control?.prepareAllowed&&nativeReady);
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
    if(!before.prepareAllowed)return null;
    const response=await fetch('/api/v1/processing/pause/prepare',{
      method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',
      headers:{accept:'application/json','content-type':'application/json'},body:'{}'
    });
    if(response.status!==200||!response.headers.get('content-type')?.includes('application/json'))return null;
    const value=await response.json();
    return value?.stage===before.stage&&value.paused===before.paused&&value.revision===before.revision&&
      typeof value.csrf_token==='string'&&/^[0-9]{1,12}\.[0-9a-f]{64}$/.test(value.csrf_token)?value.csrf_token:null;
  }
  function acceptControl(data){
    const current=generation, lane=data?.stage;
    const next=snapshotControl(lane,data?.processing_control);
    if(next&&data?.capabilities?.standalone_pause===true)next.prepareAllowed=true;
    if(control?.stage!==next?.stage||!next?.canChange){ownerReady=false;ownerCheck=null;}
    if(control?.stage!==next?.stage||control?.revision!==next?.revision||!next?.prepareAllowed||next?.canChange)nativeReady=false;
    control=next;
    drawControl();
    const probe=next?.prepareAllowed&&!next.canChange?next.stage+':'+next.revision:null;
    const startProbe=probe&&probe!==probedRevision;
    probedRevision=probe;
    if(startProbe){
      void prepareNative(next).then(token=>{
        if(generation===current&&panel.open&&control?.stage===next.stage&&control.revision===next.revision&&control.prepareAllowed&&!control.canChange){
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
    if(!panel.open||inFlight||!(control?.canChange&&ownerReady||control?.prepareAllowed&&nativeReady))return;
    const current=generation, before=control, next=!before.paused;
    inFlight=true;controlEpoch++;drawControl();
    controlFeedback.textContent='Сохраняем настройку…';
    try{
      const native=!before.canChange;
      const token=await (native?prepareNative(before):csrf());
      if(generation!==current||!panel.open)return;
      if(!token){ownerReady=false;nativeReady=false;throw Error();}
      if(control?.revision!==before.revision||control?.stage!==before.stage||control?.canChange!==before.canChange||control?.prepareAllowed!==before.prepareAllowed){
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
      control={stage:value.stage,paused:value.paused,revision:value.revision,canChange:before.canChange,prepareAllowed:before.prepareAllowed};
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
  let directQueueEpoch=null,directQueueLive=false,transportUp=false,primedQueue=null,primeAbort=null;
  let startupPending=typeof releaseFirstOwnerSnapshot==='function';
  function startQueueFallback(){
    if(startupPending||!panel.open||directQueueLive||stream||typeof EventSource==='undefined')return;
    stream=new EventSource('/api/v1/events?channel=dashboard',{withCredentials:true});
    const received=()=>{lastSignal=Date.now();void refresh();};
    stream.addEventListener('resync',received);
    stream.addEventListener('queue_changed',received);
  }
  addEventListener('owner-live-startup',()=>{
    startupPending=true;fetchAbort?.abort();stream?.close();stream=null;
    if(panel.open)status.textContent='Подключаемся к live-потоку…';
  });
  addEventListener('owner-live-offline',()=>{
    startupPending=false;startQueueFallback();if(panel.open)void refresh();
  });
  function resetDirectQueue(){
    directQueueEpoch=null;directQueueLive=false;primedQueue=null;primeAbort?.abort();primeAbort=null;
  }
  addEventListener('owner-live-connection',event=>{
    transportUp=event.detail==='registered'||event.detail==='live';
    if(transportUp){directQueueLive=!!directQueueEpoch;if(directQueueLive){stream?.close();stream=null;}return;}
    directQueueLive=false;
    if(event.detail==='closed'){resetDirectQueue();fetchAbort?.abort();}
    if(event.detail==='disconnected'){resetDirectQueue();startQueueFallback();if(panel.open)void refresh();}
  });
  addEventListener('owner-live-invalidate',()=>{resetDirectQueue();fetchAbort?.abort();if(panel.open){rendered.clear();lanes.clear();container.replaceChildren();status.textContent='Подключаемся к новой версии базы…';}});
  addEventListener('owner-live-snapshot',event=>{
    const {epoch,queue}=event.detail??{};
    if(typeof epoch!=='string'||!(/^[a-f0-9]{64}$/).test(epoch))return;
    startupPending=false;
    if(queue!==true){startQueueFallback();if(panel.open)void refresh();return;}
    if(directQueueEpoch!==epoch){directQueueEpoch=epoch;primedQueue=null;primeAbort?.abort();}
    directQueueLive=transportUp;if(directQueueLive){stream?.close();stream=null;}
    if(panel.open){void refresh();return;}
    primeAbort?.abort();const signal=new AbortController();primeAbort=signal;
    void snapshot(0,signal.signal).then(page=>{
      if(!signal.signal.aborted&&directQueueEpoch===epoch&&page.owner_live_epoch===epoch)
        primedQueue={page,at:Date.now()};
    }).catch(()=>{});
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
    if(!panel.open||startupPending)return;
    if(loading){dirty=true;return;}
    loading=true;
    const current=generation, requested=new Map(offsets), epoch=controlEpoch, sourceEpoch=directQueueEpoch;
    const abort=new AbortController();
    fetchAbort=abort;
    try{
      const pages=new Map(await Promise.all([...new Set(ordered.map(k=>requested.get(k)??0))].map(async n=>[n,n===0&&primedQueue&&Date.now()-primedQueue.at<3000?primedQueue.page:await snapshot(n,abort.signal)])))
      primedQueue=null;;
      if(!panel.open||current!==generation||sourceEpoch!==directQueueEpoch)return;
      const base=pages.values().next().value;
      if([...pages.values()].some(x=>x.stage!==base.stage||x.release_version!==base.release_version||sourceEpoch&&x.owner_live_epoch!==sourceEpoch))throw Error();
      const version=typeof base.release_version==='string'&&base.release_version?' · База '+base.release_version:'';
      const label=base.stage.toUpperCase()+version;
      if(status.textContent!==label)status.textContent=label;
      if(!inFlight&&epoch===controlEpoch)acceptControl(base);
      if(queueStage!==undefined&&base.stage!==queueStage)throw Error();
      queueStage=base.stage;
      acceptClear(base);
      if(base.capabilities?.standalone_pause===true)controlsReady=true;
      if((base.capabilities?.standalone_queue_sessions===true||base.stage==='test')&&!nativeProbeTried&&!sessionsState){
        nativeProbeTried=true;
        void loadSessions(base.capabilities?.standalone_queue_sessions===true);
      }
      for(const id of ordered){
        const offset=requested.get(id)??0;
        if((offsets.get(id)??0)===offset)render(id,pages.get(offset).queues.find(q=>q?.id===id),offset);
      }
    }catch{
      if(panel.open&&current===generation){
        const label=rendered.size?'Не удалось обновить. Показаны последние полученные данные.':'Не удалось загрузить очереди.';
        if(status.textContent!==label)status.textContent=label;
        if(!inFlight&&epoch===controlEpoch)acceptControl(null);
        acceptClear(null);
      }
    }finally{
      loading=false;fetchAbort=null;
      if(dirty){dirty=false;void refresh();}
    }
  }
  function stop(){
    generation++;clearState=null;closeClear();previews.clear();previewObserver?.disconnect();previewObserver=null;
    autoSeen.clear();previewJobs.clear();visiblePreviews.clear();dirty=false;fetchAbort?.abort();stream?.close();stream=null;
    if(timer)clearInterval(timer);timer=null;
  }
  launcher.addEventListener('click',()=>{
    if(panel.open)return;
    panel.showModal();
    offsets.clear();rendered.clear();lanes.clear();container.replaceChildren();
    sessionsState=null;policyDraft=null;sessionsDraft=new Set();sessionsBusy=false;sessionsProbe=false;nativeProbeTried=false;controlsReady=false;
    if(itemFeedback)itemFeedback.textContent='';
    if(clearFeedback)clearFeedback.textContent='';
    if(sessionsPanel){sessionsPanel.hidden=true;sessionsPanel.open=false;sessionFeedback.textContent='';}
    status.textContent='Подключаемся…';lastSignal=0;
    control=null;ownerReady=false;ownerCheck=null;nativeReady=false;probedRevision=null;controlFeedback.textContent='';drawControl();
    startQueueFallback();
    timer=setInterval(()=>{if(!directQueueLive&&Date.now()-lastSignal>2500)void refresh();},3000);
    void refresh();
  });
  panel.querySelector('#visual-queue-close').addEventListener('click',()=>panel.close());
  panel.addEventListener('close',()=>{stop();launcher.focus();});
  addEventListener('pagehide',()=>{resetDirectQueue();stop();},{once:true});
})();
