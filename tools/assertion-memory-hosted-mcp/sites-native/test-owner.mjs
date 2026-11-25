// Read only: the existing TEST enrollment stays in the same D1 binding.
const scope='assertion-memory-preview:TEST:owner-v1';
const marker='private-synthetic-assertion-smoke/v1';
const readPin='SELECT owner_sha256 FROM memory_test_owner_binding WHERE singleton=1 AND scope=? AND EXISTS (SELECT 1 FROM memory_synthetic_smoke_environment WHERE singleton=1 AND purpose=?)';
const sha=/^[0-9a-f]{64}$/;
const bytes=new TextEncoder();
function equal(a,b){let acc=0;for(let i=0;i<64;i++) acc|=a.charCodeAt(i)^b.charCodeAt(i);return acc===0;}
export async function matchesEnrolledTestOwner(req,env){
  const id=req.headers.get('oai-authenticated-user-id')||'';
  if(!id||id.length>250||/[\s,]/.test(id)||
     /^__|^(?:synthetic|fixture|service|staging-principal)(?:-|:)/i.test(id)||
     env.MEMORY_MCP_TEST_OWNER_BINDING!=='d1'||env.MEMORY_MCP_TEST_OWNER_SHA256!==undefined)
    return false;
  try{
    const record=await env.DB.prepare(readPin).bind(scope,marker).first();
    if(!sha.test(record?.owner_sha256??''))return false;
    const raw=await crypto.subtle.digest('SHA-256',bytes.encode(scope+'\0'+id));
    const pin=Array.from(new Uint8Array(raw),v=>v.toString(16).padStart(2,'0')).join('');
    return equal(record.owner_sha256,pin);
  }catch{return false;}
}
