import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createQaVisualRoute} from '../qa_visual_route.mjs';

test('owner-only explorer serves unchanged bytes and a complete original synthetic revision', async()=>{
  const cwd=fileURLToPath(new URL('../../../',import.meta.url));
  const snapshot=JSON.parse(execFileSync(process.env.PYTHON||'python',['-m','apps.memory_sites.tests.qa_frozen_oracle'],{
    cwd,encoding:'utf8',maxBuffer:4*1024*1024,
  }));
  const fixture='<title>Вселенная знаний</title>';
  const graph=createQaVisualRoute({
    assets:{'/index.html':{type:'text/html; charset=utf-8',base64:Buffer.from(fixture).toString('base64')}},
    snapshot,
  });
  const ask=(path,headers={},method='GET')=>graph(new Request('https://test.example'+path,{headers,method}));
  const login={'oai-authenticated-user-id':'synthetic-owner','accept-encoding':'identity'};
  assert.equal(await ask('/'),null);
  assert.equal((await ask('/explore')).status,401);
  assert.equal((await ask('/explore',{...login,'sec-fetch-site':'cross-site'})).status,200);
  assert.equal((await ask('/api/v1/session',{...login,'sec-fetch-site':'cross-site'})).status,401);
  assert.equal(await (await ask('/explore',login)).text(),fixture);
  assert.equal((await ask('/api/v1/session',login,'POST')).status,405);
  assert.equal((await (await ask('/api/v1/session',login)).json()).authenticated,true);
  const m=await (await ask('/api/v1/graph?mode=current&history=0&undated=separate',login)).json();
  assert.equal(m.revision,snapshot.manifest.revision);
  assert.deepEqual(await (await ask(m.topics_url,login)).json(),snapshot.topics);
  for(const kind of ['nodes','edges']){
    const bytes=Buffer.from(await (await ask(m[kind].url,login)).arrayBuffer());
    assert.deepEqual(bytes,Buffer.from(snapshot.artifacts[kind+'.bin'].identity,'base64'));
  }
});
