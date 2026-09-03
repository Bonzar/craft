// Гвард правила 9: в ОБЩЕЙ части слоя нет имён инструментов и харнеса. Разбор
// команд и вывода инструмента живёт в файле `<возможность>-<инструмент>`, а
// общая часть получает его результат данными.
//
// Проверка идёт по исходникам, а не по обещанию в шапке: имена инструментов уже
// дважды переезжали из хуков в `lib/` незаметно — сперва `toolFlags`, потом
// списки читающих и сессионных вызовов, — и каждый раз это находил человек.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const LIB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '.claude', 'hooks', 'lib');

// Адаптер узнаётся по имени: `<возможность>-<инструмент>.js`. Инструменты, под
// которые адаптеры уже есть, названы поимённо — иначе в список попадал бы любой
// файл с дефисом.
const TOOLS = ['git', 'bash', 'claude'];
const isAdapter = (name) => TOOLS.some((t) => name.endsWith(`-${t}.js`));

// Файлы, которые целиком про свой инструмент и ждут переименования в 1.6:
// `git.js` — команды git, `transcript.js` — формат транскрипта Claude. Список
// закрытый и растёт только вместе с планом, а не с удобством.
const NAMED_IN_16 = new Set(['git.js', 'transcript.js']);

// Имена инструментов харнеса и рабочих систем. Слово ищется целиком: `Task` в
// `TaskList` — то же имя, а `task` в русском тексте — нет.
const TOOL_NAMES = /\b(Bash|Edit|MultiEdit|NotebookEdit|ExitPlanMode|EnterPlanMode|AskUserQuestion|Skill|TaskCreate|TaskUpdate|WebFetch|WebSearch|craft_write|craft_read|mcp__|arcadia|startrek|yandex-team)\b/;

function offenders(file) {
  const found = [];
  for (const [n, line] of fs.readFileSync(file, 'utf8').split('\n').entries()) {
    // Комментарий имеет право назвать инструмент: он объясняет, ПОЧЕМУ имени
    // нет в коде. Гвард смотрит на код.
    const code = line.replace(/\/\/.*$/, '');
    if (TOOL_NAMES.test(code)) found.push(`${path.basename(file)}:${n + 1}: ${line.trim()}`);
  }
  return found;
}

test('в общей части слоя нет имён инструментов', () => {
  const guilty = [];
  for (const name of fs.readdirSync(LIB)) {
    if (!name.endsWith('.js') || isAdapter(name) || NAMED_IN_16.has(name)) continue;
    guilty.push(...offenders(path.join(LIB, name)));
  }
  assert.deepEqual(guilty, [], 'имя инструмента в общем модуле — это адаптер, которого нет');
});

// Сам гвард обязан ловить: без этого «список пуст» ничего не значит.
test('гвард имён инструментов ловит имя в коде и не ловит в комментарии', () => {
  const dir = fs.mkdtempSync(path.join(LIB, '..', 'tmp-tool-names-'));
  const file = path.join(dir, 'probe.js');
  fs.writeFileSync(file, "if (tool === 'Bash') return true;\n");
  assert.equal(offenders(file).length, 1);
  fs.writeFileSync(file, "// имя Bash знает только адаптер\nreturn scope.reads;\n");
  assert.deepEqual(offenders(file), []);
  fs.rmSync(dir, { recursive: true, force: true });
});
