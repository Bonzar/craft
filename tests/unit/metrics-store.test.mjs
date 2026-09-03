// Очередь сводок: сжатие по сессиям, потолок, день по началу сессии, интервал
// выгрузки. Git-логика — в tests/metrics-store-git.sh на временных репозиториях.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const store = await import('../../.claude/hooks/lib/metrics-store.js');

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

test('интервал выгрузки: первая идёт сразу, следующая ждёт отметку', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  assert.equal(store.dueForFlush(queue, 600), true, 'отметки ещё нет — выгрузка идёт');
  fs.writeFileSync(`${queue}.stamp`, '');
  assert.equal(store.dueForFlush(queue, 600), false, 'сразу после выгрузки — не пора');
  assert.equal(store.dueForFlush(queue, 0), true, 'нулевой интервал — всегда пора');
  const old = new Date(Date.now() - 700 * 1000);
  fs.utimesSync(`${queue}.stamp`, old, old);
  assert.equal(store.dueForFlush(queue, 600), true, 'интервал прошёл');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('разбор очереди пропускает битые строки и строки без сессии', () => {
  const parsed = store.parseQueue('{"sid":"a"}\nмусор\n{"nosid":1}\n\n{"sid":"b"}\n');
  assert.deepEqual(parsed.map((r) => r.sid), ['a', 'b']);
});

// Решение «пора выгружать» принимается ПОД ЛОКОМ очереди, вместе с её чтением.
// Снаружи оно успевало устареть: пока второй работник ждал лок, первый успевал
// выгрузиться и поставить отметку, а ждавший всё равно шёл в сеть — интервал не
// соблюдался ровно тогда, когда работников больше одного.
test('интервал проверяется внутри выгрузки, а не до неё', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  store.enqueue(queue, summary('s1', 1));
  fs.writeFileSync(`${queue}.stamp`, '');

  // Цель — не репозиторий: дойди дело до сети, исход был бы offline или error,
  // но не queued.
  const res = store.flushQueue({ target: dir, queueFile: queue, intervalSec: 600 });
  assert.equal(res.status, 'queued', 'выгрузка отложена своим же решением под локом');
  assert.match(fs.readFileSync(queue, 'utf8'), /"sid":"s1"/, 'очередь цела');
  fs.rmSync(dir, { recursive: true, force: true });
});

// Отметка ставится на ПОПЫТКУ, а не на удачу: пока origin недоступен, интервал
// обязан держать работников от сети — иначе каждый следующий Stop снова висит
// на fetch до потолка, и работники копятся на локе очереди всю аварию.
test('неудачная выгрузка ставит отметку, следующая ждёт интервал', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  store.enqueue(queue, summary('s1', 1));
  assert.equal(store.dueForFlush(queue, 600), true, 'предусловие: отметки нет');

  const failed = store.flushQueue({ target: dir, queueFile: queue, intervalSec: 600 });
  assert.notEqual(failed.status, 'stored', 'предусловие: выгрузка не удалась');
  assert.equal(store.dueForFlush(queue, 600), false, 'неудачная попытка отмечена');
  fs.rmSync(dir, { recursive: true, force: true });
});
