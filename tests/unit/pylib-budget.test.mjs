// Потолок pylib: пять файлов, 500 строк суммарно.
//
// Потолок держится ТЕСТОМ, а не обещанием в шапке: копия pylib едет в КАЖДЫЙ
// пакет при сборке, поэтому всё, что сюда попадает, тиражируется по всему дереву
// и переезжает к коллеге вместе с любым модулем. Превышение — находка ревью, а
// не повод поднять потолок.
//
// Считаются НЕПУСТЫЕ строки вместе с комментариями: договор про размер файла, а
// не про плотность кода, и «сжать комментарии» — не способ уложиться.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PYLIB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'runtime', 'pylib');
const MAX_FILES = 5;
const MAX_LINES = 500;

const files = fs.readdirSync(PYLIB).filter((f) => f.endsWith('.py')).sort();

test('pylib: не больше пяти файлов', () => {
  assert.ok(files.length <= MAX_FILES, `файлов ${files.length}: ${files.join(', ')}`);
});

test('pylib: не больше 500 строк суммарно', () => {
  const counted = files.map((f) => [f, fs.readFileSync(path.join(PYLIB, f), 'utf8')
    .split('\n').filter((line) => line.trim() !== '').length]);
  const total = counted.reduce((sum, [, n]) => sum + n, 0);
  assert.ok(total <= MAX_LINES, `строк ${total}: ${counted.map(([f, n]) => `${f}=${n}`).join(', ')}`);
});

// Обёртка кладёт pylib на путь импорта и зовёт эти имена; исчезнувший файл она
// найдёт только в бою.
test('pylib: состав тот, который зовёт обёртка', () => {
  assert.deepEqual(files, ['decision.py', 'key.py', 'once.py', 'state.py', 'trace.py']);
});
