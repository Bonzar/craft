import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const hook = new URL('../../core/hooks/universal-session-anchor.js', import.meta.url).pathname;

function run(event, env) {
  return spawnSync(process.execPath, [hook], {
    input: `${JSON.stringify(event)}\n`,
    env: { ...process.env, ...env, HOOK_ONCE: 'off' },
    encoding: 'utf8',
  });
}

test('anchor survives a fresh hook process and accepts a Markdown-wrapped answer', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'persistent-anchor.'));
  const env = {
    CRAFT_SESSION_ID: 'stable-thread-id',
    CRAFT_PERSISTENT_STATE_DIR: path.join(root, 'nested', 'state'),
  };
  try {
    const saved = run({
      event: { name: 'user.prompt', prompt: '`Якорь сессии: Задача 42`' },
      action: { route: 'none', payload: {} },
    }, env);
    assert.equal(saved.status, 0, saved.stderr);

    const resumed = run({
      event: { name: 'session.start' },
      action: { route: 'none', payload: {} },
    }, env);
    assert.equal(resumed.status, 0, resumed.stderr);
    assert.equal(resumed.stdout, '', 'persisted anchor must suppress the restart prompt');

    const files = fs.readdirSync(env.CRAFT_PERSISTENT_STATE_DIR);
    assert.equal(files.length, 1);
    assert.match(files[0], /^session-anchor\.[a-f0-9]{64}$/);
    assert.equal(fs.readFileSync(path.join(env.CRAFT_PERSISTENT_STATE_DIR, files[0]), 'utf8'), 'Задача 42\n');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
