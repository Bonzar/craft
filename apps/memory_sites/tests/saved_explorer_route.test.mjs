import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createQaVisualRoute} from '../qa_visual_route.mjs';
import {createSavedExplorerRoute,savedExplorerShell} from '../saved_explorer_route.mjs';
import {runSyntheticPermanentSmoke} from '../synthetic_permanent_smoke.mjs';

const originalEntry='/assets/index-synthetic.js';
const html='<!doctype html><html><script type="module" crossorigin src="'+originalEntry+'"></script></html>';
const main='/* fixture: unchanged original JS served from original path */';
const payload=(s,type)=>({base64:Buffer.from(s).toString('base64'),type});
const assets={
  '/index.html':payload(html,'text/html'),
  [originalEntry]:payload(main,'text/javascript'),
};
const cwd=fileURLToPath(new URL('../../../',import.meta.url));
const fixtureSql=['0002_queue','0003_publications','0004_raw_capture','0005_semantic_thoughts','0006_live_source_commit'];
function database(t) {
  const raw=new DatabaseSync(':memory:');
  t.after(()=>raw.close());
  for(const name of fixtureSql) raw.exec(readFileSync(new URL('../drizzle/'+name+'.sql',import.meta.url),'utf8'));
  raw.exec(readFileSync(new URL('../smoke/0001_preview_marker.sql',import.meta.url),'utf8'));
  const executed=[];
  const db={
    prepare(sql){return {sql,args:[],bind(...args){return {...this,args};}};},
    async batch(stmts){
      raw.exec('BEGIN IMMEDIATE');
      try {
        executed.push(...stmts.map(s=>s.sql));
        const r=stmts.map(({sql,args})=>({success:true,results:raw.prepare(sql).all(...args)}));
        raw.exec('COMMIT');return r;
      } catch(e){raw.exec('ROLLBACK');throw e;}
    }
  };
  return {db,executed};
}

test('isolated shell maps logical same-origin QA fetch and loads the unchanged original module',async()=>{
  const shell=savedExplorerShell(assets);
  assert.equal(shell.html,html.replace(originalEntry,'/explore/saved/bootstrap.mjs'));
  const [setup,entry]=shell.script.split('await import(');
  assert.equal(entry,JSON.stringify(originalEntry)+');\n');
  const seen=[];
  const window={location:new URL('https://preview.invalid/explore/saved'),
    fetch:(url,opts)=>{seen.push({url,opts});return Promise.resolve('accepted');}};
  vm.runInNewContext(setup,{window,URL});
  assert.equal(await window.fetch('/api/v1/graph?mode=current',{cache:'no-store'}),'accepted');
  assert.equal(await window.fetch(new URL('https://preview.invalid/api/v1/nodes/a?revision=1')),'accepted');
  for(const wrong of ['/api/v1/saved/graph','https://elsewhere.invalid/api/v1/graph','/preview/permanent','/api/v1/../graph'])
    await assert.rejects(window.fetch(wrong),/Invalid graph request/);
  assert.deepEqual(seen.map(s=>s.url),[
    '/api/v1/saved/graph?mode=current','/api/v1/saved/nodes/a?revision=1',
  ]);
  assert.equal(seen[0].opts.redirect,'error');
  assert.throws(()=>vm.runInNewContext(setup,{window:{...window,location:new URL('https://preview.invalid/explore')},URL}),
    /mode unavailable/);
  assert.throws(()=>savedExplorerShell({...assets,'/index.html':payload(html+'<script src="/foreign.js"></script>','text/html')}),
    /Unexpected verified graph shell/);
});

test('saved D1 graph is isolated before/after seeding; frozen overview and assets stay identical',async t=>{
  const snapshot=JSON.parse(execFileSync(process.env.PYTHON||'python',['-m','apps.memory_sites.tests.qa_frozen_oracle','--preview-overviews'],{
    cwd,encoding:'utf8',maxBuffer:4*1024*1024,
  }));
  const saved=createSavedExplorerRoute({assets});
  const frozen=createQaVisualRoute({assets,snapshot});
  const {db,executed}=database(t);
  const origin='https://preview.invalid';
  const owner={'oai-authenticated-user-id':'synthetic-owner','accept-encoding':'gzip, br'};
  let legacyHits=0;
  const app=async request=>{
    const response=await saved(request,{DB:db});
    if(response) return response;
    legacyHits++;
    return frozen(request);
  };
  const request=(path,headers=owner,method='GET')=>app(new Request(origin+path,{method,headers}));
  const oldRoot='/api/v1/graph';
  const savedRoot='/api/v1/saved/graph';
  const overview='/api/v1/overviews/topic%3Aroot?revision='+snapshot.manifest.revision;
  assert.equal((await request('/explore/saved',{})).status,401);
  assert.equal((await request('/api/v1/saved/session',{'sec-fetch-site':'cross-site',...owner})).status,401);
  assert.equal((await request('/api/v1/saved/graph',owner,'POST')).status,405);
  assert.equal((await request('/api/v1/saved/session')).status,200);
  const absent=await request(savedRoot);
  assert.equal(absent.status,503);
  assert.equal((await absent.json()).error.code,'synthetic_graph_unavailable');
  assert.equal((await request('/api/v1/saved/overviews/topic%3Aroot')).status,404);
  assert.equal((await request('/api/v1/saved')).status,404);
  assert.equal(legacyHits,0);
  assert.ok(executed.every(sql=>/^SELECT /.test(sql)));

  const frozenManifest=await (await request(oldRoot)).json();
  assert.deepEqual(frozenManifest,snapshot.manifest);
  const before=await (await request(overview)).json();
  assert.deepEqual(before,snapshot.overviews['topic:root']);

  const proof=await runSyntheticPermanentSmoke(db);
  assert.equal(proof.ok,true);
  const startRead=executed.length;
  assert.equal(await (await request('/explore')).text(),html);
  assert.equal(await (await request(originalEntry)).text(),main);
  assert.equal(await (await request('/explore/saved')).text(),savedExplorerShell(assets).html);
  assert.match(await (await request('/explore/saved/bootstrap.mjs')).text(),/await import/);
  assert.equal(await (await request('/explore/saved/bootstrap.mjs',owner,'HEAD')).text(),'');
  const capability=await (await request('/api/v1/saved/capabilities')).json();
  const m=await (await request(savedRoot+'?mode=current&history=0&undated=separate')).json();
  assert.equal(m.provider_id,capability.provider_id);
  assert.notEqual(m.provider_id,frozenManifest.provider_id);
  assert.equal(m.counts.nodes,1);
  assert.equal(m.counts.edges,0);
  const namespaced=p=>p.replace(/^\/api\/v1/,'/api/v1/saved');
  for(const [path,len] of [[m.nodes.url,16],[m.edges.url,0]]) {
    const reply=await request(namespaced(path));
    assert.equal(reply.status,200);
    assert.equal(reply.headers.get('content-encoding'),null);
    assert.equal((await reply.arrayBuffer()).byteLength,len);
  }
  const topics=await (await request(namespaced(m.topics_url))).json();
  assert.equal(topics.revision,m.revision);
  assert.deepEqual(topics.topics,[]);
  const node=await (await request(savedRoot+'/'+m.revision+'/nodes/0')).json();
  assert.equal(node.thought_ref.thought_id,proof.accepted.thought_id);
  assert.equal(node.revision,m.revision);
  assert.equal(node.text,proof.accepted.text);
  const nodeUrl='/api/v1/saved/nodes/'+encodeURIComponent(node.id)+'?revision=';
  assert.deepEqual(await (await request(nodeUrl+m.revision)).json(),node);
  assert.equal((await request(nodeUrl+frozenManifest.revision)).status,409);
  assert.equal((await request(namespaced(frozenManifest.nodes.url))).status,409);
  assert.equal((await request(m.nodes.url)).status,409);
  assert.equal((await request(namespaced(overview))).status,404);
  assert.deepEqual(await (await request(overview)).json(),before);
  assert.deepEqual(await (await request(savedRoot)).json(),m);
  assert.ok(executed.slice(startRead).every(sql=>/^(SELECT|WITH) /.test(sql)));
});
