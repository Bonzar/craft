// Очередь и замок хранения сводок: доставленное снимается ровно по снимку,
// брошенный замок забирается, живой — нет.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const store = await import('../../.claude/hooks/lib/metrics-store.js');

function tmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'metrics-store-test.'));
}

test('снятие доставленного оставляет дописанное после снимка', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  const snapshot = '{"sid":"a","ts":"2026-09-02T10:00:00Z"}\n';
  fs.writeFileSync(queue, `${snapshot}{"sid":"b","ts":"2026-09-02T10:01:00Z"}\n`);
  assert.equal(store.removeDelivered(queue, snapshot), true);
  assert.equal(fs.readFileSync(queue, 'utf8'), '{"sid":"b","ts":"2026-09-02T10:01:00Z"}\n');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('снятие доставленного убирает файл, когда после снимка ничего нет', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  const snapshot = '{"sid":"a"}\n';
  fs.writeFileSync(queue, snapshot);
  assert.equal(store.removeDelivered(queue, snapshot), true);
  assert.equal(fs.existsSync(queue), false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('переписанная кем-то очередь не трогается', () => {
  const dir = tmp();
  const queue = path.join(dir, 'queue.jsonl');
  fs.writeFileSync(queue, '{"sid":"z"}\n');
  assert.equal(store.removeDelivered(queue, '{"sid":"a"}\n'), false);
  assert.equal(fs.readFileSync(queue, 'utf8'), '{"sid":"z"}\n');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('живой замок держит, брошенный забирается', () => {
  const dir = tmp();
  const lock = path.join(dir, 'queue.jsonl.lock');
  assert.equal(store.acquireLock(lock), true, 'свободный замок берётся');
  assert.equal(store.acquireLock(lock), false, 'свежий замок занят');
  const old = new Date(Date.now() - store.STALE_MS - 60000);
  fs.utimesSync(lock, old, old);
  assert.equal(store.acquireLock(lock), true, 'протухший замок забирается');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('разбор очереди пропускает битые строки и строки без сессии', () => {
  const parsed = store.parseQueue('{"sid":"a"}\nмусор\n{"nosid":1}\n\n{"sid":"b"}\n');
  assert.deepEqual(parsed.map((r) => r.sid), ['a', 'b']);
});
