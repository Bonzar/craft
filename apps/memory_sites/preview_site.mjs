// Synthetic-only demonstration; use an owner-private Site with its own
// disposable D1 binding. There is no MCP surface, free-text input, model
// credential, or live-data loader here.
import {runSyntheticStagingSmoke} from './synthetic_smoke.mjs';
import {createPreviewSearch} from './preview_search.mjs';

// Site access is deployed owner-only. Require a real signed-in visitor as
// well, not a service credential, for the owner-facing demo search.
const search = createPreviewSearch({owner:req=>Boolean(req.headers.get('oai-authenticated-user-id'))});
const names = Object.freeze(['стойку','шкафчики','востоке']);
const escape = v=>String(v).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c]);

const headers = {
  'cache-control':'no-store',
  'x-content-type-options':'nosniff',
  'referrer-policy':'no-referrer',
  'content-security-policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
};
const html = (markup='',status=200) => new Response(`<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>База памяти · тест переноса</title>
<style>body{font:1rem/1.55 system-ui;background:#f5f6f1;color:#152a21;margin:0}main{max-width:40rem;margin:clamp(2rem,10vw,6rem) auto;padding:0 1.25rem}h1{line-height:1.17;font-size:clamp(2rem,7vw,3.25rem);letter-spacing:-.035em}section{border:1px solid #d4dacf;border-radius:1.2rem;background:white;padding:1.25rem;margin:1.5rem 0}button{background:#164d38;color:white;font:inherit;font-weight:610;border:0;border-radius:.6rem;padding:.8rem 1rem;cursor:pointer}small{color:#4c6056}ul{padding-left:1.25rem}</style>
<main><small>ПРИВАТНЫЙ ТЕСТ · ТОЛЬКО ВЫМЫШЛЕННЫЕ ДАННЫЕ</small>
<h1>Проверка обработки реплики</h1>
<p><a href="/explore">Открыть карту «Вселенная знаний»</a></p>
<p>Этот тест записывает вымышленную реплику в облачную D1, готовит черновик и проверяет, что отклонённый черновик не сохраняется.</p>
<section><form method="post" action="/preview/run"><button type="submit">Запустить тест</button></form>
<p>Ответы генерации и проверки заданы заранее. Реальная модель не вызывается.</p></section>
<section><h2>Поиск по вымышленному графу</h2>
<p>Поиск по точным словам, не поиск по смыслу. Выбери один из тестовых запросов:</p>
<ul>${names.map(s=>`<li><a href="/preview/search?q=${encodeURIComponent(s)}">${s}</a></li>`).join('')}</ul></section>
${markup}<small>Полноценная обработка, поиск по смыслу, создание связей и перенос настоящих записей в этом выпуске не включены.</small></main></html>`,{status,headers:{...headers,'content-type':'text/html; charset=utf-8'}});
const json = (body,status) => Response.json(body,{status,headers});

export default {
  async fetch(request,env) {
    const url=new URL(request.url);
    if (url.pathname==='/' && request.method==='GET') return html();
    if (url.pathname==='/preview/search') {
      if (!names.includes(url.searchParams.get('q'))) return json({error:'Use a listed synthetic keyword'},400);
      const result=await search(request,env);
      if (!request.headers.get('accept')?.includes('text/html') || !result.ok) return result;
      const {hits}=await result.json();
      const list=hits.length ? '<ul>'+hits.map(({text})=>'<li>'+escape(text)+'</li>').join('')+'</ul>' : '<p>Нет совпадений среди текущих записей.</p>';
      return html('<section><h2>Результат поиска</h2>'+list+'</section>');
    }
    if (url.pathname!=='/preview/run') return json({error:'Not found'},404);
    if (request.method!=='POST') return json({error:'Use POST'},405);
    if (request.headers.get('origin')!==url.origin) return json({error:'Origin mismatch'},403);
    // Native form has no named fields, so there is nothing that can become
    // intake. Reject requests that supply data, even though none is consumed.
    const contentType=request.headers.get('content-type')?.split(';')[0];
    const contentLength=request.headers.get('content-length');
    if (contentLength!==null && contentLength!=='0' || contentType && contentType!=='application/x-www-form-urlencoded') {
      return json({error:'No input accepted'},400);
    }
    if (request.body) {
      const reader=request.body.getReader();
      try {
        const item=await reader.read();
        if (!item.done && item.value?.byteLength) return json({error:'No input accepted'},400);
      } finally { await reader.cancel().catch(()=>{}); reader.releaseLock(); }
    }
    let report;
    try { report=await runSyntheticStagingSmoke(env.DB); }
    catch { report={ok:false,kind:'synthetic-staging-smoke'}; }
    const status=report.ok ? 200 : 503;
    if (!request.headers.get('accept')?.includes('text/html')) return json(report,status);
    return report.ok
      ? html('<section aria-live="polite"><h2>Проверка прошла</h2><ul><li>Принятый черновик сохранён в тестовой D1.</li><li>Отклонённый черновик не сохранён.</li></ul></section>',status)
      : html('<section aria-live="polite"><h2>Проверка не прошла</h2><p>Результат обработки не подтверждён.</p></section>',status);
  },
};
