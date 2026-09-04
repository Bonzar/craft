// Уборка состояния: что сносится, что остаётся и как часто вообще сканируется
// каталог. Цена ошибки в обе стороны — снесённое состояние ЖИВОЙ сессии либо
// каталог, который растёт файлом на каждый вызов каждого хука до конца жизни
// машины.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { sweepOld } from '../../.claude/hooks/lib/sweep.js';

const HOUR = 60 * 60 * 1000;
const OPTS = {
  prefix: 'decisions.', stamp: 'decisions.sweep', ttlMs: HOUR, everyMs: 10 * 60 * 1000,
};

function dir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'sweep-test.'));
}

function aged(file, ageMs) {
  fs.writeFileSync(file, 'x');
  const when = new Date(Date.now() - ageMs);
  fs.utimesSync(file, when, when);
}

test('сносится старое, живое остаётся', () => {
  const d = dir();
  try {
    aged(path.join(d, 'decisions.старая.jsonl'), 3 * HOUR);
    aged(path.join(d, 'decisions.живая.jsonl'), 60 * 1000);
    // Чужие файлы уборщик не трогает вовсе: у каждого свой хозяин и свой срок.
    aged(path.join(d, 'metrics.старая.jsonl'), 3 * HOUR);

    assert.equal(sweepOld(d, OPTS), true);
    assert.deepEqual(fs.readdirSync(d).sort(),
      ['decisions.sweep', 'decisions.живая.jsonl', 'metrics.старая.jsonl']);
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test('уборщик не сносит сам себя, даже когда отметка старая', () => {
  const d = dir();
  try {
    aged(path.join(d, 'decisions.sweep'), 3 * HOUR);
    aged(path.join(d, 'decisions.старая.jsonl'), 3 * HOUR);
    assert.equal(sweepOld(d, OPTS), true);
    assert.deepEqual(fs.readdirSync(d), ['decisions.sweep']);
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test('каталог сканируется не чаще срока: свежая отметка гасит уборку', () => {
  const d = dir();
  try {
    fs.writeFileSync(path.join(d, 'decisions.sweep'), '');
    aged(path.join(d, 'decisions.старая.jsonl'), 3 * HOUR);
    assert.equal(sweepOld(d, OPTS), false, 'скан на каждой записи платил бы ходом');
    assert.ok(fs.existsSync(path.join(d, 'decisions.старая.jsonl')));
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test('каталог не пишется — уборка не наше дело, и падения нет', () => {
  assert.equal(sweepOld('/proc/нет-такого-каталога', OPTS), false);
  assert.equal(sweepOld('', OPTS), false);
});
