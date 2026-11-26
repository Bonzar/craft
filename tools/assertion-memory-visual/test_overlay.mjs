import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {versionableBundle} from './bundle.mjs';
test('release uses the unmodified Connections worker and a pinned tag',async()=>{
  const dir=new URL('.',import.meta.url).pathname;
  const blank=await versionableBundle(dir);
  const tag='assertion-memory-visual-v3.2.1';
  const pinned=await versionableBundle(dir,tag);
  const worker=Object.keys(pinned).find(k=>k.includes('graph.worker'));
  const javascript=Object.keys(pinned).find(k=>k.endsWith('.js')&&k!==worker);
  assert.deepEqual(pinned[worker],await readFile(new URL('ui/'+worker,import.meta.url)));
  assert.deepEqual(pinned[javascript],blank[javascript]);
  assert.match(pinned[javascript].toString(),/graphMode=`connections`/);
  assert.match(pinned['index.html'].toString(),/id="topics-toggle" hidden/);
  assert.match(pinned['index.html'].toString(),/Визуал: assertion-memory-visual-v3.2.1/);
  assert.doesNotMatch(blank['index.html'].toString(),/Визуал:/);
  assert.equal(Object.keys(pinned).length,4);
  assert.match(pinned['index.html'].toString(),/<dialog id="visual-queue" aria-labelledby="visual-queue-heading">/);
  assert.match(pinned[javascript].toString(),/queue-card-top/);
  assert.match(pinned[javascript].toString(),/database_cleared.*location\.reload/);
  const stylesheet=Object.keys(pinned).find(n=>n.endsWith('.css'));
  assert.match(pinned[stylesheet].toString(),/#visual-queue\{box-sizing:border-box;position:fixed;inset:0/);
  await assert.rejects(versionableBundle(dir,'<script>'),/Invalid Visual release tag/);
});

import "./graph-boot-overlay.test.mjs";
