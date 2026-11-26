import { apiUrl, get, response } from './api.ts';
import { topicsSchema } from './contracts.ts';
import type { Manifest, Topics } from './contracts.ts';

export class BootError extends Error {}
const ms={session:12000,manifest:24000,payload:18000,worker:15000,presentation:20000};

export function abortError():DOMException {return new DOMException('Aborted','AbortError');}

/** Timeout covers headers, streaming body and worker/render awaits, including
 * WebKit implementations that do not settle a blocked operation on abort. */
export async function bootStep<T>(stage:keyof typeof ms,message:string,
  action:(signal:AbortSignal)=>Promise<T>,parent?:AbortSignal, timeout=ms[stage]):Promise<T>{
  if(parent?.aborted)throw abortError();
  const step=new AbortController();
  let reject!:(err:Error)=>void;
  const stop=new Promise<never>((_,no)=>{reject=no;});
  const cancel=()=>{reject(abortError());step.abort();};
  const timer=setTimeout(()=>{reject(new BootError(message));step.abort();},timeout);
  parent?.addEventListener('abort',cancel,{once:true});
  try{return await Promise.race([stop,Promise.resolve().then(()=>{
    if(step.signal.aborted)throw abortError();
    return action(step.signal);
  })]);}
  catch(error){step.abort();throw error;}
  finally{clearTimeout(timer);parent?.removeEventListener('abort',cancel);}
}

export async function bootBuffers(manifest:Manifest,signal:AbortSignal):Promise<[ArrayBuffer,ArrayBuffer,Topics]> {
  const binary=async(path:string,length:number):Promise<ArrayBuffer>=>{
    const result=await response(apiUrl(path),signal,{headers:{Accept:'application/octet-stream'}});
    if(result.headers.get('Content-Type')?.split(';',1)[0]?.trim().toLowerCase()!=='application/octet-stream'){
      throw new BootError('Источник вернул данные карты в неподходящем формате.');
    }
    const data=await result.arrayBuffer();
    if(data.byteLength!==length)throw new BootError('Источник вернул неполные данные карты.');
    return data;
  };
  return Promise.all([binary(manifest.nodes.url,manifest.nodes.byte_length),
    binary(manifest.edges.url,manifest.edges.byte_length),get(manifest.topics_url,topicsSchema,signal)]);
}
