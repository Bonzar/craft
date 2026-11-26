import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const source=await readFile(new URL('./device-enrollment.js',import.meta.url),'utf8');
const fp='a'.repeat(64),rev='b'.repeat(32),next='d'.repeat(32),csrf='mock_csrf',proof='123.'+'e'.repeat(64);
const base='/api/v1/processing/devices';
const delay=()=>new Promise(r=>setImmediate(r));
function harness({cap=true,owner=true,stage='test',resultStage=stage,confirmStage=stage,commit=true}={}){
  class Elem {
    hidden=true;value='';items=[];listeners={};textContent='';
    addEventListener(kind,fun){this.listeners[kind]=fun;}
    append(e){this.items.push(e);}
    replaceChildren(){this.items=[];}
    focus(){}
    reset(){}
  }
  const map=new Map(),calls=[];
  const get=id=>{if(!map.has(id))map.set(id,new Elem());return map.get(id);};
  const row={label:'<device>',fingerprint:'c'.repeat(64),revocable:true};
  runInNewContext(source,{document:{getElementById:get,createElement:()=>new Elem()},
    AbortController,addEventListener(){},fetch:async(url,options)=>{
      assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');
      const body=options.body===undefined?null:JSON.parse(options.body);
      calls.push({url,method:options.method,token:options.headers['X-CSRF-Token'],body});
      if(url==='/api/v1/session')return Response.json({authenticated:owner,csrf_token:csrf});
      if(url.startsWith('/api/v1/capabilities?'))return Response.json({stage,capabilities:{owner_machine_devices_v1:cap}});
      if(url===base+'/prepare')return Response.json({stage:confirmStage,revision:rev,action:body.action,fingerprint:body.fingerprint,operation_proof:proof});
      if(options.method==='PUT')return Response.json({stage,revision:next,action:body.action,fingerprint:body.fingerprint,committed:commit});
      return Response.json({schema:'owner-machine-devices/v1',stage:resultStage,revision:rev,items:[row]});
    }});
  return {get,calls,action:(id,event='click')=>get(id).listeners[event]({preventDefault(){}}),
    enroll(code){get('owner-device-label').value='test laptop';get('owner-device-fingerprint').value=code;get('owner-device-enroll').listeners.submit({preventDefault(){}});}};
}
test('capability and existing owner session are both required',async()=>{
  for(const opt of [{cap:false},{owner:false},{resultStage:'prod'}]){
    const h=harness(opt);h.action('visual-queue-open');await delay();
    assert.equal(h.get('owner-devices').hidden,true);
    assert.equal(h.calls.some(x=>x.method!=='GET'),false);
  }
});
test('refuses raw key, prepares exact fingerprint, commits only after owner click',async()=>{
  const h=harness({});h.action('visual-queue-open');await delay();assert.equal(h.get('owner-devices').hidden,false);
  h.enroll(fp);await delay();assert.equal(h.calls.some(x=>x.method!=='GET'),false);
  h.enroll('sha256:'+fp);await delay();
  const preview=h.calls.find(x=>x.method==='POST');
  assert.deepEqual(preview,{url:base+'/prepare',method:'POST',token:csrf,body:{stage:'test',expected_revision:rev,action:'enroll',fingerprint:fp,label:'test laptop'}});
  assert.equal(h.calls.some(x=>x.method==='PUT'),false);
  h.action('owner-device-apply');await delay();h.action('owner-device-apply');await delay();
  const writes=h.calls.filter(x=>x.method==='PUT');assert.equal(writes.length,1);
  assert.deepEqual(writes[0],{...preview,url:base,method:'PUT',token:proof});
  assert.match(h.get('owner-devices-feedback').textContent,/подключено/);
});
test('foreign proof cannot commit, cancellation and close discard preview',async()=>{
  for(const action of ['foreign','cancel','close']){
    const h=harness(action==='foreign'?{confirmStage:'prod'}:{});
    h.action('visual-queue-open');await delay();h.enroll('sha256:'+fp);await delay();
    if(action==='cancel')h.action('owner-device-cancel');
    if(action==='close')h.action('visual-queue','close');
    h.action('owner-device-apply');await delay();
    assert.equal(h.calls.some(x=>x.method==='PUT'),false);
  }
});
test('revoke managed device requires explicit confirmation and never assumes failure is success',async()=>{
  const h=harness({commit:false});h.action('visual-queue-open');await delay();
  const row=h.get('owner-devices-list').items[0];
  assert.match(row.items[0].textContent,/<device>/);
  row.items[1].listeners.click();await delay();
  assert.equal(h.calls.find(x=>x.method==='POST').body.action,'revoke');
  assert.equal(h.calls.some(x=>x.method==='PUT'),false);
  h.action('owner-device-apply');await delay();
  assert.match(h.get('owner-devices-feedback').textContent,/Не удалось/);
  assert.equal(h.get('owner-device-enroll').hidden,true);
});
