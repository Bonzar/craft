import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createManifest, verifyAndStage } from '../scripts/qa-assets.mjs';

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'qa-assets-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const ui = join(root, 'ui');
  await mkdir(join(ui, 'assets'), { recursive: true });
  await writeFile(join(ui, 'index.html'), '<script src="/assets/index-123.js"></script><link href="assets/index-456.css">');
  await writeFile(join(ui, 'assets/index-123.js'), 'js');
  await writeFile(join(ui, 'assets/index-456.css'), 'css');
  await writeFile(join(ui, 'assets/graph.worker-789.js'), 'worker');
  return { root, ui, output: join(root, 'build') };
}

test('stages only pinned Vite files byte for byte', async t => {
  const { ui, output } = await fixture(t);
  await writeFile(join(ui, 'private.json'), 'not part of package');
  const expected = await createManifest(ui);
  await verifyAndStage(ui, expected, output);
  assert.deepEqual(await readdir(output), ['assets', 'index.html']);
  assert.equal((await readFile(join(output, 'assets/graph.worker-789.js'))).toString(), 'worker');
  assert.deepEqual(await createManifest(output), expected);
  await assert.rejects(verifyAndStage(ui, expected, output), { code: 'EEXIST' });
});

test('rejects tampering before creating output', async t => {
  const { ui, output } = await fixture(t);
  const expected = await createManifest(ui);
  await writeFile(join(ui, 'assets/index-123.js'), 'xx');
  await assert.rejects(verifyAndStage(ui, expected, output), /does not match/);
  await assert.rejects(readdir(output), { code: 'ENOENT' });
});

test('rejects foreign assets and symlinks', async t => {
  const { ui } = await fixture(t);
  const foreign = join(ui, 'assets/raw.json');
  await writeFile(foreign, '{}');
  await assert.rejects(createManifest(ui), /Unexpected/);
  await rm(foreign);
  await rm(join(ui, 'assets/index-123.js'));
  await symlink(join(ui, 'index.html'), join(ui, 'assets/index-123.js'));
  await assert.rejects(createManifest(ui), /Non-regular/);
});
