// Каталог состояния один и тот же у node и у bash-веера: разъезд между ними
// молча обнулял бы счётчик прогонов критика — node писал бы отметку в один
// каталог, а веер читал из другого.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const FAN = path.join(REPO, 'tools', 'plan-critic-fan.sh');

test('node и bash-веер считают отметку критика по ОДНОЙ формуле каталога', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'statedir-test.'));
  try {
    // TMPDIR, отличный от /tmp, — умолчание macOS: именно там разъезд и виден.
    const env = { ...process.env, TMPDIR: dir, CRAFT_SESSION_ID: 'S9' };
    delete env.CRAFT_STATE_DIR;
    delete env.CRAFT_PLAN_CRITIC_MARKER;
    delete env.CRAFT_PLAN_CRITIC_RUNS;

    const fromNode = spawnSync(process.execPath, ['--input-type=module', '-e',
      `const p = await import(${JSON.stringify(`${REPO}/.claude/hooks/lib/paths.js`)});`
      + 'process.stdout.write(p.planCriticMarker());'], { env, encoding: 'utf8' }).stdout;

    const fromBash = spawnSync('bash', ['-c',
      `set -u; state="\${CRAFT_STATE_DIR:-\${TMPDIR:-/tmp}}"; state="\${state%/}";`
      + ' sid="${CRAFT_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-default}}";'
      + ' printf %s "$state/plan-critic.${sid}.done"'], { env, encoding: 'utf8' }).stdout;

    assert.equal(fromNode, fromBash, 'отметку пишет node, читает веер — путь обязан совпасть');
    assert.ok(fromNode.startsWith(dir), `оба обязаны сидеть в TMPDIR, а вышло ${fromNode}`);
    assert.ok(fs.existsSync(FAN), 'веер на месте');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
