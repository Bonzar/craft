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
// — привязку к ХАРНЕСУ (`CLAUDE_*`, `.claude/`, поля события, формат решения и
//   транскрипта) в `lib/` ловит отдельный счёт ниже, тоже по списку;
// — ДАННЫЕ и ПОДКАТАЛОГИ: смотрятся только `.js` в самом `lib/`, поэтому имена
//   в соседних `.json` (`lib/vendor/read-only-rules.json` с перечнем читающих
//   команд) и в `lib/vendor/*.js` счётом не покрыты — перенос имён из кода в
//   данные или в подкаталог гвард не заметит.
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
const TOOLS = ['git', 'bash', 'claude', 'craft'];
const isAdapter = (name) => TOOLS.some((t) => name.endsWith(`-${t}.js`));

// Долг: сколько ВХОЖДЕНИЙ имени инструмента ЕЩЁ живёт в общем модуле. Счёт по
// вхождениям, а не по строкам и не «файл разрешён целиком»: вторая привязка,
// дописанная в уже посчитанную строку, при счёте строк проходила молча, поэтому
// у `classifier.js` два вхождения на двух строках. Файлы списка названы в теле
// PR разделом 1.6 или заметкой на фазу 4.
const DEBT = new Map([
  ['transcript.js', 3], // WRITE_TOOLS: имена правящих инструментов харнеса
  ['env.js', 1], // commonDir из адаптера repo-git.js
  ['classifier.js', 2], // запуск классификатора шеллом и путь к .sh
  ['net.js', 1], // curl
]);

// Имена инструментов харнеса и рабочих систем. Слово ищется целиком, чтобы
// «читать» в русском комментарии не путалось с `Read`; префикс MCP ловится по
// следующей букве, потому что после `mcp__` границы слова нет.
const TOOL_SOURCE = [
  '\\b(Bash|BashOutput|Read|Write|Edit|MultiEdit|NotebookEdit|Grep|Glob|LS)\\b',
  '\\b(Task|Agent|Workflow|Skill|ExitPlanMode|EnterPlanMode|AskUserQuestion)\\b',
  '\\b(TaskCreate|TaskUpdate|TaskList|TodoWrite|WebFetch|WebSearch|ToolSearch)\\b',
  '\\b(git|gh|npm|arc|arcadia|crm|tracker|startrek|yandex-team|craft_write|craft_read)\\b',
  '\\b(bash|sh|zsh|curl|wget|sleep|jq|rg|sed|awk|python|python3)\\b',
  'mcp__[A-Za-z_]',
];
const TOOL_NAMES = new RegExp(TOOL_SOURCE.join('|'));

// Привязка к харнесу: его переменные, пути его состояния, поля его события,
// формат его решения и формат его транскрипта. Первый список ловил только
// переменные и поля события — то есть транскрипт Claude в общей части проходил
// молча, хотя тело PR называет его тем же долгом.
const HARNESS_SOURCE = [
  'CLAUDE_[A-Z_]+',
  '\\.claude\\b',
  'hook_event_name|tool_name|tool_input|tool_response|tool_use_id|session_id|transcript_path|permission_mode',
  'hookSpecificOutput|permissionDecision|permissionDecisionReason|systemMessage',
  "'assistant'|\"assistant\"|'user'|\"user\"|'tool_use'|\"tool_use\"|'tool_result'|\"tool_result\"",
  'input_tokens|output_tokens|cache_read_input_tokens|cache_creation_input_tokens',
  'file_path|notebook_path|subagent_type',
  // Поля события, которые общая часть читает напрямую, и канал между хуками
  // одного события: и то и другое — привязка к харнесу, и новую заводить нельзя.
  // `event.` тут носитель, и слабость та же, что ниже: `const ev = raw.event`
  // счёт обойдёт. Без носителя `cwd` и `prompt` ловили бы `process.cwd()` и наши
  // собственные поля, поэтому счёт здесь — нижняя граница.
  '\\bevent\\.(cwd|prompt|source|stop_hook_active)\\b|is_error|isError',
  'globalThis\\.hook[A-Z]',
  // Поля транскрипта ловятся по ИМЕНИ ПОЛЯ, а не по имени переменной: с
  // привязкой к носителю (`entry.`, `item.`) хватало переименования локальной
  // переменной, чтобы новая привязка прошла молча.
  '\\.(role|model|thinking|parentUuid|toolUseResult)\\b',
  // Общие слова (`type`, `content`, `text`, `usage`, `id`) ловятся только у
  // носителей записи транскрипта: своё поле `usage` есть и у нашей записи
  // журнала, и считать его долгом было бы ложью.
  '\\bentry\\.(type|message|timestamp)\\b|\\bmessage\\.(id|usage|content)\\b|\\bitem\\.(name|input|type|text)\\b',
  // Имена событий харнеса и поле решения, которое их несёт: `PreToolUse` и
  // соседи — его словарь, и общая часть их знать не должна.
  'hookEventName',
  "'(PreToolUse|PostToolUse|UserPromptSubmit|Stop|SubagentStop|SessionStart|SessionEnd|PreCompact|Notification)'",
  // СЛУЖЕБНЫЕ поля входа инструментов харнеса: их отсеивает адаптер, и знать их
  // имена общая часть не должна. Ловятся строковым литералом — как их и пишут в
  // списке. `'timeout'` в список не входит: так же называется и утилита шелла, и
  // `read-only-command.js` законно упоминает её в своём словаре команд; поле
  // `input.timeout` ловится отдельно, а литерал — пробел, названный здесь.
  "'(description|run_in_background|shell_id)'|\\binput\\.timeout\\b",
];
const HARNESS_NAMES = new RegExp(HARNESS_SOURCE.join('|'));

// Долг по харнесу — тоже счётом. Правило 10 запрещает заводить НОВУЮ привязку,
// а не требует снять старую сегодня.
const HARNESS_DEBT = new Map([
  ['decide.js', 20], // формат решения харнеса, имена его событий и канал между хуками
  ['env.js', 2], // каталог состояния харнеса
  ['event.js', 13], // поля события харнеса, включая форму ответа инструмента
  ['metrics.js', 18], // регистрация диспетчера, session_id и формат транскрипта
  ['once.js', 6], // ключ уступки по полям события
  ['paths.js', 2], // CLAUDE_CODE_SESSION_ID и каталог состояния
  ['transcript.js', 15], // формат транскрипта Claude целиком
  ['write-targets.js', 4], // политика ~/.claude как системной зоны
]);

// Код строки без комментария. Внутри строковых литералов `//` МАСКИРУЕТСЯ, а
// само содержимое литерала остаётся кодом: `spawnSync('git', …)` — это вызов
// инструмента, а не комментарий. Без маскировки `//` в 'https://…' обрубал
// строку, и имя инструмента за ним пропадало.
function codeOf(line) {
  const bare = line.replace(/'[^']*'|"[^"]*"|`[^`]*`/g, (lit) => lit.replace(/\/\//g, '~~'));
  const cut = bare.indexOf('//');
  return (cut < 0 ? bare : bare.slice(0, cut)).replace(/~~/g, '//');
}

function offenders(file, text = fs.readFileSync(file, 'utf8'), names = TOOL_NAMES) {
  const found = [];
  for (const [n, line] of text.split('\n').entries()) {
    // Комментарий имеет право назвать инструмент: он объясняет, ПОЧЕМУ имени
    // нет в коде. Гвард смотрит на код.
    if (names.test(codeOf(line))) found.push(`${path.basename(file)}:${n + 1}: ${line.trim()}`);
  }
  return found;
}

// Сколько ВХОЖДЕНИЙ имени в файле (см. шапку про счёт у DEBT).
function hits(file, source) {
  const g = new RegExp(source.join('|'), 'g');
  let total = 0;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    total += (codeOf(line).match(g) || []).length;
  }
  return total;
}

// Счёт нарушений по всем общим модулям: имя файла → сколько ВХОЖДЕНИЙ.
function census(source) {
  const counts = new Map();
  for (const name of fs.readdirSync(LIB)) {
    if (!name.endsWith('.js') || isAdapter(name)) continue;
    const n = hits(path.join(LIB, name), source);
    if (n) counts.set(name, n);
  }
  return counts;
}

test('в общей части слоя нет имён инструментов сверх названного долга', () => {
  assert.deepEqual(
    [...census(TOOL_SOURCE)].sort(),
    [...DEBT].sort(),
    'счёт разошёлся с долгом: имя инструмента в общем модуле — это адаптер, которого нет, а исчезнувший долг надо снять из списка',
  );
});

test('новых привязок к харнесу в общей части не заведено', () => {
  assert.deepEqual(
    [...census(HARNESS_SOURCE)].sort(),
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
    "if (entry.type !== 'assistant') return 0;",
    "return u.cache_creation_input_tokens;",
    "const p = input.file_path;",
    "out.hookSpecificOutput = { permissionDecision: 'deny' };",
    "const model = entry.message && entry.message.model;",
    "const dir = event.cwd || '';",
    "if (response.is_error === true) return true;",
    "const ev = globalThis.hookEvent;",
    "const mode = event.permission_mode || '';",
    "if (item.type === 'tool_use') return item.name;",
    "const out = { hookEventName: 'PreToolUse' };",
    "const VOLATILE = new Set(['description', 'run_in_background']);",
    "if (input.timeout) return true;",
  ]) {
    assert.equal(probe(code, HARNESS_NAMES), 1, code);
  }
  assert.equal(probe('// имя Bash знает только адаптер\nreturn scope.reads;'), 0,
    'в комментарии имя инструмента законно: он объясняет, почему имени нет в коде');
  assert.equal(probe('return scope.reads === true;'), 0);
  assert.equal(probe('const gitLike = 0;'), 0, 'часть слова именем инструмента не является');
});

test('счёт считает вхождения, а не строки', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tool-hits-'));
  try {
    const file = path.join(dir, 'probe.js');
    fs.writeFileSync(file, 'const c = entry.message && entry.message.content;\n');
    assert.equal(offenders(file, undefined, HARNESS_NAMES).length, 1, 'строка одна');
    assert.equal(hits(file, HARNESS_SOURCE), 2, 'а привязок в ней две');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
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
