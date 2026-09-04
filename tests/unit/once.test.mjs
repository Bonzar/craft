// Уступка второму вызову. Главное здесь — ключ метки: пока рядом лежат две
// версии одного хука, они обязаны занимать событие ОДНОЙ меткой, иначе обе
// отработают на одно событие — ровно то, от чего механизм и написан.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const HOOKS = path.join(REPO, '.claude', 'hooks');

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'once-test.'));
}

async function loadOnce(dir) {
  delete process.env.HOOK_ONCE;
  process.env.HOOK_ONCE_DIR = dir;
  return import(`../../.claude/hooks/lib/once.js?t=${Date.now()}${Math.random()}`);
}

test('второй вызов того же события уступает', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  const raw = JSON.stringify({ tool_name: 'Bash', tool_input: { command: 'echo hi' } });
  const self = `file://${HOOKS}/universal-fact-gate.js`;

  assert.equal(hookOnce(raw, JSON.parse(raw), self), true, 'первый вызов обязан работать');
  assert.equal(hookOnce(raw, JSON.parse(raw), self), false, 'второй вызов обязан уступить');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('ключ метки не зависит от расширения файла хука', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  const raw = JSON.stringify({ tool_name: 'Bash' });

  // Первый вызов приходит от JS-версии, второй — от bash-версии того же хука.
  assert.equal(hookOnce(raw, JSON.parse(raw), `file://${HOOKS}/universal-fact-gate.js`), true);
  assert.equal(
    hookOnce(raw, JSON.parse(raw), `file://${HOOKS}/universal-fact-gate.sh`),
    false,
    'разные расширения дали разные метки — на переезде хук отработал бы дважды',
  );

  // Имя метки — то же, что считала bash-версия: hook-once.<имя без расширения>.<хеш события>.
  const key = createHash('sha256').update(raw).digest('hex');
  assert.ok(fs.existsSync(path.join(dir, `hook-once.universal-fact-gate.${key}`)));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('инструментальное событие занимается по call_id: одинаковые вызовы с разными идентификаторами работают оба', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  const self = `file://${HOOKS}/universal-fact-gate.js`;
  // Уступке подаётся КАНОНИЧЕСКОЕ событие: сырой текст ей нужен лишь как ключ у
  // событий без идентификатора, полей харнеса она не знает.
  const call = (id) => JSON.stringify({ event: 'pre-tool', call_id: id, tool: 'Bash', input: { command: 'echo hi' } });

  assert.equal(hookOnce(call('toolu_1'), JSON.parse(call('toolu_1')), self), true);
  assert.equal(
    hookOnce(call('toolu_2'), JSON.parse(call('toolu_2')), self),
    true,
    'второй такой же вызов с другим идентификатором — другое событие, хук обязан работать',
  );
  assert.equal(
    hookOnce(call('toolu_1'), JSON.parse(call('toolu_1')), self),
    false,
    'повтор идентификатора — второй вызов того же события, он уступает',
  );
  fs.rmSync(dir, { recursive: true, force: true });
});

test('метка по call_id не протухает: срок хеш-меток на неё не действует', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  process.env.HOOK_ONCE_TTL = '0';
  const self = `file://${HOOKS}/universal-fact-gate.js`;
  const raw = JSON.stringify({ event: 'pre-tool', tool: 'Bash', call_id: 'toolu_ttl' });

  // Восстановление окружения — в finally: падение утверждения иначе оставило бы
  // нулевой срок следующим тестам и валило бы их вместо этого.
  try {
    assert.equal(hookOnce(raw, JSON.parse(raw), self), true);
    assert.equal(hookOnce(raw, JSON.parse(raw), self), false, 'нулевой срок не вернул занятое событие');
  } finally {
    delete process.env.HOOK_ONCE_TTL;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('одно событие вызова не гасит другое: PostToolUse работает после PreToolUse', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  const self = `file://${HOOKS}/universal-fact-gate.js`;
  const pre = JSON.stringify({ event: 'pre-tool', tool: 'Bash', call_id: 'toolu_pp' });
  const post = JSON.stringify({ event: 'post-tool', tool: 'Bash', call_id: 'toolu_pp' });

  assert.equal(hookOnce(pre, JSON.parse(pre), self), true);
  assert.equal(
    hookOnce(post, JSON.parse(post), self),
    true,
    'идентификатор вызова общий у Pre и Post — без имени события хук терял бы половину событий',
  );
  assert.equal(hookOnce(post, JSON.parse(post), self), false, 'второй вызов того же события уступает');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('метки убираются по возрасту: старая уходит, свежая остаётся', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  const self = `file://${HOOKS}/universal-fact-gate.js`;

  const stale = path.join(dir, 'hook-once.universal-fact-gate.id.pre-tool.toolu_old');
  fs.mkdirSync(stale);
  const old = new Date(Date.now() - 3 * 60 * 60 * 1000);
  fs.utimesSync(stale, old, old);

  const raw = JSON.stringify({ event: 'pre-tool', tool: 'Bash', call_id: 'toolu_new' });
  assert.equal(hookOnce(raw, JSON.parse(raw), self), true);

  assert.equal(fs.existsSync(stale), false, 'метка старше часа обязана убираться');
  assert.equal(
    fs.existsSync(path.join(dir, 'hook-once.universal-fact-gate.id.pre-tool.toolu_new')),
    true,
    'свежая метка уборкой не трогается',
  );
  fs.rmSync(dir, { recursive: true, force: true });
});

test('разные события занимают разные метки', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  const self = `file://${HOOKS}/universal-fact-gate.js`;

  assert.equal(hookOnce('{"prompt":"да"}', {}, self), true);
  assert.equal(hookOnce('{"prompt":"нет"}', {}, self), true, 'метка первого события погасила второе');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('выключатель гасит механизм целиком', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);
  process.env.HOOK_ONCE = 'off';
  const self = `file://${HOOKS}/universal-fact-gate.js`;

  assert.equal(hookOnce('{"a":1}', {}, self), true);
  assert.equal(hookOnce('{"a":1}', {}, self), true, 'с выключателем уступки быть не должно');
  delete process.env.HOOK_ONCE;
  fs.rmSync(dir, { recursive: true, force: true });
});

test('посторонний экземпляр уступает своему чекауту', async () => {
  const dir = tmpDir();
  const { hookOnce } = await loadOnce(dir);

  // В чекауте сессии лежит файл хука с тем же именем — значит исполняемый файл
  // из другого места посторонний и работать не должен.
  const checkout = fs.mkdtempSync(path.join(os.tmpdir(), 'checkout.'));
  fs.mkdirSync(path.join(checkout, '.claude', 'hooks'), { recursive: true });
  fs.writeFileSync(path.join(checkout, '.claude', 'hooks', 'universal-fact-gate.js'), '// свой');
  const event = { cwd: checkout };

  assert.equal(
    hookOnce('{"a":1}', event, `file://${HOOKS}/universal-fact-gate.js`),
    false,
    'посторонний экземпляр обязан уступить файлу из чекаута сессии',
  );
  // А свой файл из того же чекаута работает.
  assert.equal(
    hookOnce('{"a":1}', event, `file://${checkout}/.claude/hooks/universal-fact-gate.js`),
    true,
  );

  fs.rmSync(checkout, { recursive: true, force: true });
  fs.rmSync(dir, { recursive: true, force: true });
});
