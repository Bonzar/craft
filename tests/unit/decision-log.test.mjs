// Канал от решателя к наблюдателю: журнал решений в каталоге состояния.
//
// Прежде каналом была общая память процесса (globalThis). Кейсы ниже держат ровно
// то, чего у неё не было: строка видна ДРУГОМУ процессу и переживает смерть того,
// кто решил; замеры и признаки идут тем же каналом; молчание гварда строки не
// пишет.
//
// Читатель один — наблюдатель, и читает он ОТ СМЕЩЕНИЯ: строки этого события он
// заберёт на следующем и перенесёт в свой журнал. Сшивку строки с событием держат
// кейсы свёртки (tests/unit/metrics.test.mjs): ключ — ключ события.
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
  key: 'occ-1', session_id: 'sid-1', event: 'pre-tool', call_id: 'toolu_1', tool: 'Bash',
};

test('строка видна ДРУГОМУ процессу: канал переживает того, кто решил', async () => {
  const { dir, file } = sandbox();
  try {
    // Решение пишет отдельный процесс и умирает — ровно как хук, который принял
    // решение и вышел. На общей памяти процесса читать после этого было бы нечего.
    const code = `
      process.env.CRAFT_DECISION_LOG = ${JSON.stringify(file)};
      const { appendDecision } = await import(${JSON.stringify(`${LIB}/decision-log.js`)});
      appendDecision(${JSON.stringify(EVENT)}, { outcome: 'deny', hook: 'universal-guard-plan-gate', reasonClass: 'gate.empty' });
    `;
    const res = spawnSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8' });
    assert.equal(res.status, 0, res.stderr);

    const { readSince } = await load(file);
    const { records, offset, status } = readSince(EVENT, {});
    assert.equal(status, 'ok');
    assert.equal(records.length, 1);
    const [d] = records;
    assert.equal(d.outcome, 'deny');
    assert.equal(d.hook, 'universal-guard-plan-gate');
    assert.equal(d.class, 'gate.empty');
    assert.equal(d.key, EVENT.key, 'ключ события — он же ключ сшивки');
    assert.equal(d.h, undefined,
      'хеш вызова живёт на записи ВЫЗОВА: у отказанного вызова она теперь есть, и вторая копия разъезжалась бы');
    assert.ok(offset > 0, 'смещение сдвинулось: следующее чтение начнётся отсюда');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('молчащий гвард строки не пишет — читать нечего, и это не ошибка', async () => {
  const { dir, file } = sandbox();
  try {
    const { readSince } = await load(file);
    assert.deepEqual(readSince(EVENT, {}), { records: [], offset: 0, head: '', status: 'ok' });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('от смещения читается ТОЛЬКО новое: длинная сессия не делает чтение дороже', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendDecision, readSince } = await load(file);
    appendDecision(EVENT, { outcome: 'deny', hook: 'a' });
    const first = readSince(EVENT, {});
    assert.equal(first.records.length, 1);

    // Ничего не дописали — второе чтение с тем же смещением пусто.
    assert.deepEqual(readSince(EVENT, first).records, []);

    appendDecision({ ...EVENT, call_id: 'toolu_2' }, { outcome: 'ask', hook: 'b' });
    const second = readSince(EVENT, first);
    assert.equal(second.records.length, 1, 'прочитано только дописанное');
    assert.equal(second.records[0].outcome, 'ask');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('журнал подмели — читаем сначала, а не молчим', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendDecision, readSince } = await load(file);
    appendDecision(EVENT, { outcome: 'deny', hook: 'a' });
    const seen = readSince(EVENT, {});
    // Уборщик СНЁС журнал сессии, вернувшейся после долгого перерыва, и следующая
    // запись завела новый — с новым номером файла и своей длиной, которая запросто
    // окажется больше запомненного смещения. По одному смещению его читали бы с
    // середины, а начало пропадало бы молча.
    fs.rmSync(file);
    appendDecision(EVENT, { outcome: 'block', hook: 'ccc' });
    const again = readSince(EVENT, seen);
    assert.equal(again.records.length, 1, 'строка нового журнала не пропущена');
    assert.equal(again.records[0].outcome, 'block');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('каждая строка несёт свой ключ: событие, появление, вызов и сессию', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendDecision, appendFlag, readSince } = await load(file);
    appendDecision({ ...EVENT, call_id: 'toolu_1' }, { outcome: 'deny', hook: 'a' });
    appendDecision({ key: 'occ-2', session_id: 'sid-1', event: 'stop', call_id: '' },
      { outcome: 'block', hook: 'b' });
    appendFlag({ key: 'occ-3', session_id: 'sid-1', event: 'prompt', call_id: '' }, 'incident');
    const { records } = readSince(EVENT, {});
    assert.deepEqual(records.map((r) => [r.kind, r.key, r.event, r.call_id, r.sid]), [
      ['decision', 'occ-1', 'pre-tool', 'toolu_1', 'sid-1'],
      ['decision', 'occ-2', 'stop', '', 'sid-1'],
      ['flag', 'occ-3', 'prompt', '', 'sid-1'],
    ]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('каталог состояния берётся ИЗ СОБЫТИЯ, а не из своей копии формулы', async () => {
  const { dir } = sandbox();
  try {
    delete process.env.CRAFT_DECISION_LOG;
    const own = fs.mkdtempSync(path.join(os.tmpdir(), 'own-state.'));
    const mod = await import(`${LIB}/decision-log.js?t=${Date.now()}${Math.random()}`);
    const ev = { key: 'occ-1', session_id: 'sid-1', event: 'stop', call_id: '', state_dir: own };
    mod.appendDecision(ev, { outcome: 'block', hook: 'h' });
    assert.equal(fs.existsSync(path.join(own, 'decisions.sid-1.jsonl')), true,
      'журнал лёг в каталог, который принесло событие');
    assert.equal(mod.readSince(ev, {}).records[0].outcome, 'block');
    fs.rmSync(own, { recursive: true, force: true });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('замеры и признаки идут тем же каналом', async () => {
  const { dir, file } = sandbox();
  try {
    const { appendTimings, appendFlag, readSince } = await load(file);
    appendTimings(EVENT, { 'universal-guard-plan-gate': 12, 'universal-metrics': 3 });
    appendFlag({ session_id: 'sid-1', event: 'prompt', call_id: '' }, 'incident');
    const { records } = readSince(EVENT, {});
    assert.deepEqual(records.map((r) => r.kind), ['timing', 'flag']);
    assert.deepEqual(records[0].hooks, { 'universal-guard-plan-gate': 12, 'universal-metrics': 3 });
    assert.equal(records[1].flag, 'incident');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('нечитаемый журнал назван ошибкой, а не пустотой', async () => {
  const { dir } = sandbox();
  try {
    // Пустота значит «никто не решал». Беда чтения — не пустота, и назвать её так
    // значило бы посчитать отказ прошедшим вызовом.
    const { readSince } = await load(dir); // каталог вместо файла: EISDIR
    const res = readSince(EVENT, {});
    assert.equal(res.status, 'error');
    assert.deepEqual(res.records, []);
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
    assert.doesNotThrow(() => mod.readSince({ session_id: '', event: 'prompt', call_id: '' }, {}));
  } finally {
    if (saved !== undefined) process.env.CRAFT_DECISION_LOG = saved;
    if (savedSid !== undefined) process.env.CRAFT_SESSION_ID = savedSid;
  }
});

test('срок журнала — СУТКИ: пауза между событиями бывает длиннее часа', async () => {
  // Строки этого события забирает СЛЕДУЮЩЕЕ, а между ними Влад успевает уйти на
  // обед. Часовой срок сносил бы журнал с ещё не перенесённым решением — отказ
  // пропадал бы из счёта. Срок проверяется через сам journal, а не через параметр
  // уборщика: параметром покрыта механика, а не число, с которым её зовут.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'journal-ttl-test.'));
  const saved = process.env.CRAFT_DECISION_LOG;
  try {
    delete process.env.CRAFT_DECISION_LOG;
    const aged = (name, ageMs) => {
      const file = path.join(dir, name);
      fs.writeFileSync(file, '{}\n');
      const when = new Date(Date.now() - ageMs);
      fs.utimesSync(file, when, when);
      return file;
    };
    const HOUR = 60 * 60 * 1000;
    const pause = aged('decisions.обеденный-перерыв.jsonl', 2 * HOUR);
    const old = aged('decisions.позавчерашний.jsonl', 25 * HOUR);

    const mod = await import(`${LIB}/decision-log.js?t=${Date.now()}${Math.random()}`);
    mod.appendDecision(
      { key: 'occ-1', session_id: 'своя', event: 'pre-tool', call_id: 'c1', state_dir: dir },
      { outcome: 'deny', hook: 'h' },
    );
    assert.equal(fs.existsSync(pause), true, 'двухчасовая пауза журнал не хоронит');
    assert.equal(fs.existsSync(old), false, 'позавчерашний журнал никому не нужен');
  } finally {
    if (saved !== undefined) process.env.CRAFT_DECISION_LOG = saved;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
