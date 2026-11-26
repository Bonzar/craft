// Offline build of an optional standalone TEST diagnostic; never touches Visual assets.
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

export async function renderProof(input){
  const target=new URL(input);
  if(target.origin!==input||target.protocol!=='https:'||!/^\d+\.\d+\.\d+\.\d+$/.test(target.hostname))
    throw new Error('Expected exact direct HTTPS IPv4 origin');
  const here=import.meta.url;
  const load=async name=>await readFile(new URL(name,here),'utf8');
  const reader=(await load('./direct-stream.mjs')).replace(/^export /gm,'');
  const proof=(await load('./direct-stream-proof.mjs')).replace(/^import .*\n/m,'').replace(/^export /gm,'');
  const script=`${reader}\n${proof}\n
const state=document.querySelector('output');
const control=document.querySelector('button');
control.addEventListener('click',async()=>{
  control.disabled=true;
  try{
    const session=await fetch('/api/v1/session',{credentials:'same-origin',cache:'no-store',redirect:'error'});
    if(!session.ok)throw new Error();
    const {authenticated,csrf_token}=await session.json();
    if(authenticated!==true||!/^[-_A-Za-z0-9]{43}$/.test(csrf_token))throw new Error();
    await proveDirectStream({csrf:csrf_token,allowedOrigin:${JSON.stringify(input)},update(result){
      const labels={connecting:'Подключаемся к потоку…',hello:'Первое событие: '+result.helloMs+' мс',
        proven:'Поток подтверждён: hello '+result.helloMs+' мс, heartbeat '+result.heartbeatMs+' мс',
        not_proven:'Поток не подтверждён'};
      state.textContent=labels[result.phase];
    }});
  }catch{state.textContent='Поток не подтверждён';}
  finally{control.disabled=false;}
});
`;
  if(script.includes('</script'))throw new Error('Invalid proof source');
  const hash=createHash('sha256').update(script).digest('base64');
  const policy=`default-src 'none'; script-src 'sha256-${hash}'; connect-src 'self' ${input}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`;
  return {html:`<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Проверка потока TEST</title><h1>Проверка потока TEST</h1><button type="button">Проверить поток</button><p><output aria-live="polite">Проверка ещё не запущена</output></p><script>${script}</script></html>\n`,policy};
}
if(process.argv[1]&&pathToFileURL(process.argv[1]).href===import.meta.url){
  const output=await renderProof(process.argv[2]);
  process.stdout.write(JSON.stringify(output)+'\n');
}
