// В одной цепочке — не больше одного инжектора JSON.
//
// Дописанный контекст цепочку НЕ обрывает: инжектор не исключает чужого ответа, и
// это правильно. Но stdout у хуков цепочки ОБЩИЙ, а `inject()` печатает документ
// JSON: два таких хука подряд дали бы харнесу два документа в одном потоке, и
// прочитан был бы в лучшем случае первый. Инжекторы, печатающие голый текст (старт
// сессии), к этому отношения не имеют — их харнес складывает сам.
//
// Проверка статическая: кто печатает JSON, видно по импорту `inject` из обёртки
// решения, а состав цепочки спрашивается у самой таблицы маршрутов.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HOOKS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '.claude', 'hooks');
const { TABLE, hooksFor } = await import(`${HOOKS}/dispatch-table.js`);

const injects = new Set(fs.readdirSync(HOOKS)
  .filter((f) => f.endsWith('.js'))
  .filter((f) => /import\s*\{[^}]*\binject\b[^}]*\}\s*from\s*'\.\/lib\/decide-claude\.js'/
    .test(fs.readFileSync(path.join(HOOKS, f), 'utf8')))
  .map((f) => f.replace(/\.js$/, '')));

// Инструменты берутся из матчеров самой таблицы: цепочка складывается под
// конкретный инструмент, и «на любой» — тоже случай.
function toolsOf(groups) {
  const out = new Set(['']);
  for (const g of groups) {
    for (const name of String(g.matcher || '').split('|')) {
      if (name && !name.includes('.*')) out.add(name);
    }
  }
  return [...out];
}

test('в одной цепочке не больше одного инжектора JSON', () => {
  assert.ok(injects.size > 0, 'инжекторы вообще должны находиться — иначе проверка пуста');
  const crowded = [];
  for (const [event, groups] of Object.entries(TABLE)) {
    for (const scope of ['project', 'universal']) {
      for (const tool of toolsOf(groups)) {
        const chain = hooksFor(event, tool, scope).filter((name) => injects.has(name));
        if (chain.length > 1) crowded.push(`${event}/${scope}/${tool || 'любой'}: ${chain.join(', ')}`);
      }
    }
  }
  assert.deepEqual(crowded, []);
});
