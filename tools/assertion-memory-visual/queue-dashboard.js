;(() => {
  const panel=document.querySelector('#visual-queue');
  if(!panel)return;
  const status=panel.querySelector('[role=status]');
  const container=panel.querySelector('.queue-items');
  const names = {source:'Реплики',maintenance:'Связи и пересчёт',interactive:'Вопросы'};
  const states={running:'В работе',ready:'Ожидает',waiting_origin:'Ждёт источник',delayed:'Отложено',paused:'Приостановлено',expired:'Исчерпаны попытки',active:'Активна',unavailable:'Недоступна',disabled:'Выключена'};
  const phases={extract:'Извлечение',audit:'Проверка',link:'Связывание',refresh:'Пересчёт',consolidate:'Объединение',query:'Ответ на вопрос'};
  let stream,timer,loading=false,dirty=false,offset=0,generation=0,lastSignal=0;
  const element=(tag,txt)=>{const e=document.createElement(tag);e.textContent=txt;return e;};
  const labels=(values,k)=>Object.hasOwn(values,k)?values[k]:k;
  async function refresh(){
    if(!panel.open)return;
    if(loading){dirty=true;return;}
    loading=true;
    const current=generation;
    try {
      const result=await fetch('/api/v1/capabilities?queue_status=1&limit=50&offset='+offset,
        {cache:'no-store',credentials:'same-origin',redirect:'error',headers:{accept:'application/json'}});
      if(!result.ok||!result.headers.get('content-type')?.includes('application/json'))throw Error();
      const d=await result.json();
      if(!panel.open||current!==generation)return;
      if(!['test','prod'].includes(d.stage)||!Array.isArray(d.queues))throw Error();
      status.textContent=d.stage.toUpperCase()+' · База '+d.release_version;
      container.replaceChildren();
      let next=null;
      for(const queue of d.queues){
        const section=element('section','');
        section.append(element('h3',labels(names,queue.id)+' · '+queue.total));
        section.append(element('p',labels(states,queue.status)));
        const list=element('ol','');
        for(const item of queue.items){
          const position=Number.isSafeInteger(item.position)?'№'+item.position+' · ':'';
          const phase=item.phase?' · '+labels(phases,item.phase):'';
          list.append(element('li',position+labels(states,item.status)+phase+' · '+item.id));
        }
        if(queue.items.length) section.append(list);
        else section.append(element('p',queue.total?'На этой странице нет записей.':'Пусто.'));
        container.append(section);
        if(Number.isSafeInteger(queue.next_offset)&&queue.next_offset>offset)next=queue.next_offset;
      }
      if(offset>0){
        const back=element('button','Первая страница');back.type='button';back.onclick=()=>{offset=0;void refresh();};container.append(back);
      }
      if(next!==null){
        const button=element('button','Дальше');button.type='button';button.onclick=()=>{offset=next;void refresh();};container.append(button);
      }
    }catch {
      if(panel.open&&current===generation){status.textContent='Очередь недоступна.';container.replaceChildren();}
    }finally {
      loading=false;
      if(dirty){dirty=false;void refresh();}
    }
  }
  function close(){
    generation++;stream?.close();stream=null;if(timer)clearInterval(timer);timer=null;
  }
  panel.addEventListener('toggle',()=>{
    close();
    if(!panel.open)return;
    offset=0;
    if(typeof EventSource!=='undefined'){
      stream=new EventSource('/api/v1/events?channel=dashboard',{withCredentials:true});
      const received=()=>{lastSignal=Date.now();void refresh();};
      stream.addEventListener('resync',received);
      stream.addEventListener('queue_changed',received);
    }
    // Snapshot fallback recovers missed updates, including buffered streams.
    timer=setInterval(()=>{if(Date.now()-lastSignal>2500)void refresh();},3000);
    void refresh();
  });
  addEventListener('pagehide',close,{once:true});
})();
