import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';

const digest='a'.repeat(64);
const clock='2027-01-01T00:00:00.000000+00:00';
const rawEvent={id:'event-cc5c6ff825bc6db56b1734a7d5fc600b77d2dfb1449abf4e15fa10fb0282c0c3',session_id:'session',turn_id:'turn',actor:'user',
  prompt:'The 🍀 fixture.',source:'test',scope:'default',recorded_at:clock,metadata:{role:'user',message_id:'turn'}};
const basisRaw={event_id:rawEvent.id,span:{start:4,end:5},content_hash:digest,meaning:'🍀'};
const basisThought=(id,version=1)=>({thought_id:id,version,content_hash:digest});
function database(t) {
  const db=new DatabaseSync(':memory:',{enableForeignKeyConstraints:true});
  t.after(()=>db.close());
  const migrations=['0002_queue','0004_raw_capture','0005_semantic_thoughts'];
  for (const name of migrations) {
    // Exercise the same breakpoints used by the D1 packager.
    for(const statement of readFileSync(new URL('../drizzle/'+name+'.sql',import.meta.url),'utf8').split('--> statement-breakpoint')) {
      db.exec(statement);
    }
  }
  db.prepare(`INSERT INTO memory_raw_captures
    (event_id,session_id,message_id,receipt,raw_json,raw_sha256,captured_at)
    VALUES (?,?,?,?,?,?,?)`).run(rawEvent.id,rawEvent.session_id,rawEvent.turn_id,digest,JSON.stringify(rawEvent),digest,0);
  return db;
}
function publish(db,id,version,bases,options={}) {
  const payload={id,version,text:'Synthetic thought.',question:null,origin:{kind:'fixture'},
    bases,metadata:{},created_at:clock,...options};
  db.prepare('INSERT INTO memory_thought_versions VALUES (?,?,?,?)').run(id,version,JSON.stringify(payload),digest);
  return payload;
}
function head(db,id,version) {
  return db.prepare(`INSERT INTO memory_thoughts VALUES (?,?)
    ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version`).run(id,version);
}

test('stores a canonical full Thought and projects exact historical/raw evidence references',t=>{
  const db=database(t);
  const v1=publish(db,'thought-x',1,[basisRaw]);
  head(db,'thought-x',1);
  const v2=publish(db,'thought-x',2,[basisThought('thought-x'),basisRaw]);
  head(db,'thought-x',2);
  const latest=db.prepare(`SELECT v.payload FROM memory_thoughts h JOIN memory_thought_versions v
    ON h.id=v.thought_id AND h.current_version=v.version`).get();
  assert.deepEqual(JSON.parse(latest.payload),v2);
  assert.deepEqual(JSON.parse(db.prepare('SELECT payload FROM memory_thought_versions WHERE version=1').get().payload),v1);
  const refs=db.prepare(`SELECT basis_index,basis_event_id,basis_thought_id,basis_thought_version,span_start,span_end
    FROM memory_thought_version_bases WHERE thought_id='thought-x' AND version=2 ORDER BY basis_index`).all();
  assert.deepEqual(refs.map(r=>({...r})),[
    {basis_index:0,basis_event_id:null,basis_thought_id:'thought-x',basis_thought_version:1,span_start:null,span_end:null},
    {basis_index:1,basis_event_id:rawEvent.id,basis_thought_id:null,basis_thought_version:null,span_start:4,span_end:5},
  ]);
});

test('future/missing refs and malformed basis roll back, versions and projections cannot drift',t=>{
  const db=database(t);
  assert.throws(()=>publish(db,'self',1,[basisThought('self')]),/must already exist/);
  assert.throws(()=>publish(db,'missing',1,[{...basisRaw,event_id:'missing'}]),/must already exist/);
  assert.throws(()=>publish(db,'both',1,[{...basisRaw,...basisThought('some')}]),/invalid pinned/);
  assert.throws(()=>publish(db,'bad-span',1,[{...basisRaw,span:{start:5,end:4}}]),/CHECK constraint/);
  assert.throws(()=>publish(db,'zero',0,[]),/CHECK constraint/);
  assert.equal(db.prepare('SELECT count(*) n FROM memory_thought_versions').get().n,0);
  assert.equal(db.prepare('SELECT count(*) n FROM memory_thought_version_bases').get().n,0);
  const v1=publish(db,'root',1,[basisRaw]);
  head(db,'root',1);
  assert.throws(()=>head(db,'root',2),/FOREIGN KEY/);
  assert.throws(()=>db.prepare('INSERT OR REPLACE INTO memory_thought_versions VALUES (?,?,?,?)')
    .run('root',1,JSON.stringify({...v1,text:'changed'}),digest),/already exists/);
  assert.throws(()=>db.exec("UPDATE memory_thought_versions SET content_hash='changed'"),/immutable/);
  assert.throws(()=>db.exec("UPDATE memory_thought_version_bases SET span_end=22"),/immutable/);
  assert.throws(()=>db.exec("DELETE FROM memory_thought_version_bases"),/owning version/);
  assert.throws(()=>db.exec("DELETE FROM memory_raw_captures"),/FOREIGN KEY/);
  // An authorized purge path can later remove leaf history in dependency
  // order. Deleting the owning version cleans up its projection.
  db.exec("DELETE FROM memory_thoughts");
  db.exec("DELETE FROM memory_thought_versions");
  assert.equal(db.prepare('SELECT count(*) n FROM memory_thought_version_bases').get().n,0);
});

test('directed link cycles preserve exact endpoint versions; retirement keeps historical thought',t=>{
  const db=database(t);
  publish(db,'a',1,[basisRaw]);
  publish(db,'b',1,[basisThought('a')]);
  head(db,'a',1);
  head(db,'b',1);
  const link=(id,from,to,version=1)=>{
    const payload={id,source_id:from,source_version:1,target_id:to,target_version:version,
      meaning:'Synthetic connection',source_span:null,target_span:null,metadata:{},created_at:clock};
    db.prepare('INSERT INTO memory_thought_links VALUES (?,?,?,?,?,?)').run(id,from,1,to,version,JSON.stringify(payload));
  };
  link('ab','a','b');
  link('ba','b','a');
  assert.throws(()=>link('bad','a','b',2),/FOREIGN KEY/);
  const retire=db.prepare('INSERT INTO memory_thought_retirements (thought_id,version,reason,proof,retired_at) VALUES (?,?,?,?,?)');
  assert.throws(()=>retire.run('a',1,'test','{"verification":"rejected","expected_versions":{"a":1}}',clock),/CHECK/);
  db.exec('BEGIN');
  retire.run('a',1,'test','{"verification":"accepted","expected_versions":{"a":1}}',clock);
  db.exec("DELETE FROM memory_thoughts WHERE id='a'");
  db.exec('COMMIT');
  assert.equal(db.prepare("SELECT count(*) n FROM memory_thought_versions WHERE thought_id='a'").get().n,1);
  assert.equal(db.prepare("SELECT count(*) n FROM memory_thoughts WHERE id='a'").get().n,0);
  assert.equal(db.prepare("SELECT count(*) n FROM memory_thought_links").get().n,2);
});

test('preserves pinned evidence and content hashes from a real Python ThoughtStore fixture', t=>{
  const db=database(t);
  // Synthetic original fixture: ThoughtStore._pin_basis and _hash(asdict(thought)).
  const basisHash='dba624c82f94e30ef1a027933a61ac31338313c6e0e006e13e3ea06186b6b26a';
  const thoughtHash='0fb54a606bc76ef2a4fcfc72e9e8b3f0a6e2ea46b33369484aaaa81c1a00fc0b';
  const payload={id:'thought-oracle',version:1,text:'Synthetic thought.',question:null,origin:{kind:'fixture'},
    bases:[{...basisRaw,content_hash:basisHash}],metadata:{},created_at:clock};
  db.prepare('INSERT INTO memory_thought_versions VALUES (?,?,?,?)')
    .run(payload.id,payload.version,JSON.stringify(payload),thoughtHash);
  assert.equal(db.prepare('SELECT content_hash FROM memory_thought_versions').get().content_hash,thoughtHash);
  assert.equal(db.prepare('SELECT content_hash FROM memory_thought_version_bases').get().content_hash,basisHash);
});
