// Owner-facing, private Sites demo of the persistent synthetic slice only.
import {sameOriginPreviewPost} from './preview_origin.mjs';
import {getSyntheticPermanentSmoke,runSyntheticPermanentSmoke} from './synthetic_permanent_smoke.mjs';

const path='/preview/permanent';
const headers={
  'cache-control':'private, no-store',
  'x-content-type-options':'nosniff',
  'referrer-policy':'same-origin',
  'content-security-policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
};
const esc=value=>String(value).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c]);
const plain=(value,status)=>Response.json(value,{status,headers});
const page=(markup,status=200)=>new Response(`<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Постоянная запись · База памяти</title>
<style>body{background:#f5f6f1;color:#152a21;font:1rem/1.55 system-ui;margin:0}main{max-width:41rem;margin:clamp(2rem,9vw,5rem) auto;padding:0 1.3rem}h1{font:700 clamp(2rem,6vw,3rem)/1.14 system-ui}section{border:1px solid #d4dacf;border-radius:1.1rem;background:#fff;padding:1.2rem;margin:1.4rem 0}button{font:inherit;background:#164d38;color:white;border:0;border-radius:.6rem;padding:.8rem 1rem;cursor:pointer}a{color:#164d38}small{color:#466051}</style>
<main><small>ПРИВАТНЫЙ ТЕСТ · ТОЛЬКО ВЫМЫШЛЕННЫЕ ДАННЫЕ</small>
<h1>От реплики до записи в базе</h1><p>Генерация мысли и проверка используют заранее заданные ответы. Реальная модель не вызывается; свои записи сюда отправить нельзя.</p>
<form method="post" action="${path}"><button type="submit">Запустить тест</button></form>
${markup}<p><a href="/explore">Открыть тестовый граф</a> · <a href="/">К списку проверок</a></p></main></html>`,
{status,headers:{...headers,'content-type':'text/html; charset=utf-8'}});

export async function permanentPreview(request,env){
  const url=new URL(request.url);
  if(url.pathname!==path)return null;
  if(!request.headers.get('oai-authenticated-user-id'))return plain({error:'Sign in to the private test Site'},401);
  if(request.method==='POST'){
    if(!sameOriginPreviewPost(request))return request.headers.get('accept')?.includes('text/html')
      ? page('<section role="status">Запрос не поступил с этой страницы. Открой проверку заново и нажми кнопку здесь.</section>',403)
      : plain({error:'Origin mismatch'},403);
    const length=request.headers.get('content-length');
    const type=request.headers.get('content-type')?.split(';')[0];
    if((length!==null&&length!=='0')||(type&&type!=='application/x-www-form-urlencoded')){
      return plain({error:'This test accepts no user input'},400);
    }
    if(request.body){
      const reader=request.body.getReader();
      try{
        const item=await reader.read();
        if(!item.done&&item.value?.length)return plain({error:'This test accepts no user input'},400);
      }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    }
    try{
      const result=await runSyntheticPermanentSmoke(env.DB);
      if(!result.ok)return page('<section role="status">Результат теста пока не подтверждён.</section>',503);
      return new Response(null,{status:303,headers:{...headers,location:path}});
    }catch{return page('<section role="status">Тест не завершился. Сохранение не подтверждено.</section>',503);}
  }
  if(request.method!=='GET')return plain({error:'Use GET or POST'},405);
  try{
    const check=await getSyntheticPermanentSmoke(env.DB);
    if(!check.ok)return page('<section role="status">Сохранённого результата пока нет.</section>');
    const text=check.thought?.text??check.accepted?.text??'Вымышленная мысль принята и сохранена.';
    return page('<section aria-live="polite"><h2>Мысль сохранена</h2><p>'+esc(text)+'</p><p>Страница прочитала результат из постоянного хранилища. Отклонённый вариант не записан.</p></section>');
  }catch{return page('<section role="status">Постоянное хранилище недоступно.</section>',503);}
}
