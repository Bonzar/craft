import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
import {DatabaseSync} from 'node:sqlite';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {frozenQaApi} from '../qa_frozen_loader.mjs';
import {runSyntheticPermanentSmoke} from '../synthetic_permanent_smoke.mjs';
import {loadSyntheticQaRevision,readSyntheticQaWire,SyntheticQaWireError} from '../synthetic_qa_wire.mjs';

function fixture(t,mark=true) {
  const raw=new DatabaseSync(':memory:');
  for(const file of ['0002_queue','0003_publications','0004_raw_capture','0005_semantic_thoughts','0006_live_source_commit'])
    raw.exec(readFileSync(new URL('../drizzle/'+file+'.sql',import.meta.url),'utf8'));
  if(mark) raw.exec(readFileSync(new URL('../smoke/0001_preview_marker.sql',import.meta.url),'utf8'));
  t.after(()=>raw.close());
  const statements=[];
  const db={
    prepare(sql){return {sql,args:[],bind(...args){return {...this,args};}};},
    async batch(batch){
      statements.push(...batch.map(x=>x.sql));
      raw.exec('BEGIN IMMEDIATE');
      try {
        const result=batch.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');
        return result;
      } catch(e){raw.exec('ROLLBACK');throw e;}
    }
  };
  return {raw,db,statements};
}

test('no fixed verified proof means fail closed and no request could write',async t=>{
  for(const mark of [true,false]){
    const {db,statements}=fixture(t,mark);
    await assert.rejects(readSyntheticQaWire(db),e=>e instanceof SyntheticQaWireError&&
      e.status===503&&e.code==='synthetic_graph_unavailable');
    assert.ok(statements.every(x=>/^SELECT /.test(x)));
  }
});

test('exact one-node QA wire from actual commit, stable read model, stale revisions and private raw transport',async t=>{
  const {db,statements}=fixture(t);
  const proof=await runSyntheticPermanentSmoke(db);
  const after=statements.length;
  const snap=await readSyntheticQaWire(db);
  const {manifest,cards,topics,capabilities,artifacts}=snap;
  const {revision}=manifest;
  assert.deepEqual(await loadSyntheticQaRevision(db,revision),snap);
  for(const id of ['0'.repeat(30),'invalid']) await assert.rejects(loadSyntheticQaRevision(db,id),e=>
    e.status===409&&e.code==='revision_expired');
  assert.ok(statements.slice(after).every(x=>/^(?:SELECT|WITH) /.test(x)));
  assert.deepEqual([manifest.counts.nodes,manifest.counts.visible_nodes,manifest.counts.edges,manifest.counts.topics],[1,1,0,0]);
  assert.equal(manifest.provider_id,capabilities.provider_id);
  assert.equal(topics.revision,revision);
  assert.deepEqual([topics.topics,topics.links,topics.memberships],[[],[],[]]);
  assert.equal(cards[0].card.id,'thought-version:'+proof.accepted.thought_id+':1');
  assert.equal(cards[0].card.text,proof.accepted.text);
  assert.deepEqual(cards[0].card.sources,[]);
  assert.doesNotMatch(JSON.stringify(cards),/fixture:\/\/|event-|wooden roof|metal roof/);
  assert.equal(artifacts['nodes.bin'].identity.length,16);
  assert.equal(artifacts['edges.bin'].identity.length,0);
  for(const part of Object.values(artifacts)) assert.deepEqual(gunzipSync(part.gzip),Buffer.from(part.identity));
  const route=frozenQaApi(snap,req=>req.headers.get('authorization')==='test'?'fixture':null,{rawTransport:true});
  const transport=(path,authorized=true)=>route(new Request('https://fixture.invalid'+path,{
    headers:{'Accept-Encoding':'gzip, br',...(authorized?{authorization:'test'}:{})},
  }));
  assert.equal((await transport(manifest.nodes.url,false)).status,401);
  const response=await transport(manifest.nodes.url);
  assert.equal(response.headers.get('content-encoding'),null);
  assert.equal((await response.arrayBuffer()).byteLength,16);
  const ordinal=await (await transport('/api/v1/graph/'+revision+'/nodes/0')).json();
  const stable=await (await transport('/api/v1/nodes/'+encodeURIComponent(cards[0].card.id)+'?revision='+revision)).json();
  assert.deepEqual(ordinal,stable);
  assert.equal((await transport('/api/v1/graph/'+'f'.repeat(30)+'/nodes.bin')).status,409);
});

test('material graph semantics and exact bytes match the original CanonicalThoughtSource oracle',async t=>{
  const {db,raw}=fixture(t);
  await runSyntheticPermanentSmoke(db);
  const output=await readSyntheticQaWire(db);
  const material=raw.prepare(`SELECT c.raw_json,v.payload FROM memory_live_source_commits r
    JOIN memory_raw_captures c ON c.event_id=r.job_id
    JOIN memory_thought_versions v ON v.thought_id=r.thought_id AND v.version=1`).get();
  const oracle=spawnSync('python',['-c',`
import json,tempfile,time,sys
from pathlib import Path
from starlette.testclient import TestClient
from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.providers.canonical_thoughts import CanonicalThoughtSource
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import Thought, ThoughtStore
from prototypes.assertion_memory.thought_runtime import initialize_empty
from prototypes.assertion_memory.queue import CaptureQueue
data=json.load(sys.stdin)
raw=json.loads(data['raw_json']); thought=Thought(**json.loads(data['payload']))
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder); p=root/'memory.db'
    with MemoryStore(p) as storage:
        initialize_empty(storage)
        queue=CaptureQueue(storage)
        event=queue.capture_event(raw['session_id'],raw['turn_id'],raw['prompt'],source=raw['source'],
            actor=raw['actor'],message_id=raw['turn_id'],scope=raw['scope'],metadata=raw['metadata'],recorded_at=raw['recorded_at'])
        assert event.id==raw['id']
        thoughts=ThoughtStore(storage)
        with storage.transaction(write=True) as db:
            thoughts._insert(db,thought)
    source=CanonicalThoughtSource(p,root/'source',provider_id='fixture-oracle')
    password='synthetic-test-only'
    with TestClient(create_app(None,root/'cache',password=password,source=source,synthetic_fixture=True)) as client:
        client.post('/api/v1/session',json={'password':password},headers={'origin':'http://testserver'}).raise_for_status()
        for _ in range(100):
            reply=client.get('/api/v1/graph')
            if reply.status_code!=202: break
            time.sleep(.02)
        reply.raise_for_status()
        m=reply.json()
        card=client.get('/api/v1/graph/'+m['revision']+'/nodes/0').json()
        topics=client.get(m['topics_url']).json()
        nodes=client.get(m['nodes']['url']).content.hex()
        edges=client.get(m['edges']['url']).content.hex()
        print(json.dumps(dict(manifest=m,card=card,topics=topics,nodes=nodes,edges=edges)))
`],{cwd:fileURLToPath(new URL('../../../',import.meta.url)),input:JSON.stringify(material),encoding:'utf8'});
  assert.equal(oracle.status,0,oracle.stderr);
  const expected=JSON.parse(oracle.stdout);
  assert.deepEqual(output.manifest.counts,expected.manifest.counts);
  assert.deepEqual(output.manifest.selector,expected.manifest.selector);
  for(const key of ['version','stable','semantic','extent']) assert.equal(output.manifest.layout[key],expected.manifest.layout[key]);
  for(const side of ['min','max']) for(let a=0;a<3;a++)
    assert.ok(Math.abs(output.manifest.bounds[side][a]-expected.manifest.bounds[side][a])<1e-10);
  for(const k of ['id','ordinal','entity_type','thought_ref','is_current','label','full_title','text','text_revision',
    'text_total','next_text_offset','topic_ids','status','time','scope']) assert.deepEqual(output.cards[0].card[k],expected.card[k],k);
  for(const k of ['topics','links','memberships','completeness']) assert.deepEqual(output.topics[k],expected.topics[k]);
  for(const k of ['nodes','edges']) assert.equal(Buffer.from(output.artifacts[k+'.bin'].identity).toString('hex'),expected[k]);
});
