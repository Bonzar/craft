import {readFile} from 'node:fs/promises';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {BOOT_SEED, patchGraphBoot} from './graph-boot-overlay.mjs';
import {versionableBundle} from './bundle.mjs';
import {hash} from './store.mjs';

const root=fileURLToPath(new URL('.',import.meta.url));
const seed=await readFile(new URL('./ui/assets/index-Byv8wmN9.js',import.meta.url));

test('pinned boot overlay preserves the shipped Worker and source assets',async()=>{
  assert.equal(hash(seed),BOOT_SEED);
  const staged=await versionableBundle(root);
  const name='assets/graph.worker-Bex9AmBR.js';
  assert.deepEqual(staged[name],await readFile(new URL('./ui/'+name,import.meta.url)));
  const main=staged['assets/index-Byv8wmN9.js'].toString();
  assert.match(main,/releaseGraphBootStep\(24000/);
  assert.match(main,/releaseGraphBootStep\(18000/);
  assert.match(main,/releaseGraphBootStep\(15000/);
  assert.match(main,/releaseGraphBootStep\(20000/);
  assert.match(main,/application\/octet-stream/);
  assert.match(main,/addEventListener\(`messageerror`,fail\)/);
  assert.match(main,/\.\/graph\.worker-Bex9AmBR\.js/);
  assert.ok(staged['index.html'].toString().includes('./assets/index-Byv8wmN9.js'));
});

test('source-parity overlay refuses unreviewed seed or ambiguous anchors',()=>{
  const raw=seed.toString();
  assert.throws(()=>patchGraphBoot(raw,raw+' '),/seed changed/);
  assert.throws(()=>patchGraphBoot(raw.replace('let r=await this.manifestFor','let r=await this.changed'),raw),/anchor/);
  assert.throws(()=>patchGraphBoot(patchGraphBoot(raw,raw),raw),/anchor/);
});
