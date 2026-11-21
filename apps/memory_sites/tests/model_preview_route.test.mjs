import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {createLiveSourceCommitter} from '../live_source_commit.mjs';
import {createModelPreviewRoute,modelPreviewPath,modelPreviewPhrase} from '../model_preview_route.mjs';

const origin='https://synthetic.invalid';
const owner={'oai-authenticated-user-id':'owner'};
const path=origin+modelPreviewPath;
const thought='Дверь вымышленной теплицы сиреневая.';
const form=new URLSearchParams({utterance:modelPreviewPhrase}).toString();
const schemaFiles=['0002_queue','0003_publications','0004_raw_capture','0005_semantic_thoughts','0006_live_source_commit'];
function fixture(t,{marker=true}={}) {
  const raw=new DatabaseSync(':memory:');
  t.after(()=>raw.close());
  for(const name of schemaFiles)raw.exec(readFileSync(new URL('../drizzle/'+name+'.sql',import.meta.url),'utf8'));
  if(marker)raw.exec(readFileSync(new URL('../smoke/0001_preview_marker.sql',import.meta.url),'utf8'));
  const writes=[];
  const db={prepare(sql){return {sql,args:[],bind(...args){return {...this,args};}};},
    async batch(batch){
      raw.exec('BEGIN IMMEDIATE');
      try {
        const out=batch.map(({sql,args})=>{
          if(!sql.startsWith('SELECT '))writes.push(sql);
          return {success:true,results:raw.prepare(sql).all(...args)};
        });
        raw.exec('COMMIT');return out;
      } catch(e){raw.exec('ROLLBACK');throw e;}
    },
  };
  const count=table=>raw.prepare('SELECT count(*) n FROM '+table).get().n;
  return {db,raw,writes,count};
}
function call(route,db,{method='GET',body=form,headers={},target=path}={}) {
  return route(new Request(target,{method,body:method==='POST'?body:undefined,headers:{
    ...owner,...(method==='POST'?{'origin':origin,'content-type':'application/x-www-form-urlencoded'}:{}),...headers,
  }}),{DB:db});
}
function factory(calls,{rejected=false}={}) {
  return (_env,{authorizeSource})=>createLiveSourceCommitter({
    authorizeSource,
    construct:async request=>{
      calls.push(request.task);
      assert.ok(request.prompt.includes(modelPreviewPhrase));
      assert.doesNotMatch(request.prompt,/fixture-secret-never-sent/);
      return JSON.stringify({thoughts:[{text:thought,question:null,support_fragments:[0]}],needs_detail:null});
    },
    review:async request=>{
      calls.push(request.task);
      assert.ok(request.prompt.includes(thought));
      return JSON.stringify({verdict:rejected?'reject':'accept',text:'Synthetic test verifies wiring only.'});
    },
  });
}

test('absent provider disables form, never captures or contacts provider',async t=>{
  const {db,writes}=fixture(t);
  const route=createModelPreviewRoute({buildProvider(){throw Error('fake-key-not-for-output');}});
  const browser=await call(route,db,{headers:{accept:'text/html'}});
  assert.equal(browser.status,503);
  const html=await browser.text();
  assert.match(html,/Реплика не записана/);
  assert.doesNotMatch(html,/<form|fake-key/);
  const post=await call(route,db,{method:'POST'});
  assert.equal(post.status,503);
  assert.equal((await post.json()).state,'provider_unavailable');
  assert.deepEqual(writes,[]);
});

test('owner, same origin, fixed exact phrase and marker enforced before intake',async t=>{
  const {db,writes}=fixture(t);
  const missing=fixture(t,{marker:false});
  const calls=[];
  const route=createModelPreviewRoute({buildProvider:factory(calls)});
  for(const [args,status] of [
    [{method:'POST',headers:{'oai-authenticated-user-id':''}},401],
    [{method:'POST',headers:{origin:'https://foreign.invalid'}},403],
    [{method:'POST',headers:{'sec-fetch-site':'cross-site'}},403],
    [{method:'POST',headers:{'content-type':'application/json'}},400],
    [{method:'POST',body:'utterance=fixture-secret-never-sent'},400],
    [{method:'POST',body:form+'&utterance=another'},400],
    [{method:'POST',body:form+'&unknown=discard'},400],
    [{method:'POST',body:'x'.repeat(513)},400],
  ])assert.equal((await call(route,db,args)).status,status);
  assert.equal((await call(route,missing.db,{method:'POST'})).status,503);
  assert.deepEqual([...calls,...writes,...missing.writes],[]);
});

test('one exact phrase → separate model adapters → committed thought; GET and replay do not write',async t=>{
  const {db,raw,writes,count}=fixture(t);
  const calls=[];
  const route=createModelPreviewRoute({buildProvider:factory(calls)});
  const ready=await call(route,db,{headers:{accept:'text/html'}});
  assert.equal(ready.status,200);
  assert.match(await ready.text(),/<form/);
  assert.deepEqual(writes,[]);
  const [first,duplicate]=await Promise.all([call(route,db,{method:'POST'}),call(route,db,{method:'POST'})]);
  for(const reply of [first,duplicate]) {
    assert.equal(reply.status,303);
    assert.equal(reply.headers.get('location'),modelPreviewPath);
  }
  assert.deepEqual(calls,['Construct thought','Verify thought']);
  assert.equal(count('memory_raw_captures'),1);
  assert.equal(count('memory_job_publications'),0);
  assert.equal(count('memory_thought_versions'),1);
  const stored=JSON.parse(raw.prepare('SELECT payload FROM memory_thought_versions').get().payload);
  assert.equal(stored.text,thought);
  assert.equal(stored.bases[0].meaning,modelPreviewPhrase);
  const before=writes.length;
  const read=await (await call(createModelPreviewRoute(),db)).json();
  assert.equal(read.state,'saved');
  assert.deepEqual([read.thought.id,read.thought.version,read.thought.text],[stored.id,1,thought]);
  assert.equal((await call(route,db,{method:'POST'})).status,303);
  assert.equal(writes.length,before);
  assert.deepEqual(calls,['Construct thought','Verify thought']);
});

test('rejected model output never becomes a provisional or permanent publication, retries are not automatic',async t=>{
  const {db,writes,count}=fixture(t);
  const calls=[];
  const route=createModelPreviewRoute({buildProvider:factory(calls,{rejected:true})});
  assert.equal((await call(route,db,{method:'POST'})).status,303);
  const last=writes.length;
  assert.equal((await (await call(route,db)).json()).state,'not_saved');
  assert.equal((await call(route,db,{method:'POST'})).status,503);
  assert.equal(writes.length,last);
  assert.equal(count('memory_raw_captures'),1);
  assert.equal(count('memory_thought_versions')+count('memory_job_publications'),0);
  assert.deepEqual(calls,['Construct thought','Verify thought']);
});
