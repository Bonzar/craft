// Owner-private Sites preview: one explicitly fictional phrase, live adapter only.
import {sameOriginPreviewPost} from './preview_origin.mjs';
import {normalizeCaptureMessage,prepareCaptureReceipt} from './capture_message.mjs';
import {admitRawCapture} from './raw_capture.mjs';
import {encodeCanonicalThought,pinCanonicalRawBasis} from './semantic_encoding.mjs';

export const modelPreviewPath='/preview/model';
export const modelPreviewPhrase='У вымышленной теплицы сиреневая дверь.';
const purpose='private-synthetic-assertion-smoke/v1';
const fixture=Object.freeze({
  session_id:'synthetic-assertion-live-v3',message_id:'fictional-greenhouse',position:0,
  actor_id:'user',role:'user',text:modelPreviewPhrase,source:'fixture://permanent/live/v3',
  scope:'synthetic-assertion-live-v3',
});
const protectedHeaders={
  'cache-control':'private, no-store','x-content-type-options':'nosniff','referrer-policy':'same-origin',
  'content-security-policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
};
const htmlType='text/html; charset=utf-8';
const esc=v=>String(v).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
const plain=(state,status=200)=>Response.json(state,{status,headers:protectedHeaders});
const result=(response,status,code,description,ready=false)=>{
  if(!response.headers.get('accept')?.includes('text/html')) return plain({state:code},status);
  return new Response(`<!doctype html><html lang="ru"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Живая обработка · База памяти</title>
<style>body{background:#f5f6f1;color:#152a21;font:1rem/1.55 system-ui}main{max-width:41rem;margin:clamp(2rem,9vw,5rem) auto;padding:0 1.3rem}form,section{border:1px solid #d4dacf;border-radius:1.1rem;background:#fff;padding:1.2rem;margin:1.4rem 0}input{display:block;box-sizing:border-box;width:100%;margin:.7rem 0;padding:.6rem;font:inherit}button{padding:.7rem;font:inherit}</style>
<main><small>ПРИВАТНЫЙ ТЕСТ · ОДНА ВЫМЫШЛЕННАЯ РЕПЛИКА</small><h1>Записать реплику и обработать моделью</h1>
<p>Генерация и отдельная проверка используют внешний OpenAI API. Это не тест с заранее заданными ответами. Личные данные вводить нельзя.</p>
<section role="status">${description}</section>
${ready?`<form method="post" action="${modelPreviewPath}"><p>Допустимая реплика: <strong>${esc(modelPreviewPhrase)}</strong></p><label>Введите эту реплику <input name="utterance" maxlength="${[...modelPreviewPhrase].length}" autocomplete="off" spellcheck="false" required></label><button type="submit">Отправить одну тестовую реплику</button></form>`:''}
<p><a href="/preview/permanent">Отдельный тест с заданными ответами</a> · <a href="/">К списку проверок</a></p></main></html>`,
  {status,headers:{...protectedHeaders,'content-type':htmlType}});
};

const bind=(db,sql,...args)=>db.prepare(sql).bind(...args);
const first=query=>{
  if(query?.success===false||!Array.isArray(query?.results)) throw new Error('Preview storage unavailable.');
  return query.results[0]??null;
};
async function marked(db) {
  try {
    const [row]=await db.batch([bind(db,'SELECT purpose FROM memory_synthetic_smoke_environment WHERE singleton=1 AND purpose=?',purpose)]);
    return first(row)?.purpose===purpose;
  } catch { return false; }
}
async function reference() {
  return prepareCaptureReceipt(await normalizeCaptureMessage(fixture));
}
function actualRaw(raw,ref) {
  return raw?.id===ref.event_id&&raw.session_id===fixture.session_id&&raw.turn_id===fixture.message_id&&
    raw.prompt===fixture.text&&raw.actor===fixture.actor_id&&raw.scope===fixture.scope&&raw.source===fixture.source&&
    typeof raw.recorded_at==='string'&&Number.isFinite(Date.parse(raw.recorded_at))&&
    Object.keys(raw).length===9&&JSON.stringify(raw.metadata)===JSON.stringify(ref.raw_metadata);
}
const rawSha=async source=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(source))),
  byte=>byte.toString(16).padStart(2,'0')).join('');

async function readStored(db,ref) {
  const [query]=await db.batch([bind(db,`SELECT c.raw_json,c.raw_sha256,c.receipt,j.state,j.attempts,j.lease_expires_at,
    r.thought_id,r.thought_payload,r.thought_sha256,r.source_sha256,r.evidence_sha256,
    t.current_version,v.payload,v.content_hash,
    (SELECT count(*) FROM memory_thought_version_bases b WHERE b.thought_id=r.thought_id AND b.version=1 AND b.basis_event_id=c.event_id) AS basis_count,
    (SELECT count(*) FROM memory_job_publications p WHERE p.job_id=c.event_id) AS staged_count
    FROM memory_raw_captures c JOIN memory_jobs j ON j.id=c.event_id
    LEFT JOIN memory_live_source_commits r ON r.job_id=c.event_id
    LEFT JOIN memory_thoughts t ON t.id=r.thought_id
    LEFT JOIN memory_thought_versions v ON v.thought_id=r.thought_id AND v.version=1
    WHERE c.event_id=?`,ref.event_id)]);
  const row=first(query);
  if(!row) return {state:'fresh'};
  const source=JSON.parse(row.raw_json);
  if(row.receipt!==ref.receipt||!actualRaw(source,ref)||await rawSha(row.raw_json)!==row.raw_sha256||
    row.staged_count!==0) return {state:'unconfirmed'};
  if(row.state!=='done'||!row.thought_id)
    return {state:row.state==='running'&&row.lease_expires_at>Date.now()?'running':row.state==='ready'&&row.attempts===0?'fresh':
      row.state==='failed'?'not_saved':'unconfirmed'};
  if(row.attempts!==1||row.current_version!==1||row.basis_count!==1||
     row.thought_payload!==row.payload||row.thought_sha256!==row.content_hash||row.source_sha256!==row.raw_sha256||
     !/^[0-9a-f]{64}$/.test(row.evidence_sha256)) return {state:'unconfirmed'};
  const thought=JSON.parse(row.payload);
  const expectedBasis=await pinCanonicalRawBasis({
    event_id:ref.event_id,span:{start:0,end:[...fixture.text].length},meaning:fixture.text,
  },source);
  if(thought.id!==row.thought_id||thought.version!==1||thought.metadata?.verification!=='accepted'||
     thought.metadata.protocol!=='unified-thought-v1'||thought.origin?.event_id!==ref.event_id||
     thought.bases?.length!==1||thought.bases[0].content_hash!==expectedBasis.content_hash||
     thought.bases[0].meaning!==expectedBasis.meaning||
     thought.bases[0].event_id!==ref.event_id||thought.bases[0].span?.start!==0||
     thought.bases[0].span?.end!==expectedBasis.span.end) return {state:'unconfirmed'};
  const check=await encodeCanonicalThought(thought);
  if(check.payload!==row.payload||check.content_hash!==row.content_hash) return {state:'unconfirmed'};
  return {state:'saved',thought:{id:thought.id,version:1,text:thought.text,content_hash:row.content_hash}};
}

async function exactPhrase(request) {
  const max=512;
  const size=request.headers.get('content-length');
  const type=request.headers.get('content-type')?.split(';')[0];
  if(type!=='application/x-www-form-urlencoded'||size!==null&&(!/^\d+$/.test(size)||Number(size)>max)||!request.body)
    return false;
  const reader=request.body.getReader();
  const decoder=new TextDecoder('utf-8',{fatal:true});
  let read=0, form='';
  try {
    while(true){
      const {value,done}=await reader.read();
      if(done) break;
      read+=value.byteLength;
      if(read>max)return false;
      form+=decoder.decode(value,{stream:true});
    }
    form+=decoder.decode();
    const fields=[...new URLSearchParams(form)];
    return fields.length===1&&fields[0][0]==='utterance'&&fields[0][1]===modelPreviewPhrase;
  } catch { return false; }
  finally { await reader.cancel().catch(()=>{}); reader.releaseLock(); }
}
const redirect=()=>new Response(null,{status:303,headers:{...protectedHeaders,location:modelPreviewPath}});
const nowSql="CAST(strftime('%s','now') AS INTEGER)*1000+CAST(substr(strftime('%f','now'),4,3) AS INTEGER)";

/**
 * buildProvider is trusted server configuration only. It must be the live
 * provider factory in production; no scripted route/fallback is supplied.
 */
export function createModelPreviewRoute({buildProvider}={}) {
  return async (request,env)=>{
    if(new URL(request.url).pathname!==modelPreviewPath)return null;
    if(!request.headers.get('oai-authenticated-user-id'))return plain({error:'Sign in to the private Site.'},401);
    const post=request.method==='POST';
    if(!post&&request.method!=='GET')return plain({error:'Use GET or POST.'},405);
    if(post&&!sameOriginPreviewPost(request))return result(request,403,'origin_mismatch','Повтори отправку со страницы теста.');
    if(post&&!await exactPhrase(request))return result(request,400,'synthetic_phrase_only','Допускается только указанная вымышленная реплика.');
    const db=env?.DB;
    if(!await marked(db))return result(request,503,'storage_unavailable','Специальное тестовое хранилище недоступно.');
    try{
      const ref=await reference();
      const state=await readStored(db,ref);
      if(state.state==='saved'){
        if(post)return redirect();
        if(!request.headers.get('accept')?.includes('text/html'))return plain(state);
        return result(request,200,'saved','<h2>Мысль сохранена</h2><p>'+esc(state.thought.text)+
          '</p><p>Это новая проверка записи из постоянного хранилища.</p>');
      }
      if(state.state==='running')return post?redirect():result(request,200,'running','Тест обрабатывается. Обнови страницу позже.');
      if(state.state!=='fresh') return result(request,503,state.state,
        'Постоянная запись не подтверждена. Повтор не запускает платную обработку.');
      let admittedSource=null,commit;
      try {
        commit=buildProvider?.(env,{authorizeSource:subject=>Boolean(admittedSource&&subject.id===ref.event_id&&
          subject.source===admittedSource.payload&&subject.sourceSha256===admittedSource.payload_sha256)});
      } catch { /* Configuration failure never starts intake or provider work. */ }
      if(typeof commit!=='function')
        return result(request,503,'provider_unavailable','Внешняя модель пока не подключена. Реплика не записана.');
      if(!post) return result(request,200,'ready','Готово к одному тестовому запуску.',true);

      const now=Date.now();
      await admitRawCapture(db,fixture,{now});
      const leaseToken=crypto.randomUUID();
      const [claim]=await db.batch([bind(db,`UPDATE memory_jobs SET state='running',attempts=1,
        lease_token=?,lease_expires_at=?,updated_at=?
        WHERE id=? AND kind='source-thoughts' AND state='ready' AND attempts=0 AND available_at<=?
        AND EXISTS (SELECT 1 FROM memory_raw_captures c WHERE c.event_id=memory_jobs.id
          AND c.receipt=? AND c.raw_json=memory_jobs.payload AND c.raw_sha256=memory_jobs.payload_sha256)
        RETURNING payload,payload_sha256`,leaseToken,now+60_000,now,ref.event_id,now,ref.receipt)]);
      admittedSource=first(claim);
      if(!admittedSource)return redirect();
      const match=actualRaw(JSON.parse(admittedSource.payload),ref)&&
        await rawSha(admittedSource.payload)===admittedSource.payload_sha256;
      let outcome;
      try { if(match)outcome=await commit(db,{id:ref.event_id,leaseToken}); } catch { /* Never expose provider error. */ }
      if(outcome?.status==='committed')return redirect();
      // At most one bounded cost-bearing attempt per fixture. A rejection
      // leaves only the fictional raw row and a failed job, never a thought.
      const finishAt=Date.now();
      await db.batch([bind(db,`UPDATE memory_jobs SET state='failed',failure_code='non_retryable',
        lease_token=NULL,lease_expires_at=NULL,updated_at=?
        WHERE id=? AND state='running' AND lease_token=? AND lease_expires_at>MAX(?,${nowSql})
        AND NOT EXISTS (SELECT 1 FROM memory_live_source_commits r WHERE r.job_id=memory_jobs.id)`,
        finishAt,ref.event_id,leaseToken,finishAt)]);
      return redirect();
    } catch {
      return result(request,503,'unconfirmed','Сохранение не подтверждено. Данные внешней модели не показаны.');
    }
  };
}
