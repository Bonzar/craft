import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {runSyntheticPermanentSmoke} from '../synthetic_permanent_smoke.mjs';
import {readSyntheticSemanticGraphEvidence,readSyntheticSemanticProjection} from '../synthetic_semantic_projection.mjs';
import {admitRawCapture} from '../raw_capture.mjs';
import {encodeCanonicalThought} from '../semantic_encoding.mjs';

const migrations=['0002_queue','0003_publications','0004_raw_capture','0005_semantic_thoughts','0006_live_source_commit'];
function fixture(t,mark=true) {
  const raw=new DatabaseSync(':memory:');
  for (const file of migrations) raw.exec(readFileSync(new URL('../drizzle/'+file+'.sql',import.meta.url),'utf8'));
  if(mark) raw.exec(readFileSync(new URL('../smoke/0001_preview_marker.sql',import.meta.url),'utf8'));
  t.after(()=>raw.close());
  let batches=0;
  const db={
    prepare(sql) {return {sql,args:[],bind(...args){return {...this,args}}};},
    async batch(statements) {
      batches++;
      raw.exec('BEGIN IMMEDIATE');
      try {
        const result=statements.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');
        return result;
      } catch (err) {raw.exec('ROLLBACK');throw err;}
    },
  };
  return {raw,db,batches:()=>batches};
}
async function append(raw, material) {
  const {payload,content_hash}=await encodeCanonicalThought(material);
  raw.prepare('INSERT INTO memory_thought_versions VALUES (?,?,?,?)').run(material.id,material.version,payload,content_hash);
  raw.prepare('INSERT INTO memory_thoughts VALUES(?,?) ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version')
    .run(material.id,material.version);
}
function connect(raw,id,left,right) {
  const payload={id,source_id:left.id,source_version:left.version,target_id:right.id,target_version:right.version,
    meaning:'not part of an approved projection',created_at:left.created_at};
  raw.prepare('INSERT INTO memory_thought_links VALUES (?,?,?,?,?,?)').run(
    id,left.id,left.version,right.id,right.version,JSON.stringify(payload));
}

test('same marked D1 snapshot reads current exact synthetic thought; redacts source and rejected fixture',async t=>{
  const f=fixture(t);
  assert.deepEqual(await readSyntheticSemanticGraphEvidence(f.db),{document:null,versions:[]});
  assert.equal((await runSyntheticPermanentSmoke(f.db)).ok,true);
  const count=f.batches();
  const read=await readSyntheticSemanticGraphEvidence(f.db);
  assert.equal(f.batches()-count,1);
  assert.equal(read.document.nodes.length,1);
  assert.equal(read.document.nodes[0].scope,null); // original canonical origin lacks scope
  assert.equal(read.document.edges.length,0);
  assert.deepEqual(read.document.episodes,[]);
  assert.equal(read.versions[0].id,read.document.nodes[0].id);
  assert.equal(read.versions[0].content_hash,f.raw.prepare('SELECT content_hash FROM memory_thought_versions').get().content_hash);
  assert.doesNotMatch(JSON.stringify(read),/fixture:\/\/|wooden roof|metadata|episode_id|evidence_refs/);
  assert.deepEqual(await readSyntheticSemanticProjection(f.db),read.document);
});

test('retains exact canonical SHA derivation ID; stored links only; no retargeting old edges or cross-scope ancestry',async t=>{
  const f=fixture(t);
  assert.equal((await runSyntheticPermanentSmoke(f.db)).ok,true);
  const original=JSON.parse(f.raw.prepare('SELECT payload FROM memory_thought_versions').get().payload);
  const tainted=await admitRawCapture(f.db,{session_id:'foreign',message_id:'n',actor_id:'user',
    role:'user',text:'Never mix into fixture projection.',scope:'private',source:'fixture://foreigner'});
  const dependent={...original,id:"synthetic note/α'!",text:'A permitted dependent.',
    bases:[{thought_id:original.id,version:1,content_hash:'a'.repeat(64),meaning:'bounded evidence'}]};
  const other={...original,id:'exclude-me',text:'Exclude this thought',bases:[{event_id:tainted.event_id,content_hash:'b'.repeat(64)}]};
  const indirect={...original,id:'exclude-indirectly',text:'Exclude this dependent too',
    bases:[{thought_id:other.id,version:1,content_hash:'c'.repeat(64)}]};
  for(const material of [dependent,other,indirect]) await append(f.raw,material);
  connect(f.raw,'synthetic-link',original,dependent);
  connect(f.raw,'foreign-link',other,original);
  const doc=await readSyntheticSemanticProjection(f.db);
  assert.equal(doc.nodes.length,2);
  assert.equal(doc.edges.length,2);
  const expectedNode='thought-version:synthetic%20note%2F%CE%B1%27%21:1';
  const edge=doc.edges.find(edge=>edge.kind==='thought_basis');
  assert.equal(edge.from_id,expectedNode);
  assert.equal(doc.edges.find(edge=>edge.kind==='thought_relation').to_id,expectedNode);
  const oracle=spawnSync('python',['-c',`
import json,sys
from prototypes.assertion_memory.thought_graph import _digest,version_node_id
a,v,b = json.load(sys.stdin)
print('thought-basis:'+_digest([[a,v],0,b]))
print(version_node_id(a,v))
`],{cwd:fileURLToPath(new URL('../../../',import.meta.url)),encoding:'utf8',
    input:JSON.stringify([dependent.id,dependent.version,dependent.bases[0]])});
  assert.equal(oracle.status,0,oracle.stderr);
  assert.deepEqual(oracle.stdout.trim().split('\n'),[edge.id,edge.from_id]);
  await append(f.raw,{...original,version:2});
  const advanced=await readSyntheticSemanticProjection(f.db);
  assert.equal(advanced.nodes.length,1);
  assert.match(advanced.nodes[0].id,/:2$/);
  assert.deepEqual(advanced.edges,[]);
  assert.notEqual(advanced.source_revision,doc.source_revision);
});

test('refuses an ordinary database without the dedicated synthetic marker',async t=>{
  const {db}=fixture(t,false);
  await assert.rejects(readSyntheticSemanticProjection(db),/unavailable/);
});
