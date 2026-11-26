// Preserve the shipped renderer while carrying the TS boot reliability fix.
// The static seed (including its larger, different Worker) is not the public TS
// renderer. This only changes bounded startup and diagnostics, not graph layout.
import {hash} from './store.mjs';
export const BOOT_SEED='9a0cef2d67103909cd18a81d37aadeb62659622e2f21f56225b43d5fff0b791d';

function once(text,old,next){
  if(text.split(old).length!==2)throw Error('Unexpected graph boot seed anchor');
  return text.replace(old,()=>next);
}
const errors={
  session:'Сервер не ответил на проверку входа. Повторите.',
  capabilities:'Источник не ответил после входа. Повторите.',
  source:'Не удалось подтвердить источник карты. Повторите.',
  manifest:'Источник не успел подготовить карту. Повторите загрузку.',
  payload:'Не удалось вовремя загрузить звёзды, связи или темы. Повторите.',
  worker:'Браузер не успел обработать данные карты. Повторите.',
  present:'Браузер не успел отобразить карту. Повторите.',
  fatalWorker:'Браузер не смог запустить модуль обработки карты. Обновите страницу и повторите.',
};
const js=JSON.stringify;
const helper=`
async function releaseGraphBootStep(timeout,message,action,parent){
  const cancelled=()=>new DOMException('Aborted','AbortError');
  if(parent?.aborted)throw cancelled();
  const child=new AbortController();
  let reject;
  const stop=new Promise((_,no)=>{reject=no;});
  const cancel=()=>{reject(cancelled());child.abort();};
  const timer=setTimeout(()=>{reject(new Error(message));child.abort();},timeout);
  parent?.addEventListener('abort',cancel,{once:true});
  try{return await Promise.race([stop,Promise.resolve().then(()=>{
    if(child.signal.aborted)throw cancelled();
    return action(child.signal);
  })]);}
  catch(error){child.abort();throw error;}
  finally{clearTimeout(timer);parent?.removeEventListener('abort',cancel);}
}
async function releaseGraphBootBinary(descriptor,signal){
  const reply=await md(descriptor.url,signal,{headers:{Accept:'application/octet-stream'}});
  if(reply.headers.get('Content-Type')?.split(';',1)[0]?.trim().toLowerCase()!=='application/octet-stream')
    throw Error('Источник вернул данные карты в неподходящем формате.');
  const buffer=await reply.arrayBuffer();
  if(buffer.byteLength!==descriptor.byte_length)throw Error('Источник вернул неполные данные карты.');
  return buffer;
}
`;

export function patchGraphBoot(script,rawSeed){
  if(hash(Buffer.from(rawSeed))!==BOOT_SEED)throw Error('Graph boot overlay seed changed; re-audit the renderer');
  let result=script;
  const replace=(a,b)=>{result=once(result,a,b);};
  replace('async function md(e,t,n={})',helper+'async function md(e,t,n={})');
  replace('constructor(){wd(`worker-create`),this.worker.addEventListener(`message`,',
    'constructor(){wd(`worker-create`);const fail=()=>{if(this.disposed)return;this.bootFailure=Error('+
    js(errors.fatalWorker)+');this.disposed=!0;this.failSynchronization(this.bootFailure);this.worker.terminate()};'+
    'this.worker.addEventListener(`error`,fail);this.worker.addEventListener(`messageerror`,fail);this.worker.addEventListener(`message`,');
  replace('if(this.disposed)throw Error(`Worker disposed`)','if(this.disposed)throw this.bootFailure??Error(`Worker disposed`)');
  replace('this.authenticated?this.loadGraph():this.session()', 'this.authenticated&&this.scene&&this.capabilities?this.loadGraph():this.session()');
  replace('await hd(`/session`,le)',`await releaseGraphBootStep(12000,${js(errors.session)},signal=>hd('/session',le,signal))`);
  replace('throw this.failLoad(`Сервер недоступен. Запустите источник и повторите подключение.`),e',
    'if(!(e instanceof fd&&e.status===401))this.failLoad(je(e));throw e');
  replace('Ld(t,!0,`Войти`),$(`login-error`,HTMLElement).textContent=``;try{','Ld(t,!0,`Войти`);let accepted=!1;$(`login-error`,HTMLElement).textContent=``;try{');
  replace('this.csrf=t.csrf_token,e.value=``,await this.start()', 'accepted=!0,this.csrf=t.csrf_token,e.value=``,await this.start()');
  replace('catch(e){$(`login-error`,HTMLElement).textContent=e instanceof fd&&e.status===401?`Пароль не подошёл.`:je(e)}finally',
    'catch(e){if(accepted){if(e instanceof fd&&e.status===401){this.showLogin();$(`login-error`,HTMLElement).textContent=`Сессия истекла. Войдите ещё раз.`;}else this.failLoad(je(e));}else $(`login-error`,HTMLElement).textContent=e instanceof fd&&e.status===401?`Пароль не подошёл.`:je(e)}finally');
  replace('this.capabilities=await hd(`/capabilities`,ce);let e=new URLSearchParams(location.search)',
    `this.capabilities=await releaseGraphBootStep(12000,${js(errors.capabilities)},signal=>hd('/capabilities',ce,signal));let e=new URLSearchParams(location.search)`);
  replace('let r=await this.manifestFor(t.signal,e.selector)',`let r=await releaseGraphBootStep(24000,${js(errors.manifest)},signal=>this.manifestFor(signal,e.selector),t.signal)`);
  replace('this.capabilities=await hd(`/capabilities`,ce,t.signal)',
    `this.capabilities=await releaseGraphBootStep(12000,${js(errors.source)},signal=>hd('/capabilities',ce,signal),t.signal)`);
  replace('let[a,o,s]=await Promise.all([md(r.nodes.url,t.signal).then(e=>e.arrayBuffer()),md(r.edges.url,t.signal).then(e=>e.arrayBuffer()),hd(r.topics_url,j,t.signal)])',
    `let[a,o,s]=await releaseGraphBootStep(18000,${js(errors.payload)},signal=>Promise.all([releaseGraphBootBinary(r.nodes,signal),releaseGraphBootBinary(r.edges,signal),hd(r.topics_url,j,signal)]),t.signal)`);
  replace('n=new lm;let c=await n.load(a,o,r.counts.nodes,r.counts.edges,r.selector.history,s,r.revision)',
    `try{n=new lm;}catch{throw Error(${js(errors.fatalWorker)})}$(\`loading-detail\`,HTMLElement).textContent=\`Проверяем данные карты…\`;let c=await releaseGraphBootStep(15000,${js(errors.worker)},()=>n.load(a,o,r.counts.nodes,r.counts.edges,r.selector.history,s,r.revision),t.signal)`);
  replace('let l=!this.graph,u=n,d=await this.presentGraph(c,r,s,u,l,()=>this.authenticated&&this.graphRequest===t&&this.capabilities?.provider_id===r.provider_id,t.signal)',
    `let l=!this.graph,u=n;$(\`loading-detail\`,HTMLElement).textContent=\`Отрисовываем карту…\`;let d=await releaseGraphBootStep(20000,${js(errors.present)},signal=>this.presentGraph(c,r,s,u,l,()=>this.authenticated&&this.graphRequest===t&&this.capabilities?.provider_id===r.provider_id,signal),t.signal)`);
  replace('throw this.setConnection(this.manifest?`Нет связи · данные от ${Me(this.manifest.captured_at,`instant`)}`',
    't.abort();if(e instanceof fd&&e.status===401){this.showLogin();throw e;}throw this.setConnection(this.manifest?`Нет связи · данные от ${Me(this.manifest.captured_at,`instant`)}`');
  replace('this.setConnection(`Нет связи с источником`,!1)}readerConnections',
    'this.setConnection(`Карта не загружена`,!1)}readerConnections');
  replace('if(s.signal?.throwIfAborted(),c!==this.graphToken||s.isCurrent?.()===!1)', 'if(s.signal?.aborted||c!==this.graphToken||s.isCurrent?.()===!1)');
  replace('function mm(e,t){return new Promise((n,r)=>{let i=setTimeout(n,e);t.addEventListener(`abort`,()=>{clearTimeout(i),r(new DOMException(`Aborted`,`AbortError`))},{once:!0})})}',
    'function mm(e,t){return new Promise((n,r)=>{if(t.aborted){r(new DOMException(`Aborted`,`AbortError`));return}const cancel=()=>{clearTimeout(timer);r(new DOMException(`Aborted`,`AbortError`))};const timer=setTimeout(()=>{t.removeEventListener(`abort`,cancel);n()},e);t.addEventListener(`abort`,cancel,{once:!0})})}');
  return result;
}
