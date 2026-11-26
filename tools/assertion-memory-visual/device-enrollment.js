(() => {
  const el=id=>document.getElementById(id);
  const section=el('owner-devices');
  if(!section)return;
  const queue=el('visual-queue'),open=el('visual-queue-open'),form=el('owner-device-enroll');
  const label=el('owner-device-label'),fingerprint=el('owner-device-fingerprint'),confirmation=el('owner-device-confirm');
  const apply=el('owner-device-apply'),feedback=el('owner-devices-feedback'),list=el('owner-devices-list');
  const api='/api/v1/processing/devices';
  const fp=/^[0-9a-f]{64}$/,revision=/^[0-9a-f]{32}$/,proof=/^[0-9]{1,18}\.[0-9a-f]{64}$/;
  const matches=(rule,value)=>typeof value==='string'&&rule.test(value);
  const exact=(row,fields)=>row!==null&&typeof row==='object'&&!Array.isArray(row)&&
    Object.keys(row).length===fields.length&&fields.every(key=>Object.hasOwn(row,key));
  const isLane=x=>x==='test'||x==='prod';
  const cleanLabel=x=>typeof x==='string'&&x===x.trim()&&x.length>=1&&x.length<=80&&!/[\u0000-\u001f\u007f]/u.test(x);
  let generation=0, state=null, pending=null, busy=false, controller=null;
  function clearDraft(){
    pending=null;confirmation.hidden=true;el('owner-device-prompt').textContent='';apply.disabled=true;
  }
  function stop(){
    generation++;controller?.abort();controller=null;state=null;busy=false;clearDraft();
    section.hidden=true;list.replaceChildren();feedback.textContent='';form.hidden=false;form.reset();
  }
  async function request(path,signal,method='GET',body,csrf){
    const headers={'Accept':'application/json'};
    if(body!==undefined)headers['Content-Type']='application/json';
    if(csrf)headers['X-CSRF-Token']=csrf;
    const response=await fetch(path,{credentials:'same-origin',redirect:'error',cache:'no-store',
      signal,method,headers,...(body===undefined?{}:{body:JSON.stringify(body)})});
    if(response.status!==200)throw new Error('request');
    return response.json();
  }
  function accepted(d,wanted){
    if(!exact(d,['stage','schema','revision','items'])||d.stage!==wanted||d.schema!=='owner-machine-devices/v1'||!matches(revision,d.revision)
      ||!Array.isArray(d?.items)||d.items.length>16)return false;
    const seen=new Set();
    return d.items.every(it=>{
      if(!exact(it,['fingerprint','label','revocable'])||!matches(fp,it.fingerprint)||seen.has(it.fingerprint)||!cleanLabel(it?.label)||typeof it?.revocable!=='boolean')return false;
      seen.add(it.fingerprint);return true;
    });
  }
  function render(result,csrf){
    state={result,csrf};section.hidden=false;
    el('owner-devices-stage').textContent='Окружение: '+result.stage.toUpperCase();
    list.replaceChildren();
    for(const item of result.items){
      const row=document.createElement('li'), name=document.createElement('span');
      name.textContent=item.label+' · '+item.fingerprint.slice(0,12)+'…';
      row.append(name);
      if(item.revocable){
        const button=document.createElement('button');button.type='button';button.textContent='Отозвать';
        button.addEventListener('click',()=>void prepare('revoke',item.fingerprint,null,item.label));
        row.append(button);
      }
      list.append(row);
    }
  }
  async function refresh(ticket,signal,stage,csrf){
    const result=await request(api,signal);
    if(!accepted(result,stage))throw new Error('stage');
    if(generation===ticket&&!signal.aborted)render(result,csrf);
  }
  open.addEventListener('click',()=>{
    stop();
    const ticket=generation; controller=new AbortController();const {signal}=controller;
    void (async()=>{
      try{
        const [session,caps]=await Promise.all([request('/api/v1/session',signal),request('/api/v1/capabilities?queue_status=1&limit=1&offset=0',signal)]);
        if(session?.authenticated!==true||typeof session.csrf_token!=='string'||!session.csrf_token||!isLane(caps?.stage)
          ||caps?.capabilities?.owner_machine_devices_v1!==true)return;
        await refresh(ticket,signal,caps.stage,session.csrf_token);
      }catch{/* Unsupported or unauthorized environments have no device controls. */}
    })();
  });
  queue.addEventListener('close',stop);
  el('logout')?.addEventListener('click',stop);
  addEventListener('pagehide',stop);
  form.addEventListener('submit',event=>{
    event.preventDefault();
    const alias=label.value.trim(),code=fingerprint.value.trim();
    const digest=code.startsWith('sha256:')?code.slice(7):'';
    if(!fp.test(digest)||!cleanLabel(alias)){feedback.textContent='Введите название и код sha256: с 64 строчными шестнадцатеричными символами.';return;}
    void prepare('enroll',digest,alias,alias);
  });
  async function prepare(action,digest,alias,description){
    if(!state||!controller||busy)return;
    clearDraft();busy=true;feedback.textContent='';const ticket=generation,{signal}=controller;
    const {result,csrf}=state;
    const body={stage:result.stage,expected_revision:result.revision,action,fingerprint:digest,...(alias===null?{}:{label:alias})};
    try{
      const response=await request(api+'/prepare',signal,'POST',body,csrf);
      if(ticket!==generation||signal.aborted)return;
      if(!exact(response,['stage','revision','action','fingerprint','operation_proof'])||response.stage!==body.stage||response?.revision!==body.expected_revision||
         response?.action!==action||response?.fingerprint!==digest||!matches(proof,response.operation_proof))throw new Error('wrong proof');
      pending={body,token:response.operation_proof};
      el('owner-device-prompt').textContent=(action==='enroll'?'Подключить «':'Отозвать доступ «')+description+'» в '+body.stage.toUpperCase()+'? Отпечаток: '+digest;
      confirmation.hidden=false;apply.disabled=false;apply.focus();
    }catch{if(ticket===generation)feedback.textContent='Не удалось подготовить действие. Перезапустите окно очереди и повторите.';}
    finally{if(ticket===generation)busy=false;}
  }
  el('owner-device-cancel').addEventListener('click',clearDraft);
  apply.addEventListener('click',()=>void commit());
  async function commit(){
    if(!pending||!state||!controller||busy)return;
    const {body,token}=pending,{csrf}=state,ticket=generation,{signal}=controller;
    clearDraft();busy=true;
    try{
      const result=await request(api,signal,'PUT',body,token);
      if(ticket!==generation||signal.aborted)return;
      if(!exact(result,['stage','revision','action','fingerprint','committed'])||result.stage!==body.stage||result?.action!==body.action||result?.fingerprint!==body.fingerprint
        ||result?.committed!==true||!matches(revision,result.revision)||result.revision===body.expected_revision)throw new Error('not committed');
      form.reset();feedback.textContent=body.action==='enroll'?'Устройство подключено.':'Доступ устройства отозван.';
      await refresh(ticket,signal,body.stage,csrf);
    }catch{if(ticket===generation){feedback.textContent='Не удалось подтвердить актуальное состояние. Откройте очередь заново перед повтором.';state=null;form.hidden=true;list.replaceChildren();}}
    finally{if(ticket===generation)busy=false;}
  }
})();
