import { createHash } from 'node:crypto';
import { lstat, mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const ASSET = /^(?:index-[\w-]+\.(?:js|css)|graph\.worker-[\w-]+\.js)$/;
const SCHEMA = 'assertion-memory-qa-assets/v1';

async function checkType(path, directory) {
  const st = await lstat(path);
  if (!(directory ? st.isDirectory() : st.isFile())) {
    throw new Error('Non-regular QA asset or directory');
  }
}

async function loadPackage(ui) {
  const dir = join(ui, 'assets');
  await checkType(ui, true);
  await checkType(dir, true);
  const leaves = (await readdir(dir)).sort();
  if (!leaves.length || leaves.some(name => !ASSET.test(name))) {
    throw new Error('Unexpected QA assets directory entry');
  }
  const required = [/^index-.*\.js$/, /^index-.*\.css$/, /^graph\.worker-.*\.js$/];
  if (required.some(pattern => leaves.filter(name => pattern.test(name)).length !== 1)) {
    throw new Error('Expected one QA entry script, stylesheet and graph worker');
  }
  const files = new Map();
  for (const name of ['index.html', ...leaves.map(name => `assets/${name}`)]) {
    const path = join(ui, name);
    await checkType(path, false);
    files.set(name, await readFile(path));
  }
  const html = files.get('index.html').toString('utf8');
  for (const leaf of leaves.filter(name => name.startsWith('index-'))) {
    if (!html.includes(`assets/${leaf}`)) {
      throw new Error('QA HTML does not reference its entry assets');
    }
  }
  return files;
}

function describe(files) {
  return {
    schema: SCHEMA,
    files: [...files].map(([path, bytes]) => ({
      path, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')
    }))
  };
}

export async function createManifest(ui) {
  return describe(await loadPackage(ui));
}

export async function verifyAndStage(ui, expected, output) {
  const files = await loadPackage(ui);
  const manifest = describe(files);
  if (JSON.stringify(manifest) !== JSON.stringify(expected)) {
    throw new Error('QA package does not match the expected source-bytes manifest');
  }
  if (output != null) {
    await mkdir(output); // A fresh directory only: do not overwrite another build.
    for (const [name, bytes] of files) {
      const target = join(output, name);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, bytes, { flag: 'wx' });
    }
  }
  return manifest;
}

async function main([mode, ui, manifestPath, output, ...extra]) {
  if (!ui || !manifestPath || extra.length || !['capture', 'verify', 'stage'].includes(mode)
      || ((mode === 'stage') !== Boolean(output))) {
    throw new Error('Usage: node scripts/qa-assets.mjs capture|verify|stage UI_DIR MANIFEST [NEW_OUTPUT_DIR for stage]');
  }
  if (mode === 'capture') {
    const manifest = await createManifest(ui);
    await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
  } else {
    const expected = JSON.parse(await readFile(manifestPath, 'utf8'));
    await verifyAndStage(ui, expected, output);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try { await main(process.argv.slice(2)); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
