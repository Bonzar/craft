import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {createPreviewSearch} from '../preview_search.mjs';

function fixture(t,marked=true) {
  const sqlite=new DatabaseSync(':memory:');
  t.after(()=>sqlite.close());
  if(marked) sqlite.exec(readFileSync(new URL('../smoke/0001_preview_marker.sql',import.meta.url),'utf8'));
  sqlite.exec(readFileSync(new URL('../smoke/0002_preview_lexical.sql',import.meta.url),'utf8'));
  const statements=[];
  const DB={prepare(sql){
    statements.push(sql);const statement=sqlite.prepare(sql);
    return {first:()=>statement.get(),bind:(...args)=>({all:()=>({results:statement.all(...args)})})};
  }};
  const route=createPreviewSearch({owner:req=>req.headers.get('test-owner')==='1'});
  return {statements, call:(q,owner=true,method='GET')=>route(new Request('https://preview.invalid/preview/search'+q,{method,
    headers:owner?{'test-owner':'1'}:{}}),{DB})};
}

test('reads marked fixed corpus with exact history filtering and no query echo',async t=>{
  const {call,statements}=fixture(t);
  const r=await call('?q='+encodeURIComponent('ВЫМЫШЛЕННАЯ запад'));
  assert.equal(r.status,200);
  assert.deepEqual(await r.json(),{kind:'synthetic-graph-lexical',hits:[
    {id:'fixture-west',text:'Вымышленная команда перенесла стойку на запад.'}]});
  assert.equal(r.headers.get('cache-control'),'private, no-store');
  assert.deepEqual((await (await call('?q='+encodeURIComponent('востоке'))).json()).hits,[]);
  assert.deepEqual((await (await call('?q='+encodeURIComponent('запад" OR шкафчики'))).json()).hits,[]);
  assert(statements.every(sql=>/^SELECT /.test(sql)));
  assert(statements.every(sql=>!sql.includes('шкафчики')));
});

test('fails closed without owner or marker; rejects oversized and ambiguous input',async t=>{
  const a=fixture(t);
  assert.equal((await a.call('?q=word',false)).status,401);
  assert.equal((await a.call('?q=word',true,'POST')).status,405);
  assert.equal((await a.call('?q='+('x'.repeat(97)))).status,400);
  assert.equal((await a.call('?q=a&q=b')).status,400);
  assert.equal(a.statements.length,0);
  const unmarked=fixture(t,false);
  assert.equal((await unmarked.call('?q=word')).status,503);
  assert.equal(unmarked.statements.length,1);
});
