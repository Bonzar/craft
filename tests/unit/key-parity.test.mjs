// Ключ следа обязан СОВПАДАТЬ у JS и у Python на одном входе.
//
// Иначе вся упаковка бессмысленна: пакет живёт своим процессом и кладёт след в
// тот же журнал решений, что JS-хуки, а сшивают их по ключу. Разойдись формулы —
// решения пакетов молча выпали бы из сводки, и увидеть это можно было бы только
// по числу, которого нет.
//
// Кейс гоняет ОБЕ реализации на одних байтах, а не сверяет их глазами: две копии
// формулы уже разъезжались бы на пустом входе и на не-ASCII.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { eventKey } from '../../.claude/hooks/lib/event-key.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const PYLIB = path.join(REPO, 'runtime', 'pylib');

// Python-сторона зовётся так же, как её зовёт обёртка: байты приходят потоком, а
// идентификатор вызова — аргументом.
const pythonKey = (callId, raw) => {
  const res = spawnSync('python3', [
    '-c',
    'import sys; sys.path.insert(0, sys.argv[1]);\n'
    + 'from key import event_key;\n'
    + 'sys.stdout.write(event_key(sys.argv[2], sys.stdin.buffer.read()))',
    PYLIB, callId,
  ], { input: raw, encoding: 'utf8' });
  assert.equal(res.status, 0, `python3 не отработал: ${res.error ? res.error.code : res.stderr}`);
  return res.stdout;
};

const SAMPLES = [
  ['идентификатор вызова сильнее байтов', 'toolu_01', '{"hook_event_name":"PreToolUse"}'],
  ['пробелы вокруг идентификатора не считаются', '  toolu_01  ', '{"a":1}'],
  ['нет идентификатора — хеш байтов', '', '{"hook_event_name":"Stop","session_id":"s"}'],
  ['пустое событие тоже имеет ключ', '', ''],
  ['не-ASCII в байтах', '', '{"prompt":"ты сломал мою заметку"}'],
  ['два байт-в-байт одинаковых события дают ОДИН ключ', '', '{"hook_event_name":"Stop"}'],
];

for (const [name, callId, raw] of SAMPLES) {
  test(`ключ: ${name}`, () => {
    assert.equal(eventKey(callId, raw), pythonKey(callId, raw));
  });
}

test('ключ: разные байты дают разные ключи', () => {
  const a = eventKey('', '{"hook_event_name":"Stop"}');
  const b = eventKey('', '{"hook_event_name":"SessionEnd"}');
  assert.notEqual(a, b);
  assert.notEqual(pythonKey('', '{"hook_event_name":"Stop"}'), pythonKey('', '{"hook_event_name":"SessionEnd"}'));
});

test('ключ: шестнадцатеричный, шестнадцать знаков', () => {
  const key = eventKey('', '{"hook_event_name":"Stop"}');
  assert.match(key, /^[0-9a-f]{16}$/);
});
