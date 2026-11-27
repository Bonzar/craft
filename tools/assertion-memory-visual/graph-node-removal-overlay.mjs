// Node deletion is dark until native Graph publishes an explicit lane capability.
const hex=(s,n)=>typeof s==='string'&&new RegExp('^[a-f0-9]{'+n+'}$').test(s);
export const nodeRemovalCss='[data-owner-node-removal]{padding:12px 16px;border-top:1px solid #59657e80}[data-owner-node-removal] button{margin-right:8px}.owner-node-removal-button{color:#ffd5d8;border:1px solid #ae5865;background:#341d2b;border-radius:7px;padding:7px 11px}[data-owner-node-removal] p:empty{display:none}';
export function attachNodeRemoval(app,card,manifest,doc=document,send=fetch){
  const cap=app.capabilities?.owner_graph_node_removal;
  const anchor=doc.getElementById('reader');
  anchor?.querySelector('[data-owner-node-removal]')?.remove();
  if(!anchor||!app.authenticated||cap?.version!==1||!['test','prod'].includes(cap.stage)
      ||manifest?.selector?.mode!=='current'||manifest?.selector?.history!==false||card.revision!==manifest.revision
      ||card.is_current!==true||card.session_context_only===true
      ||typeof card.thought_ref?.thought_id!=='string'||!Number.isSafeInteger(card.thought_ref?.version)
      ||card.thought_ref.version<1)return;
  const identity={stage:cap.stage,thought_id:card.thought_ref.thought_id,thought_version:card.thought_ref.version};
  const panel=doc.createElement('div');panel.dataset.ownerNodeRemoval='';
  const status=doc.createElement('p');status.setAttribute('role','status');
  const action=doc.createElement('button');action.type='button';action.textContent='Удалить мысль';
  action.className='owner-node-removal-button';
  const cancel=doc.createElement('button');cancel.type='button';cancel.textContent='Отмена';cancel.hidden=true;
  const current=()=>panel.isConnected&&app.authenticated&&app.manifest===manifest&&app.selectedNodeId===card.id;
  const root='/api/v1/processing/graph-nodes/remove';
  const fail={
    dependent_thoughts:'Другие сохранённые мысли опираются на эту. Удаление не выполнено.',
    thought_work_in_progress:'Эта мысль ещё обрабатывается. Удаление не выполнено.',
    dependency_proof_unavailable:'Не удалось проверить зависимости. Удаление не выполнено.',
    thought_changed_or_missing:'Мысль уже изменилась или недоступна. Обновите граф.',
    node_confirmation_stale:'Граф изменился. Откройте мысль снова и подтвердите удаление.',
    control_changed:'Граф изменился. Откройте мысль снова и подтвердите удаление.',
    csrf_denied:'Подтверждение истекло. Начните заново.',
  };
  const request=async(path,method,body,token)=>{
    const headers={'Content-Type':'application/json'};
    if(token)headers['X-CSRF-Token']=token;
    const response=await send(path,{method,headers,body:JSON.stringify(body),credentials:'same-origin',redirect:'error',cache:'no-store'});
    const result=await response.json();
    if(!response.ok){const error=new Error(fail[result.error]??'Удаление не подтверждено. Обновите граф.');error.definite=response.status===409||response.status===403;throw error;}
    if(result.stage!==identity.stage||result.thought_id!==identity.thought_id||result.thought_version!==identity.thought_version
        ||!hex(result.revision,32)||!hex(result.scope,64)||!hex(result.source_revision,64))throw Error('Сервер не подтвердил точную мысль. Обновите граф.');
    return result;
  };
  let challenge=null, busy=false;
  const reset=()=>{challenge=null;cancel.hidden=true;action.textContent='Удалить мысль';};
  cancel.addEventListener('click',()=>{if(!busy){reset();status.textContent='Удаление отменено.';}});
  action.addEventListener('click',async()=>{
    if(!current()||busy)return;
    const committing=challenge!==null;
    busy=true;action.disabled=true;cancel.disabled=true;
    try{
      if(!challenge){
        const preview=await request(root+'/prepare','POST',identity);
        if(!current())return;
        if(typeof preview.csrf_token!=='string'||preview.csrf_token.length<16
            )throw Error('Граф изменился. Откройте мысль снова.');
        challenge=preview;cancel.hidden=false;action.textContent='Подтвердить удаление';
        status.textContent='Убрать эту мысль и её связи с графа? Исходные реплики и другие мысли останутся.';
      }else{
        const candidate=challenge;reset();
        const result=await request(root,'PUT',{...identity,expected_revision:candidate.revision},candidate.csrf_token);
        if(result.removed!==true||result.revision!==candidate.revision||result.scope!==candidate.scope
            ||result.source_revision===candidate.source_revision)throw Error('Удаление не подтверждено. Обновите граф.');
        if(!current())return;
        action.hidden=true;status.textContent='Мысль удалена. Обновляем граф…';
        try{await app.loadGraph(undefined,true);}
        catch{status.textContent='Мысль удалена. Для обновления графа перезагрузите страницу.';return;}
        if(!panel.isConnected)return;
        status.textContent='Мысль удалена.';
      }
    }catch(error){
      if(current()){
        reset();status.textContent=error instanceof Error&&error.message.startsWith('Failed to fetch')?'Связь прервалась. Обновите граф и проверьте мысль.':String(error.message??'Не удалось подтвердить удаление.');
        // A lost commit response must not enable an easy blind retry.
        if(committing&&!error.definite)action.hidden=true;
      }
    }finally{busy=false;action.disabled=false;cancel.disabled=false;}
  });
  panel.append(action,cancel,status);anchor.append(panel);
}

function once(input,from,to){if(input.split(from).length!==2)throw Error('Pinned node removal seam changed');return input.replace(from,()=>to);}
export function patchGraphNodeRemoval(script){
  script=once(script,'ce=p({provider_id:i,','ce=p({provider_id:i,owner_graph_node_removal:d(c,null),');
  script=once(script,'N=p({id:i,ordinal:o,revision:i,text_revision:i','N=p({id:i,ordinal:o,revision:i,thought_ref:d(c,null),is_current:d(s,false),session_context_only:d(s,false),text_revision:i');
  script=once(script,'{revision:e.revision,provider_id:t.provider_id})}async refreshSelectedStatus','{revision:e.revision,provider_id:t.provider_id});releaseAttachNodeRemoval(this,e,t)}async refreshSelectedStatus');
  return 'const releaseAttachNodeRemoval='+attachNodeRemoval.toString().replace('function attachNodeRemoval(', 'function(').replace('const cap=',()=>"const hex=(s,n)=>typeof s==='string'&&new RegExp('^[a-f0-9]{'+n+'}$').test(s);const cap=")+';\n'+script;
}
