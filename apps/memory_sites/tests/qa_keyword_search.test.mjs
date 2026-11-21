import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createQaKeywordSearch} from '../qa_keyword_search.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const oracle=fileURLToPath(new URL('./qa_keyword_search_oracle.py',import.meta.url));
const f=JSON.parse(execFileSync('python',[oracle],{cwd:root,env:{...process.env,PYTHONPATH:root},encoding:'utf8'}));
const db=new DatabaseSync(':memory:');
db.exec('CREATE TABLE nodes(id,ordinal INTEGER PRIMARY KEY,text,flags);CREATE VIRTUAL TABLE search USING fts5(text);CREATE VIRTUAL TABLE map_search USING fts5(question,text,topic UNINDEXED,topic_id UNINDEXED)');
for(const [sql,values] of [['INSERT INTO nodes VALUES(?,?,?,?)',f.nodes],['INSERT INTO search(rowid,text) VALUES(?,?)',f.search],['INSERT INTO map_search(rowid,question,text,topic,topic_id) VALUES(?,?,?,?,?)',f.maps]]){
  const stmt=db.prepare(sql);for(const row of values)stmt.run(...row);
}
const api=createQaKeywordSearch({authorize:r=>r.headers.has('x-synthetic-owner'),loadRevision:id=>id===f.revision?{
  all:(sql,args)=>db.prepare(sql).all(...args),history:false,topics:new Map(f.topics.map(t=>[t.index,t]))
}:null});
test('synthetic graph keyword results, overview ordering, cursors and errors agree with original HTTP',async()=>{
  for(const [path,status,body] of f.cases){
    const response=await api(new Request('https://test.example'+path,{headers:{'x-synthetic-owner':'1'}}));
    assert.equal(response.status,status,path);
    const actual=await response.json();
    if(status===409)assert.equal(actual.error.code,body.error.code,path);
    else assert.deepEqual(actual,body,path);
  }
});
test('keyword projection is owner only and GET only',async()=>{
  const path='https://test.example/api/v1/search';
  assert.equal((await api(new Request(path))).status,401);
  assert.equal((await api(new Request(path,{method:'POST',headers:{'x-synthetic-owner':'1'}}))).status,405);
});
