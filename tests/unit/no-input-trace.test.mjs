// Гвард: хуки не пишут на диск СЛЕД ВХОДА — снимок события с текстом правки,
// командой или промптом. Такой файл был у план-гейта и кнопки; он лежал в общем
// /tmp, читался кем угодно на машине и снят в фазе 0.
//
// Проверка идёт по ИСХОДНИКАМ, а не по отсутствию файла с зашитым путём: кейс,
// который смотрит на конкретный путь, зеленеет и когда след вернулся под другим
// именем, — то есть проверяет ровно ничто.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HOOKS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '.claude', 'hooks');

function sources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return sources(full);
    return e.isFile() && full.endsWith('.js') ? [full] : [];
  });
}

test('хуки не заводят файл со следом входа', () => {
  const guilty = [];
  for (const file of sources(HOOKS)) {
    const text = fs.readFileSync(file, 'utf8');
    // Имя файла со словом «вход» рядом с записью: last-input, input-trace и
    // прочие формы того же снимка.
    if (/(last[-_]input|input[-_](trace|dump|snapshot))/i.test(text)) guilty.push(path.relative(HOOKS, file));
  }
  assert.deepEqual(guilty, [], 'след входа на диск не пишется ни одним хуком');
});
