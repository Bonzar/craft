import test from 'node:test';
import assert from 'node:assert/strict';
import {attachNodeRemoval} from './graph-node-removal-overlay.mjs';

function fixture({enabled=true,history=false,reply,changes={}}={}){
  class Elem{
    constructor(){this.children=[];this.handlers={};this.dataset={};this.isConnected=true;}
    append(...items){this.children.push(...items);}
    querySelector(){return this.children.find(e=>e.dataset.ownerNodeRemoval!==undefined);}
    remove(){this.isConnected=false;anchor.children=anchor.children.filter(e=>e!==this);}
    setAttribute(){}
    addEventListener(event,handler){this.handlers[event]=handler;}
  }
  const anchor=new Elem, doc={getElementById:()=>anchor,createElement:()=>new Elem};
  const manifest={revision:'graph-1',selector:{mode:'current',history}};
  const card={revision:'graph-1',id:'current-card',is_current:true,thought_ref:{thought_id:'thought-exact',version:7},...changes};
  let reloads=0;
  const app={authenticated:true,manifest,selectedNodeId:card.id,loadGraph:async()=>{reloads++;},capabilities:enabled?{owner_graph_node_removal:{version:1,stage:'test'}}:{}};
  const calls=[];
  const send=async(path,init)=>{
    calls.push([path,init.method,JSON.parse(init.body),init.headers]);
    const res=reply?.(path,init);
    const result={stage:'test',thought_id:'thought-exact',thought_version:7,revision:'1'.repeat(32),scope:'2'.repeat(64),
      source_revision:(path.endsWith('/prepare')?'3':'4').repeat(64),...(path.endsWith('/prepare')?{csrf_token:'csrf-'+ '1'.repeat(33)}:{removed:true})};
    return res??{ok:true,json:async()=>result};
  };
  attachNodeRemoval(app,card,manifest,doc,send);
  return {app,anchor,calls,reloads:()=>reloads,controls:()=>anchor.children[0]?.children};
}

test('no action on disabled route, history or non-current node',()=>{
  for(const options of [{enabled:false},{history:true},{changes:{is_current:false}},{changes:{thought_ref:null}}])
    assert.equal(fixture(options).anchor.children.length,0);
});
test('exact same thought/version must be explicitly confirmed and authoritative commit refreshes',async()=>{
  const f=fixture(),[button,cancel,status]=f.controls();
  await button.handlers.click();
  assert.equal(button.textContent,'Подтвердить удаление');
  assert.match(status.textContent,/Исходные реплики/);
  assert.equal(f.calls.length,1);
  assert.equal(cancel.hidden,false);
  await button.handlers.click();
  assert.equal(f.calls.length,2);
  assert.deepEqual(f.calls[1].slice(0,3),['/api/v1/processing/graph-nodes/remove','PUT',
    {stage:'test',thought_id:'thought-exact',thought_version:7,expected_revision:'1'.repeat(32)}]);
  assert.match(f.calls[1][3]['X-CSRF-Token'],/^csrf-/);
  assert.equal(f.reloads(),1);
  assert.equal(button.hidden,true);
});
test('dependency denial is truthful and selecting another card prevents commit',async()=>{
  const f=fixture({reply:(path)=>path.endsWith('/prepare')&&{ok:false,status:409,json:async()=>({error:'dependent_thoughts'})}});
  const [b,,s]=f.controls();await b.handlers.click();assert.match(s.textContent,/опираются/);assert.equal(f.calls.length,1);
  const g=fixture();const btn=g.controls()[0];await btn.handlers.click();
  g.app.selectedNodeId='other';await btn.handlers.click();assert.equal(g.calls.length,1);
});
