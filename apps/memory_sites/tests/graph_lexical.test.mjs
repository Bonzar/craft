import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { graphNodeLexical } from '../graph_lexical.mjs';

test('node lexical SQL matches actual upstream search across history, case and tokens',()=>{
  const probes=[
    {query:'мир',history:false},
    {query:'МИР',history:true},
    {query:'мир лес',history:true},
    {query:'Новый маршрут',history:false},
    {query:'"старый" OR мир',history:true},
    {query:'___',history:false},
    {query:'🙂 ` !',history:false},
    {query:'сохранять',history:true},
  ];
  const cases=probes.map(p=>({...p,compiled:graphNodeLexical(p)}));
  const cwd=fileURLToPath(new URL('../../../',import.meta.url));
  const input=JSON.stringify(cases);
  const result=JSON.parse(execFileSync(process.env.PYTHON||'python',
    ['-m','apps.memory_sites.tests.graph_lexical_oracle'],{cwd,encoding:'utf8',input}));
  assert.deepEqual(result.actual,result.expected);
  assert(result.expected.some(rows=>rows.length));
  assert(result.expected.some(rows=>rows.length===0));
});

test('rejects invalid query and pagination and binds all input',()=>{
  assert.throws(()=>graphNodeLexical({query:'  '}),/invalid_query/);
  assert.throws(()=>graphNodeLexical({query:'word',offset:-1}),/invalid_pagination/);
  const value=graphNodeLexical({query:'a" OR b; DROP TABLE',limit:1});
  assert.equal(value.values[0],'"a" AND "OR" AND "b" AND "DROP" AND "TABLE"');
  assert.equal(value.sql.includes('DROP TABLE'),false);
});
