// Очередь сводок: сжатие по сессиям, потолок, день по началу сессии, интервал
// выгрузки. Git-логика — в tests/metrics-store-git.sh на временных репозиториях.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const store = await import('../../.claude/hooks/lib/summary-store.js');

function tmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-store-test.'));
}
const summary = (sid, turns, at = '2026-09-02T10:00:00Z') => ({
  sid, turns, started_at: at, ended_at: at, ts: at,
});

test('очередь держит по одной строке на сессию: последняя сводка побеждает', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  for (let n = 1; n <= 5; n += 1) store.enqueue(queue, summary('s1', n));
  store.enqueue(queue, summary('s2', 1));

  const rows = store.parseQueue(fs.readFileSync(queue, 'utf8'));
  assert.deepEqual(rows.map((r) => r.sid), ['s1', 's2']);
  assert.equal(rows[0].turns, 5, 'осталась последняя сводка сессии');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('очередь обрезана сверху: на запрещённом push она не растёт бесконечно', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  for (let n = 0; n < store.QUEUE_CAP + 25; n += 1) store.enqueue(queue, summary(`s${n}`, 1));

  const rows = store.parseQueue(fs.readFileSync(queue, 'utf8'));
  assert.equal(rows.length, store.QUEUE_CAP);
  assert.equal(rows[rows.length - 1].sid, `s${store.QUEUE_CAP + 24}`, 'свежие сводки остаются');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('сводка без сессии в очередь не идёт', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  assert.equal(store.enqueue(queue, { turns: 1 }), false);
  assert.equal(fs.existsSync(queue), false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('день считается по началу сессии, а не по её концу', () => {
  assert.equal(store.dayOf({ started_at: '2026-09-02T23:50:00Z', ended_at: '2026-09-03T00:10:00Z' }), '2026-09-02');
  assert.equal(store.dayOf({ ended_at: '2026-09-03T00:10:00Z' }), '2026-09-03', 'без начала — по концу');
});

test('разбор очереди пропускает битые строки и строки без сессии', () => {
  const parsed = store.parseQueue('{"sid":"a"}\nмусор\n{"nosid":1}\n\n{"sid":"b"}\n');
  assert.deepEqual(parsed.map((r) => r.sid), ['a', 'b']);
});

// Цель не под тем инструментом, для которого есть адаптер, — это не ошибка
// доставки, а отсутствие возможности, и называется явно: молчаливый error здесь
// выглядел бы как сломанное хранение.
test('адаптер без available — тоже unsupported, а не падение', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  store.enqueue(queue, summary('s1', 1));
  const res = store.flushQueue({ target: dir, queueFile: queue, adapter: {} });
  assert.equal(res.status, 'unsupported');
  assert.equal(res.capability, 'summary-store');
  assert.match(fs.readFileSync(queue, 'utf8'), /"sid":"s1"/, 'очередь цела');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('без адаптера хранение отвечает unsupported с именем возможности', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  store.enqueue(queue, summary('s1', 1));
  const res = store.flushQueue({ target: dir, queueFile: queue });
  assert.equal(res.status, 'unsupported');
  assert.equal(res.capability, 'summary-store');
  assert.match(fs.readFileSync(queue, 'utf8'), /"sid":"s1"/, 'очередь цела');
  fs.rmSync(dir, { recursive: true, force: true });
});

// Отказ постановки называется ПРИЧИНОЙ: занятый лок и неудавшаяся запись — разные
// беды, и в журнале они выглядели одинаково («ждали столько-то»), из-за чего
// сломанный диск читался как чужая долгая выгрузка.
test('отказ постановки в очередь называет причину', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');

  // Лок занят живым чужим процессом: наш собственный номер, которого этот вызов
  // не держит.
  fs.mkdirSync(`${queue}.lock`);
  fs.writeFileSync(path.join(`${queue}.lock`, 'owner'), String(process.pid));
  assert.deepEqual(store.enqueueSummary(queue, summary('s1', 1), { waitMs: 50 }),
    { ok: false, reason: 'locked' });
  fs.rmSync(`${queue}.lock`, { recursive: true, force: true });

  // Записать некуда: на месте файла очереди каталог.
  fs.mkdirSync(queue);
  assert.deepEqual(store.enqueueSummary(queue, summary('s1', 1), { waitMs: 50 }),
    { ok: false, reason: 'write-failed' });
  fs.rmSync(dir, { recursive: true, force: true });
});

// Нет адаптера — нет и очереди, и это ПРОПУСК, а не тишина: сводка не уехала и
// не уедет, и в журнале это названо возможностью.
test('без адаптера постановка в очередь названа пропуском в журнале', () => {
  const dir = tmp();
  const log = path.join(dir, 'metrics.jsonl');
  // Выключатель, файл очереди и цель приходят готовыми: окружение читает край.
  const ok = store.queueSummary(summary('s1', 1), log, undefined, { target: dir });

  assert.equal(ok, false);
  const line = JSON.parse(fs.readFileSync(log, 'utf8').trim().split('\n').pop());
  assert.equal(line.kind, 'skip');
  assert.equal(line.what, 'queue');
  assert.equal(line.capability, 'summary-store');
  fs.rmSync(dir, { recursive: true, force: true });
});
