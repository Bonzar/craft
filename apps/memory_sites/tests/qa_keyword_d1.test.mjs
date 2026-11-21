import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {keywordSeed} from '../scripts/qa_keyword_seed.mjs';
import {createQaVisualRoute} from '../qa_visual_route.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const oracle=fileURLToPath(new URL('./qa_frozen_oracle.py',import.meta.url));
const original=JSON.parse(execFileSync('python',[oracle,'--preview-overviews'],{cwd:root,env:{...process.env,PYTHONPATH:root},encoding:'utf8'}));
const {index,sql}=keywordSeed(original);
const owner={'oai-authenticated-user-id':'fixture-owner'};
function make(){
  const snapshot={...original,keyword_index:index};
  delete snapshot.keyword_projection;
  const graph=createQaVisualRoute({assets:{'/index.html':{base64:btoa('fixture'),type:'text/html'}},snapshot});
  const db=new DatabaseSync(':memory:');
  const DB={prepare(query){const op=(...args)=>({async all(){return {results:db.prepare(query).all(...args)}}});return {bind:op,...op()};},
    batch:statements=>Promise.all(statements.map(s=>s.all()))};
  const get=(path,headers=owner)=>graph(new Request('https://private.example'+path,{headers}),{DB});
  return {db,get};
}
const capability=async (get)=>(await (await get('/api/v1/capabilities')).json()).capabilities.keyword_search;
test('original frozen graph, exact Python keyword pages and one original saved overview share isolated pinned D1 search',async()=>{
  const {db,get}=make();
  assert.equal(await capability(get),false);
  assert.equal((await get(original.keyword_cases[0][0])).status,503);
  db.exec(sql);db.exec(sql); // idempotent registration of same frozen fixture
  assert.equal(await capability(get),true);
  const manifest=await(await get('/api/v1/graph')).json();
  assert.equal(manifest.revision,index.revision);
  assert.ok(original.keyword_cases.some(([,s,b])=>s===200&&b.topic_hits.length&&b.next_cursor));
  for(const [path,status,body] of original.keyword_cases){
    const response=await get(path);
    assert.equal(response.status,status,path);
    assert.deepEqual(await response.json(),body,path);
  }
  const first=Object.values(original.overviews)[0];
  const overview=await(await get('/api/v1/overviews/'+encodeURIComponent(first.topic_id)+'?revision='+index.revision)).json();
  assert.deepEqual(overview,first);
  assert.equal((await get(original.keyword_cases[0][0],{})).status,401);
  assert.equal((await get(original.keyword_cases[0][0],{...owner,'sec-fetch-site':'cross-site'})).status,401);
  db.exec('DELETE FROM qa_kw_fts WHERE rowid=0');
  assert.equal(await capability(get),false);
  assert.equal((await get(original.keyword_cases[0][0])).status,503);
  db.exec(sql);
  db.exec("UPDATE qa_kw_marker SET revision='mismatch'");
  assert.equal(await capability(get),false);
  assert.equal((await get(original.keyword_cases[0][0])).status,503);
});
