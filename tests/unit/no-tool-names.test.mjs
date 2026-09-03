// Гвард правила 9: в ОБЩЕЙ части слоя (`lib/`, кроме адаптеров) нет имён
// инструментов и харнеса. Разбор команд и вывода инструмента живёт в файле
// `<возможность>-<инструмент>`, а общая часть получает его результат данными.
//
// Проверка идёт по исходникам, а не по обещанию в шапке: имена инструментов уже
// дважды переезжали из хуков в `lib/` незаметно — сперва `toolFlags`, потом
// списки читающих и сессионных вызовов, — и каждый раз это находил человек.
//
// Хуки (`universal-*`, `craft-*`) гвард НЕ проверяет: сегодня они и есть
// обёртки, и имена инструментов в них законны. Долг «обёртка тоже не должна
// знать имён» записан в теле PR как работа 1.6, и молчание гварда про хуки
// ничего про этот долг не отрицает.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const LIB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '.claude', 'hooks', 'lib');

// Адаптер узнаётся по имени: `<возможность>-<инструмент>.js`. Инструменты, под
// которые адаптеры уже есть, названы поимённо — иначе в список попадал бы любой
// файл с дефисом.
const TOOLS = ['git', 'bash', 'claude'];
const isAdapter = (name) => TOOLS.some((t) => name.endsWith(`-${t}.js`));

// Долг: файлы, которые целиком про свой инструмент и ждут переезда в 1.6.
// `git.js` — команды git, `transcript.js` — формат транскрипта Claude,
// `env.js` — общий каталог чекаута через тот же `git.js`. Список ЗАКРЫТЫЙ:
// он только сокращается, и каждая строка названа в теле PR.
const DEBT = new Set(['git.js', 'transcript.js', 'env.js']);

// Имена инструментов харнеса и рабочих систем. Слово ищется целиком, чтобы
// «читать» в русском комментарии не путалось с `Read`; префикс MCP ловится по
// следующей букве, потому что после `mcp__` границы слова нет.
const TOOL_NAMES = new RegExp([
  '\\b(Bash|BashOutput|Read|Write|Edit|MultiEdit|NotebookEdit|Grep|Glob|LS)\\b',
  '\\b(Task|Agent|Workflow|Skill|ExitPlanMode|EnterPlanMode|AskUserQuestion)\\b',
  '\\b(TaskCreate|TaskUpdate|TaskList|TodoWrite|WebFetch|WebSearch|ToolSearch)\\b',
  '\\b(git|gh|npm|arc|arcadia|crm|tracker|startrek|craft_write|craft_read)\\b',
  'mcp__[A-Za-z_]',
].join('|'));

export function offenders(file, text = fs.readFileSync(file, 'utf8')) {
  const found = [];
  for (const [n, line] of text.split('\n').entries()) {
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
    if (!name.endsWith('.js') || isAdapter(name) || DEBT.has(name)) continue;
    guilty.push(...offenders(path.join(LIB, name)));
  }
  assert.deepEqual(guilty, [], 'имя инструмента в общем модуле — это адаптер, которого нет');
});

// Долг существует, пока файлы существуют: строка списка, под которой уже нет
// файла, — это забытая уборка, и список обязан её показать.
test('список долга не расходится с деревом', () => {
  const missing = [...DEBT].filter((name) => !fs.existsSync(path.join(LIB, name)));
  assert.deepEqual(missing, [], 'файл из списка долга исчез — строку надо снять');
});

// Сам гвард обязан ловить: без этого «список пуст» ничего не значит. Пробы —
// ровно те формы, на которых прежняя, узкая версия гварда молчала.
test('гвард имён ловит имя инструмента в коде и не ловит его в комментарии', () => {
  const probe = (code) => offenders('probe.js', code).length;
  for (const code of [
    "if (tool === 'Bash') return true;",
    "if (tool === 'Write') return true;",
    "if (tool === 'Read' || tool === 'Grep') return true;",
    "if (tool === 'Task') return true;",
    "const t = 'mcp__github__create_pull_request';",
    "spawnSync('git', ['check-ignore', file]);",
    "import { isIgnored } from './git.js';",
    "if (cmd.startsWith('arc ')) return true;",
    "const q = 'tracker';",
  ]) {
    assert.equal(probe(code), 1, code);
  }
  assert.equal(probe('// имя Bash знает только адаптер\nreturn scope.reads;'), 0,
    'в комментарии имя инструмента законно: он объясняет, почему имени нет в коде');
  assert.equal(probe('return scope.reads === true;'), 0);
  assert.equal(probe('const gitLike = 0;'), 0, 'часть слова именем инструмента не является');
});

// Файл долга проверяется тем же гвардом: он в списке не потому, что чист, а
// потому, что его переезд запланирован.
test('файлы долга и правда несут имена инструментов', () => {
  const clean = [...DEBT].filter((name) => offenders(path.join(LIB, name)).length === 0);
  assert.deepEqual(clean, [], 'файл долга уже чист — строку из списка надо снять');
});

// Временные файлы кейса живут в системном временном каталоге, а не в дереве:
// упавший ассерт иначе оставлял бы .js-файл внутри `.claude/hooks/`, где его
// подхватят соседние гварды.
test('гвард читает файл с диска так же, как строку', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tool-names-'));
  try {
    const file = path.join(dir, 'probe.js');
    fs.writeFileSync(file, "if (tool === 'Bash') return true;\n");
    assert.equal(offenders(file).length, 1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
