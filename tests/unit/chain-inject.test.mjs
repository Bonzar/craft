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
//
// Названо честно: инжектор в слое сегодня ОДИН, поэтому утверждение про «не больше
// одного» покраснеть сейчас не может — зубы у него появятся со вторым. Зубы,
// работающие уже сегодня, — у второго утверждения: матчер, к которому не удалось
// подобрать имя, оставляет цепочку непроверенной, и это провал, а не пропуск.
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
//
// Матчер бывает регуляркой (`mcp__.*__craft_write`), и молча пропускать такие
// нельзя: именно под ней стоят три хука подряд. Из каждой альтернативы делается
// ОБРАЗЕЦ имени — `.*` заменяется куском текста, — и образец обязан самому матчеру
// подойти. Не подошёл — цепочка не проверена, и это провал кейса, а не пропуск:
// молчаливый пропуск уже однажды выключил проверку целиком.
function toolsOf(groups) {
  const out = new Set(['']);
  const unreachable = [];
  for (const g of groups) {
    for (const name of String(g.matcher || '').split('|')) {
      if (!name) continue;
      const sample = name.replace(/\.\*/g, 'x');
      let ok = false;
      try { ok = new RegExp(`^(?:${name})$`).test(sample); } catch { ok = false; }
      if (ok) out.add(sample); else unreachable.push(name);
    }
  }
  return { tools: [...out], unreachable };
}

test('в одной цепочке не больше одного инжектора JSON', () => {
  assert.ok(injects.size > 0, 'инжекторы вообще должны находиться — иначе проверка пуста');
  const crowded = [];
  const unreachable = [];
  for (const [event, groups] of Object.entries(TABLE)) {
    const sampled = toolsOf(groups);
    for (const name of sampled.unreachable) unreachable.push(`${event}: ${name}`);
    for (const scope of ['project', 'universal']) {
      for (const tool of sampled.tools) {
        const chain = hooksFor(event, tool, scope).filter((name) => injects.has(name));
        if (chain.length > 1) crowded.push(`${event}/${scope}/${tool || 'любой'}: ${chain.join(', ')}`);
      }
    }
  }
  assert.deepEqual(unreachable, [], 'матчер, под который не удалось подобрать имя, оставляет цепочку непроверенной');
  assert.deepEqual(crowded, []);
});
