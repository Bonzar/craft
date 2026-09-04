// Канал между хуками одного события: журнал решений в каталоге состояния.
//
// Прежде каналом была общая память процесса (globalThis). Кейсы ниже держат ровно
// то, чего у неё не было: решение видно ДРУГОМУ процессу и переживает смерть того,
// кто решил; замеры и признаки идут тем же каналом; молчание гварда строки не
// пишет, и «строки нет» значит проход.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.resolve(HERE, '..', '..', '.claude', 'hooks', 'lib');

function sandbox() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'decision-log-test.'));
  return { dir, file: path.join(dir, 'decisions.jsonl') };
}

async function load(file) {
  process.env.CRAFT_DECISION_LOG = file;
  return import(`${LIB}/decision-log.js?t=${Date.now()}${Math.random()}`);
}

const EVENT = {
  session_id: 'sid-1', event: 'pre-tool', call_id: 'toolu_1', tool: 'Bash',
};

test('решение видно ДРУГОМУ процессу: канал переживает того, кто решил', async () => {
  const { dir, file } = sandbox();
  try {
    // Решение пишет отдельный процесс и умирает — ровно как хук, который принял
    // решение и вышел. На общей памяти процесса читать после этого было бы нечего.
    const code = `
      process.env.CRAFT_DECISION_LOG = ${JSON.stringify(file)};
      const { appendDecision } = await import(${JSON.stringify(`${LIB}/decision-log.js`)});
      appendDecision(${JSON.stringify(EVENT)}, { outcome: 'deny', hook: 'universal-guard-plan-gate', reasonClass: 'gate.empty', h: 'abc123', tool: 'Bash' });
    `;
    const res = spawnSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8' });
    assert.equal(res.status, 0, res.stderr);

    const { decisionFor } = await load(file);
    const d = decisionFor(EVENT);
    assert.equal(d.outcome, 'deny');
    assert.equal(d.hook, 'universal-guard-plan-gate');
    assert.equal(d.class, 'gate.empty');
    assert.equal(d.h, 'abc123', 'хеш вызова нужен сводке: у отказанного вызова записи метрик нет');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('решения нет — значит проход: молчащий гвард строки не пишет', async () => {
  const { dir, file } = sandbox();
  try {
    const { decisionFor } = await load(file);
    assert.equal(decisionFor(EVENT), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('решения разных вызовов не путаются: ключ — событие и идентификатор вызова', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendDecision, decisionFor } = await load(file);
    appendDecision({ ...EVENT, call_id: 'toolu_1' }, { outcome: 'deny', hook: 'a' });
    appendDecision({ ...EVENT, call_id: 'toolu_2' }, { outcome: 'ask', hook: 'b' });
    assert.equal(decisionFor({ ...EVENT, call_id: 'toolu_1' }).outcome, 'deny');
    assert.equal(decisionFor({ ...EVENT, call_id: 'toolu_2' }).outcome, 'ask');
    // Одно и то же событие с одним идентификатором, но ДО и ПОСЛЕ вызова — разные
    // события: у хука, стоящего на обоих, иначе гасилось бы второе.
    assert.equal(decisionFor({ ...EVENT, event: 'post-tool', call_id: 'toolu_1' }), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('повторный запуск того же события перекрывает прежнее решение', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendDecision, decisionFor } = await load(file);
    appendDecision(EVENT, { outcome: 'deny', hook: 'a' });
    appendDecision(EVENT, { outcome: 'allow', hook: 'b' });
    assert.equal(decisionFor(EVENT).outcome, 'allow', 'решает ПОСЛЕДНЯЯ строка');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('журнал читается ХВОСТОМ: длинная сессия не делает чтение дороже', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendDecision, decisionFor } = await load(file);
    // Больше хвоста в 64 КиБ: строки давних ходов до текущего события не
    // дочитываются вовсе, и цена чтения не растёт вместе с сессией.
    const line = (id) => `${JSON.stringify({ kind: 'decision', sid: 'sid-1', event: 'pre-tool', call_id: id, outcome: 'deny', pad: 'x'.repeat(500) })}\n`;
    // Самая ПЕРВАЯ строка — давний ход; за ней столько, что она уходит за хвост.
    const rows = [line('ancient'), ...Array.from({ length: 300 }, (_, i) => line(`filler-${i}`))];
    fs.writeFileSync(file, rows.join(''));
    const size = fs.statSync(file).size;
    assert.ok(size > 64 * 1024, `нужен файл длиннее хвоста, вышло ${size}`);

    appendDecision(EVENT, { outcome: 'ask', hook: 'late' });
    assert.equal(decisionFor(EVENT).outcome, 'ask', 'свежая строка в хвосте читается');
    assert.equal(decisionFor({ ...EVENT, call_id: 'ancient' }), null,
      'строка за пределами хвоста не читается — это и есть цена, которую мы не платим');
    assert.equal(decisionFor({ ...EVENT, call_id: 'filler-299' }).outcome, 'deny',
      'а всё, что в хвосте, читается по-прежнему');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('замеры и признаки идут тем же каналом', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendTimings, timingsFor, appendFlag, hasFlag } = await load(file);
    assert.deepEqual(timingsFor(EVENT), {}, 'замеров ещё нет — пусто, а не выдумка');
    appendTimings(EVENT, { 'universal-guard-plan-gate': 12, 'universal-metrics': 3 });
    assert.deepEqual(timingsFor(EVENT), { 'universal-guard-plan-gate': 12, 'universal-metrics': 3 });

    const prompt = { session_id: 'sid-1', event: 'prompt', call_id: '' };
    assert.equal(hasFlag(prompt, 'incident'), false);
    appendFlag(prompt, 'incident');
    assert.equal(hasFlag(prompt, 'incident'), true);
    assert.equal(hasFlag(EVENT, 'incident'), false, 'признак принадлежит своему событию');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('отметка журнала меняется только когда в него написали', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendDecision, journalStamp } = await load(file);
    // Диспетчер спрашивает «решили ли уже» после КАЖДОГО хука цепочки хода.
    // Отметка отвечает на это без разбора хвоста: не выросла — решения не было.
    const empty = journalStamp(EVENT);
    assert.equal(journalStamp(EVENT), empty, 'без записи отметка не двигается');
    appendDecision(EVENT, { outcome: 'deny', hook: 'a' });
    const after = journalStamp(EVENT);
    assert.notEqual(after, empty, 'запись решения отметку сдвинула');
    assert.equal(journalStamp(EVENT), after, 'и снова стоит, пока не пишут');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('без сессии и переопределения журнала нет — писать некуда, и это не падение', async () => {
  const saved = process.env.CRAFT_DECISION_LOG;
  const savedSid = process.env.CRAFT_SESSION_ID;
  try {
    delete process.env.CRAFT_DECISION_LOG;
    delete process.env.CRAFT_SESSION_ID;
    const mod = await import(`${LIB}/decision-log.js?t=${Date.now()}${Math.random()}`);
    // Путь по умолчанию есть всегда (счётчик, а не периметр), поэтому проверяется
    // не отказ записи, а то, что вызов не роняет хук.
    assert.doesNotThrow(() => mod.decisionFor({ session_id: '', event: 'prompt', call_id: '' }));
  } finally {
    if (saved !== undefined) process.env.CRAFT_DECISION_LOG = saved;
    if (savedSid !== undefined) process.env.CRAFT_SESSION_ID = savedSid;
  }
});
