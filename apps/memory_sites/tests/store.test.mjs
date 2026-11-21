import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {getGraph,getNode,importSnapshot,listOverviews,validateSnapshot,SnapshotError} from '../store.mjs';

const schema = readFileSync(new URL('../drizzle/0001_graph.sql',import.meta.url),'utf8');
const stamp = '2026-02-03T09:00:00+03:00';
function doc(revision='approved-revision-1') {
  const version=(id,member,summary) => ({version_id:id,published_at:stamp,member_ids:[member],summary});
  return {
    schema:'graph-viewer-source/v1', provider_id:'synthetic/approved', source_revision:revision, episodes:[],
    nodes:[
      {id:'alpha',text:'Лексикон 😀',scope:'synthetic',kind:'fact',recorded_at:stamp,valid_at:'2026-02-03',valid_precision:'day'},
      {id:'beta',text:'План, а не выполненная задача.',scope:'synthetic',kind:'plan',recorded_at:stamp},
    ],
    edges:[
      {id:'edge-a',from_id:'beta',to_id:'alpha',kind:'clarifies',recorded_at:stamp},
      {id:'edge-b',from_id:'beta',to_id:'alpha',kind:'clarifies',recorded_at:stamp},
    ],
    overviews:[
      {id:'parent',title:'Родительская тема',scopes:['synthetic'],versions:[version('v1','alpha','Первая'),version('v2','beta','Вторая')], current_version_id:'v2'},
      {id:'child',title:'Подвопрос',scopes:['synthetic'],versions:[version('child-v1','beta','Обзор')],current_version_id:'child-v1'},
    ],
    topic_links:[{id:'link',parent_id:'parent',child_id:'child',relation:'subquestion',
      recorded_at:stamp,provenance:{kind:'approved_snapshot'}}],
  };
}
function dbFixture() {
  const raw = new DatabaseSync(':memory:');
  raw.exec('PRAGMA foreign_keys=ON;'+schema);
  let batches=0;
  return {raw,get batches(){return batches}, db:{
    prepare(sql) {return {sql,args:[],bind(...args){return {...this,args}}};},
    async batch(statements) {
      batches++;
      raw.exec('BEGIN');
      try {
        const out=statements.map(({sql,args})=>{
          const statement=raw.prepare(sql);
          if (/^\s*(SELECT|WITH)\b/i.test(sql)) return {success:true,results:statement.all(...args)};
          statement.run(...args);
          return {success:true,results:[]};
        });
        raw.exec('COMMIT');
        return out;
      } catch (err) { raw.exec('ROLLBACK'); throw err; }
    },
  }};
}

test('import retains stable node and parallel directed edge IDs and overview versions', async()=>{
  const {db,raw}=dbFixture();
  try {
    assert.equal((await getGraph(db)).source_revision,null);
    const got=await importSnapshot(db,doc());
    assert.deepEqual(got,{provider_id:'synthetic/approved',source_revision:'approved-revision-1',nodes:2,edges:2,overviews:2,topic_links:1});
    const graph=await getGraph(db);
    assert.equal(graph.revision,'approved-revision-1');
    assert.equal(graph.nodes[0].text,'Лексикон 😀');
    assert.equal(graph.nodes[0].recorded_at,stamp);
    assert.equal(graph.edges.length,2);
    assert.deepEqual(graph.edges.map(x=>[x.id,x.from_id,x.to_id]),
      [['edge-a','beta','alpha'],['edge-b','beta','alpha']]);
    assert.equal((await getNode(db,'alpha')).edges.length,2);
    assert.equal(await getNode(db,'not-present'),null);
    const queries=await listOverviews(db);
    assert.equal(queries.overviews.find(o=>o.id==='parent').current_version_id,'v2');
    assert.deepEqual(queries.overviews.find(o=>o.id==='parent').versions.map(v=>v.version_id),['v1','v2']);
    assert.equal(queries.topic_links[0].relation,'subquestion');
    const page=await getGraph(db,{limit:1,offset:0});
    assert.deepEqual(page.nodes.map(x=>x.id),['alpha']);
    assert.equal(page.next_offset,1);
    assert.deepEqual(page.edges,[]);
    assert.equal((await listOverviews(db,{limit:1})).truncated,true);
    await assert.rejects(getGraph(db,{limit:501}),SnapshotError);
  } finally {raw.close();}
});

test('rejects raw sources, quotes, arbitrary provenance, missing references and cycles before D1 access', async()=>{
  const {db,raw}=dbFixture();
  try {
    for (const change of [
      d=>{d.episodes=[{id:'episode',text:'Must never leave local storage.'}];},
      d=>{d.nodes[0].evidence_refs=[{episode_id:'episode',start:0,end:2}];},
      d=>{d.nodes[0].actor='Private name';},
      d=>{d.nodes[0].session_id='private-session';},
      d=>{d.edges[0].source_quote='Private excerpt';},
      d=>{d.edges[0].to_id='missing';},
      d=>{d.nodes[0].valid_at='2026-02-30';},
      d=>{d.nodes[0].recorded_at='2026-02-03T09:00:00';},
      d=>{d.nodes[0].id='beta';},
      d=>{d.nodes[0].text='\uD800';},
      d=>{d.overviews[0].current_version_id='unknown';},
      d=>{d.topic_links[0].provenance.raw='Sensitive';},
      d=>{d.topic_links.push({...d.topic_links[0],id:'reverse',parent_id:'child',child_id:'parent'});},
    ]) {
      const sample=doc();
      change(sample);
      await assert.rejects(importSnapshot(db,sample),SnapshotError);
    }
    assert.equal(raw.prepare('SELECT COUNT(*) AS n FROM memory_graph_nodes').get().n,0);
    assert.equal((await getGraph(db)).nodes.length,0);
    const normalized=doc();
    normalized.nodes[0].actor=null;
    normalized.nodes[0].source=null;
    normalized.nodes[0].episode_id=null;
    normalized.nodes[0].evidence_refs=[];
    normalized.edges[0].source_quote='';
    const value=validateSnapshot(normalized);
    assert.equal(Object.hasOwn(value.nodes[0],'episode_id'),false);
    assert.equal(Object.hasOwn(value.edges[0],'source_quote'),false);
  } finally {raw.close();}
});

test('failed replacement rolls back deletes; successful replacement does not mix revisions',async()=>{
  const {db,raw}=dbFixture();
  try {
    await importSnapshot(db,doc());
    raw.exec(`CREATE TRIGGER forced_failure BEFORE INSERT ON memory_graph_nodes
      WHEN new.id='force-error' BEGIN SELECT RAISE(ABORT, 'forced'); END;`);
    const newer=doc('approved-revision-2');
    newer.nodes.push({id:'force-error',kind:'fact',text:'Synthetic',recorded_at:stamp});
    await assert.rejects(importSnapshot(db,newer),/forced/);
    assert.equal((await getGraph(db)).revision,'approved-revision-1');
    assert.equal((await getGraph(db)).nodes.length,2);
    newer.nodes.pop();
    newer.nodes[0].text='Новая версия';
    newer.edges=[];
    await importSnapshot(db,newer);
    const saved=await getGraph(db);
    assert.equal(saved.revision,'approved-revision-2');
    assert.equal(saved.nodes[0].text,'Новая версия');
    assert.deepEqual(saved.edges,[]);
  } finally {raw.close();}
});
