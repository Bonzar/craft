// Integration layer for the existing site's owner proof. Import the project's
// real matchesEnrolledOwner and pass it here, never a request supplied token.
import {makeTestPause} from './native-pause.mjs';
const HEX=/^[0-9a-f]{64}$/;
const te=new TextEncoder();
const digest=async s=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',te.encode(s))),x=>x.toString(16).padStart(2,'0')).join('');
const equal=(a,b)=>{let d=0;for(let i=0;i<64;i++)d|=a.charCodeAt(i)^b.charCodeAt(i);return d===0;};
export function attachTestPause({origin,gateway,legacy,withinEnrolledOwner,fetcher}){
 if(typeof legacy?.fetch!=='function'||typeof withinEnrolledOwner!=='function')throw Error('Use exact TEST Sites owner binding');
 const handle=makeTestPause({origin,gateway,fetcher,owner:async(req,env)=>{
  const id=req.headers.get('oai-authenticated-user-id')??'';
  const email=(req.headers.get('oai-authenticated-user-email')??'').trim().toLowerCase();
  const pin=env.MEMORY_GRAPH_OWNER_EMAIL_SHA256??'';
  if(!id||id.length>250||/[\s,]/.test(id)||!email||email.length>512||!HEX.test(pin)||
     !equal(await digest(email),pin)||await withinEnrolledOwner(req,env)!==true)return null;
  return digest('native-queue-pause-test-v1\0'+id);
 }});
 return {async fetch(req,env){return (await handle(req,env))??legacy.fetch(req,env);}};
}
