// Каталог состояния один и тот же у node и у bash-веера: разъезд между ними
// молча обнулял бы счётчик прогонов критика — node писал бы отметку в один
// каталог, а веер читал из другого.
//
// Формула веера НЕ переписывается сюда, а ВЫРЕЗАЕТСЯ ИЗ САМОГО ФАЙЛА и
// исполняется: кейс, повторяющий проверяемую строку у себя, зеленеет и после того,
// как в файле её сломали.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const FAN = path.join(REPO, 'tools', 'plan-critic-fan.sh');

// Строки веера, задающие сессию, каталог состояния и путь отметки. Их и исполняем.
function fanMarkerScript() {
  const wanted = /^\s*(sid|state|marker)=/;
  const lines = fs.readFileSync(FAN, 'utf8').split('\n').filter((l) => wanted.test(l));
  assert.ok(lines.some((l) => l.trimStart().startsWith('marker=')),
    'в веере не нашлось строки marker= — кейс проверяет не тот файл');
  return `set -u\n${lines.join('\n')}\nprintf %s "$marker"`;
}

test('node и bash-веер считают отметку критика по ОДНОЙ формуле каталога', () => {
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

    const run = spawnSync('bash', ['-c', fanMarkerScript()], { env, encoding: 'utf8' });
    assert.equal(run.status, 0, `формула веера не исполнилась: ${run.stderr}`);

    assert.equal(fromNode, run.stdout, 'отметку пишет node, читает веер — путь обязан совпасть');
    assert.ok(fromNode.startsWith(dir), `оба обязаны сидеть в TMPDIR, а вышло ${fromNode}`);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
