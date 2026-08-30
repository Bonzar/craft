import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ensureDirectToolRouting } from '../../adapters/codex/tool-routing-config.mjs';

test('Codex adapter disables code-mode host without changing foreign config', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-codex-routing.'));
  const file = path.join(dir, 'config.toml');
  try {
    fs.writeFileSync(file, 'model = "keep"\n\n[features]\napps = true\n');
    assert.equal(ensureDirectToolRouting(file), true);
    const once = fs.readFileSync(file, 'utf8');
    assert.match(once, /^model = "keep"/m);
    assert.match(once, /\[features\]\napps = true\ncode_mode_host = false/);
    assert.equal(ensureDirectToolRouting(file), false);
    assert.equal(fs.readFileSync(file, 'utf8'), once);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('project adapter declares direct tool routing', () => {
  const config = fs.readFileSync(new URL('../../.codex/config.toml', import.meta.url), 'utf8');
  assert.match(config, /\[features\][\s\S]*\bcode_mode_host\s*=\s*false/);
});
