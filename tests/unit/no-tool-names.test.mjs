// Гвард правила 9: в ОБЩЕЙ части слоя (`lib/`, кроме адаптеров) нет новых имён
// инструментов, а старые сосчитаны поимённо. Разбор команд и вывода инструмента
// живёт в файле `<возможность>-<инструмент>`, а общая часть получает его
// результат данными.
//
// Проверка идёт по исходникам, а не по обещанию в шапке: имена инструментов уже
// дважды переезжали из хуков в `lib/` незаметно — сперва `toolFlags`, потом
// списки читающих и сессионных вызовов, — и каждый раз это находил человек.
//
// Что гвард НЕ проверяет, сказано прямо, чтобы зелёный прогон не читался как
// «долга нет»:
// — хуки (`universal-*`, `craft-*`): сегодня они и есть обёртки, и имена
//   инструментов в них законны; долг «обёртка тоже не должна знать имён» — 1.6;
// — привязку к ХАРНЕСУ (`CLAUDE_*`, `.claude/`, поля события) в `lib/` ловит
//   отдельный счёт ниже, тоже по списку, а не по обещанию.
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

// Долг: сколько строк с именами инструментов ЕЩЁ живёт в общем модуле. Счёт
// точный, а не «файл разрешён целиком»: новая строка в файле долга — такое же
// нарушение, как первая строка в чистом файле, и гвард обязан её показать.
// Каждая строка списка названа в теле PR разделом 1.6 или заметкой на фазу 4.
const DEBT = new Map([
  ['git.js', 2], // команды git; файл целиком уезжает в адаптер
  ['transcript.js', 1], // WRITE_TOOLS: имена правящих инструментов харнеса
  ['env.js', 1], // commonDir из git.js
  ['classifier.js', 2], // запуск классификатора шеллом и путь к .sh
  ['net.js', 1], // curl
]);

// Имена инструментов харнеса и рабочих систем. Слово ищется целиком, чтобы
// «читать» в русском комментарии не путалось с `Read`; префикс MCP ловится по
// следующей букве, потому что после `mcp__` границы слова нет.
const TOOL_NAMES = new RegExp([
  '\\b(Bash|BashOutput|Read|Write|Edit|MultiEdit|NotebookEdit|Grep|Glob|LS)\\b',
  '\\b(Task|Agent|Workflow|Skill|ExitPlanMode|EnterPlanMode|AskUserQuestion)\\b',
  '\\b(TaskCreate|TaskUpdate|TaskList|TodoWrite|WebFetch|WebSearch|ToolSearch)\\b',
  '\\b(git|gh|npm|arc|arcadia|crm|tracker|startrek|yandex-team|craft_write|craft_read)\\b',
  '\\b(bash|sh|zsh|curl|wget|sleep|jq|rg|sed|awk|python|python3)\\b',
  'mcp__[A-Za-z_]',
].join('|'));

// Привязка к харнесу: переменные, пути его состояния и поля его события.
const HARNESS_NAMES = /CLAUDE_[A-Z_]+|\.claude\b|hook_event_name|tool_name|tool_input|tool_response|session_id/;

// Долг по харнесу — тоже счётом. Правило 10 запрещает заводить НОВУЮ привязку,
// а не требует снять старую сегодня.
const HARNESS_DEBT = new Map([
  ['env.js', 2], // каталог состояния харнеса
  ['event.js', 4], // поля события харнеса
  ['metrics.js', 3], // регистрация диспетчера и session_id события
  ['once.js', 2], // ключ уступки по полям события
  ['paths.js', 2], // CLAUDE_CODE_SESSION_ID и каталог состояния
  ['write-targets.js', 3], // политика ~/.claude как системной зоны
]);

// Код строки без комментария. Строковые литералы вырезаются ПЕРВЫМИ: без этого
// `//` внутри 'https://…' обрубал строку, и имя инструмента за ним пропадало.
export function codeOf(line) {
  const bare = line.replace(/'[^']*'|"[^"]*"|`[^`]*`/g, (lit) => lit.replace(/\/\//g, '~~'));
  const cut = bare.indexOf('//');
  return (cut < 0 ? bare : bare.slice(0, cut)).replace(/~~/g, '//');
}

export function offenders(file, text = fs.readFileSync(file, 'utf8'), names = TOOL_NAMES) {
  const found = [];
  for (const [n, line] of text.split('\n').entries()) {
    // Комментарий имеет право назвать инструмент: он объясняет, ПОЧЕМУ имени
    // нет в коде. Гвард смотрит на код.
    if (names.test(codeOf(line))) found.push(`${path.basename(file)}:${n + 1}: ${line.trim()}`);
  }
  return found;
}

// Счёт нарушений по всем общим модулям: имя файла → сколько строк.
function census(names) {
  const counts = new Map();
  for (const name of fs.readdirSync(LIB)) {
    if (!name.endsWith('.js') || isAdapter(name)) continue;
    const n = offenders(path.join(LIB, name), undefined, names).length;
    if (n) counts.set(name, n);
  }
  return counts;
}

test('в общей части слоя нет имён инструментов сверх названного долга', () => {
  assert.deepEqual(
    [...census(TOOL_NAMES)].sort(),
    [...DEBT].sort(),
    'счёт разошёлся с долгом: имя инструмента в общем модуле — это адаптер, которого нет, а исчезнувший долг надо снять из списка',
  );
});

test('новых привязок к харнесу в общей части не заведено', () => {
  assert.deepEqual(
    [...census(HARNESS_NAMES)].sort(),
    [...HARNESS_DEBT].sort(),
    'счёт разошёлся с долгом по харнесу: новую привязку заводить нельзя, снятую — надо убрать из списка',
  );
});

// Сам гвард обязан ловить: без этого «список пуст» ничего не значит. Пробы —
// ровно те формы, на которых прежние, более узкие версии гварда молчали.
test('гвард имён ловит имя инструмента в коде и не ловит его в комментарии', () => {
  const probe = (code, names = TOOL_NAMES) => offenders('probe.js', code, names).length;
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
    "if (name === 'yandex-team') return true;",
    "spawnSync('bash', [bin, mode]);",
    "spawnSync('curl', args);",
    "execFileSync('sleep', ['0.2']);",
    "const u = 'https://x'; if (tool === 'Bash') return true;",
  ]) {
    assert.equal(probe(code), 1, code);
  }
  for (const code of [
    "const sid = process.env.CLAUDE_CODE_SESSION_ID;",
    "const f = path.join(home, '.claude', 'settings.json');",
    "return event.hook_event_name || '';",
  ]) {
    assert.equal(probe(code, HARNESS_NAMES), 1, code);
  }
  assert.equal(probe('// имя Bash знает только адаптер\nreturn scope.reads;'), 0,
    'в комментарии имя инструмента законно: он объясняет, почему имени нет в коде');
  assert.equal(probe('return scope.reads === true;'), 0);
  assert.equal(probe('const gitLike = 0;'), 0, 'часть слова именем инструмента не является');
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
