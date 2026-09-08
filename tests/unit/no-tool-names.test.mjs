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
// — хуки (`universal-*`, `craft-*`): они и есть обёртки, и имена инструментов в
//   них законны; что и обёртка не должна их знать — задача не этого гварда;
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

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const LIB = path.join(ROOT, '.claude', 'hooks', 'lib');
const MODULES = path.join(ROOT, 'modules');

// Адаптер узнаётся по имени: `<возможность>-<инструмент>.js`. Инструменты, под
// которые адаптеры уже есть, названы поимённо — иначе в список попадал бы любой
// файл с дефисом.
const TOOLS = ['git', 'bash', 'claude', 'craft', 'curl'];
const isAdapter = (name) => TOOLS.some((t) => name.endsWith(`-${t}.js`));

// Долг: сколько ВХОЖДЕНИЙ имени инструмента ЕЩЁ живёт в общем модуле. Счёт по
// вхождениям, а не по строкам и не «файл разрешён целиком»: вторая привязка,
// дописанная в уже посчитанную строку, при счёте строк проходила молча, поэтому
// у `classifier.js` два вхождения на двух строках. Файлы списка названы в теле
// PR разделом 1.6 или заметкой на фазу 4.
// Долга по именам инструментов в общей части БОЛЬШЕ НЕТ: разбор транскрипта
// уехал в transcript-claude.js, запуск классификатора — в classify-bash.js,
// запасной канал сети — в fetch-curl.js, а адаптер рабочей копии приходит
// параметром с края. Пустая карта — это утверждение, а не пропуск проверки:
// любое новое имя разойдётся с ней и уронит кейс.
const DEBT = new Map([]);

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
  // `session_id` из списка УБРАН: по решению 17 это имя КАНОНИЧЕСКОГО ядра
  // события, а не поле харнеса, и общая часть обязана его знать. Имена, которые
  // остались, — только харнесные: своего аналога у них нет.
  'hook_event_name|tool_name|tool_input|tool_response|tool_use_id|transcript_path|permission_mode',
  'hookSpecificOutput|permissionDecision|permissionDecisionReason|systemMessage',
  "'assistant'|\"assistant\"|'user'|\"user\"|'tool_use'|\"tool_use\"|'tool_result'|\"tool_result\"",
  'input_tokens|output_tokens|cache_read_input_tokens|cache_creation_input_tokens',
  'file_path|notebook_path|subagent_type',
  // Поля события, которые общая часть читает напрямую, и канал между хуками
  // одного события: и то и другое — привязка к харнесу, и новую заводить нельзя.
  // `event.` тут носитель, и слабость та же, что ниже: `const ev = raw.event`
  // счёт обойдёт. Без носителя `cwd` и `prompt` ловили бы `process.cwd()` и наши
  // собственные поля, поэтому счёт здесь — нижняя граница.
  // `event.cwd` тоже ушёл из списка: `cwd` — поле ядра. Остались поля, которых у
  // ядра нет и которые читает только обёртка.
  '\\bevent\\.(prompt|source|stop_hook_active)\\b|is_error|isError',
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
  "'(PreToolUse|PostToolUse|UserPromptSubmit|Stop|SubagentStop|SessionStart|SessionEnd|PreCompact|Notification)'"
    + '|"(PreToolUse|PostToolUse|UserPromptSubmit|Stop|SubagentStop|SessionStart|SessionEnd|PreCompact|Notification)"',
  // СЛУЖЕБНЫЕ поля входа инструментов харнеса: их отсеивает адаптер, и знать их
  // имена общая часть не должна. Ловятся строковым литералом — как их и пишут в
  // списке. `'timeout'` в список не входит: так же называется и утилита шелла, и
  // `write-targets-bash.js` законно упоминает её в своём словаре команд; поле
  // `input.timeout` ловится отдельно, а литерал — пробел, названный здесь.
  "'(description|run_in_background|shell_id)'|\"(description|run_in_background|shell_id)\"|\\binput\\.timeout\\b",
];
const HARNESS_NAMES = new RegExp(HARNESS_SOURCE.join('|'));

// Долг по харнесу — тоже счётом. Правило 10 запрещает заводить НОВУЮ привязку,
// а не требует снять старую сегодня.
const HARNESS_DEBT = new Map([
  // Оба оставшихся долга — про КАТАЛОГ настроек харнеса как место на диске, а не
  // про его событие, решение или транскрипт. Они уйдут вместе с решением о том,
  // где живут файлы слоя, и здесь названы поимённо, чтобы зелёный прогон не
  // читался как «долга нет».
  ['paths.js', 1], // свой файл предодобренной зоны лежит в каталоге настроек харнеса
  ['write-targets.js', 4], // политика «каталог настроек — системная зона»
]);

// Код строки без комментария. Внутри строковых литералов знак комментария
// МАСКИРУЕТСЯ, а само содержимое литерала остаётся кодом: `spawnSync('git', …)`
// — это вызов инструмента, а не комментарий. Без маскировки `//` в 'https://…'
// обрубал строку, и имя инструмента за ним пропадало.
//
// Знак приходит параметром: у питона он свой, и счёт по `//` пропускал бы в
// коде пакетов ровно то, ради чего гвард и стоит.
// Метка маскировки — символ, которого в исходнике не бывает. Возьми обычный
// (пробел, тильду), и обратная замена превратила бы в знак комментария КАЖДОЕ
// его вхождение в строке, а не только замаскированное.
const MASK = '\u0000';
function codeOf(line, marker = '//') {
  const mask = new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
  const bare = line.replace(/'[^']*'|"[^"]*"|`[^`]*`/g, (lit) => lit.replace(mask, MASK));
  const cut = bare.indexOf(marker);
  return (cut < 0 ? bare : bare.slice(0, cut)).split(MASK).join(marker);
}

// Код питоновского файла: строки документации выброшены целиком. Это то же
// правило, по которому комментарий имеет право назвать инструмент — шапка
// модуля объясняет, ПОЧЕМУ имени нет в коде, и адаптеру оболочки без слова
// «оболочка» своего контракта не описать.
const FENCE = /"""|'''/;
function pythonCode(text) {
  const out = [];
  let inside = '';
  for (const line of text.split('\n')) {
    let rest = line;
    let code = '';
    for (;;) {
      if (inside) {
        const at = rest.indexOf(inside);
        if (at < 0) { rest = ''; break; }
        rest = rest.slice(at + 3);
        inside = '';
        continue;
      }
      const open = rest.match(FENCE);
      if (!open) { code += rest; break; }
      code += rest.slice(0, open.index);
      inside = open[0];
      rest = rest.slice(open.index + 3);
    }
    out.push(code);
  }
  return out.join('\n');
}

function offenders(file, text = fs.readFileSync(file, 'utf8'), names = TOOL_NAMES, marker = '//') {
  const found = [];
  const body = marker === '#' ? pythonCode(text) : text;
  for (const [n, line] of body.split('\n').entries()) {
    // Комментарий имеет право назвать инструмент: он объясняет, ПОЧЕМУ имени
    // нет в коде. Гвард смотрит на код.
    if (names.test(codeOf(line, marker))) found.push(`${path.basename(file)}:${n + 1}: ${line.trim()}`);
  }
  return found;
}

// Код пакетов: ВСЕ `.py` в дереве пакета, кроме сборки (`dist/`), кейсов
// (`tests/`) и данных (`data/`). Шире, чем `modules/*/scripts/**`, и намеренно:
// файл, положенный мимо `scripts/`, кодом быть не перестаёт.
//
// Данные не входят: имена инструментов в них законны, ровно как в
// `lib/vendor/read-only-rules.json`, и вынос имён в данные ЕСТЬ способ убрать
// их из кода.
function moduleSources(dir = MODULES, out = []) {
  for (const name of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) {
      if (name !== '__pycache__' && name !== 'dist' && name !== 'tests' && name !== 'data') {
        moduleSources(full, out);
      }
    } else if (name.endsWith('.py')) out.push(full);
  }
  return out;
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
    "import { isIgnored } from './repo-git.js';",
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
    "if (response.is_error === true) return true;",
    "const ev = globalThis.hookEvent;",
    "const id = event.tool_use_id;",
    "const t = event.transcript_path;",
    "const mode = event.permission_mode || '';",
    "if (item.type === 'tool_use') return item.name;",
    "const out = { hookEventName: 'PreToolUse' };",
    'const out = { name: "PostToolUse" };',
    "const VOLATILE = new Set(['description', 'run_in_background']);",
    'const VOLATILE = new Set(["shell_id"]);',
    "if (input.timeout) return true;",
  ]) {
    assert.equal(probe(code, HARNESS_NAMES), 1, code);
  }
  // Имена КАНОНИЧЕСКОГО ядра (решение 17) привязкой не являются: общая часть
  // обязана их знать, и счёт их не трогает.
  for (const code of [
    "const sid = event.session_id || '';",
    "const dir = event.cwd || '';",
    "return { call_id: event.call_id, state_dir: event.state_dir };",
  ]) {
    assert.equal(probe(code, HARNESS_NAMES), 0, code);
  }

  assert.equal(probe('// имя Bash знает только адаптер\nreturn scope.reads;'), 0,
    'в комментарии имя инструмента законно: он объясняет, почему имени нет в коде');
  assert.equal(probe('return scope.reads === true;'), 0);
  assert.equal(probe('const gitLike = 0;'), 0, 'часть слова именем инструмента не является');
});

// Три формы, у которых долга нет и быть не может: переменные харнеса, путь его
// транскрипта и идентификатор его вызова. Ядро даёт им замену (`session_id`,
// факт `tokens`, `call_id`), поэтому здесь не счёт с долгом, а НОЛЬ — иначе
// «долг уменьшился» читалось бы как «работа сделана», пока привязка жива.
const FORBIDDEN = ['CLAUDE_[A-Z_]+', '\\btranscript_path\\b', '\\btool_use_id\\b'];

test('CLAUDE_*, transcript_path и tool_use_id в общей части не встречаются вовсе', () => {
  const found = [];
  for (const name of fs.readdirSync(LIB)) {
    if (!name.endsWith('.js') || isAdapter(name)) continue;
    found.push(...offenders(path.join(LIB, name), undefined, new RegExp(FORBIDDEN.join('|'))));
  }
  assert.deepEqual(found, [],
    'переменные харнеса, его транскрипт и идентификатор его вызова живут только в обёртках и адаптерах');
});

// Сам этот запрет обязан ловить: без пробы «список пуст» ничего не значит.
test('запрет трёх форм ловит каждую из них', () => {
  const rx = new RegExp(FORBIDDEN.join('|'));
  for (const code of [
    "const sid = process.env.CLAUDE_CODE_SESSION_ID;",
    "const root = process.env.CLAUDE_PROJECT_DIR;",
    "const t = event.transcript_path || '';",
    "const id = event.tool_use_id;",
  ]) {
    assert.equal(offenders('probe.js', code, rx).length, 1, code);
  }
  assert.equal(offenders('probe.js', "const id = event.call_id;", rx).length, 0,
    'замена из ядра запретом не считается');
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

// --- код ПАКЕТОВ ---------------------------------------------------------------
//
// Правило 16 действует и на пакеты: функция решения описывает поведение в
// терминах возможностей, а имена инструментов живут в ДАННЫХ пакета. Гвард
// раньше читал только `lib/*.js`, и `decide.py`, назвавший инструмент, прошёл бы
// молча — притом что ради выноса имён в данные пакет и заводился.
//
// Имя разбора оболочки считается именем инструмента: его знает ровно один пакет
// — адаптер `command-tree-shell`, — и знать его больше некому.
const SHFMT = ['\\bshfmt\\b'];
const ADAPTER_PACKAGE = 'command-tree-shell';

const packageOf = (file) => path.relative(MODULES, file).split(path.sep)[0];

test('в коде пакетов нет имён инструментов', () => {
  const found = [];
  for (const file of moduleSources()) {
    // Адаптеру оболочки имя его разбора разрешено — оно и есть тот инструмент,
    // под который он написан. Всё остальное запрещено и ему.
    const names = new RegExp((packageOf(file) === ADAPTER_PACKAGE
      ? TOOL_SOURCE : TOOL_SOURCE.concat(SHFMT)).join('|'));
    found.push(...offenders(file, undefined, names, '#'));
  }
  assert.deepEqual(found, [],
    'имя инструмента в коде пакета — это данные, которых нет: вынеси его в data/ пакета');
});

test('в коде пакетов нет привязок к харнесу', () => {
  const found = [];
  for (const file of moduleSources()) found.push(...offenders(file, undefined, HARNESS_NAMES, '#'));
  assert.deepEqual(found, [],
    'поля и события харнеса живут в его таблице (runtime/harness/), а не в коде пакета');
});

// Гвард пакетов ОБЯЗАН ловить: без пробы «список пуст» ничего не значит. Пробы —
// ровно те формы, на которых счёт по `//` молчал бы.
test('гвард пакетов ловит имя в питоновском коде и не ловит его в комментарии и шапке', () => {
  const all = new RegExp(TOOL_SOURCE.concat(SHFMT).join('|'));
  const py = (code, names = all) => offenders('probe.py', code, names, '#').length;
  assert.equal(py('subprocess.run(["git", "commit"])'), 1);
  assert.equal(py('if word == "sleep":\n    return deny(REASON)'), 1);
  assert.equal(py('BINARY = "shfmt"'), 1, 'имя разбора считается именем инструмента');
  assert.equal(py('# имя git знает только адаптер\nreturn none()'), 0,
    'в комментарии имя инструмента законно: он объясняет, почему имени нет в коде');
  assert.equal(py('return none()  # раньше тут звали git'), 0, 'хвостовой комментарий тоже');
  assert.equal(offenders('probe.py', 'os.environ.get("CLAUDE_CODE_SESSION_ID")', HARNESS_NAMES, '#').length, 1);
});

test('шапка модуля именем инструмента не считается, а код под ней — считается', () => {
  const all = new RegExp(TOOL_SOURCE.concat(SHFMT).join('|'));
  const doc = ['"""Шапка про git и bash.', '', 'Вторая строка тоже про sleep."""', 'return none()'].join('\n');
  assert.equal(offenders('probe.py', doc, all, '#').length, 0, 'шапка — тот же комментарий');
  assert.equal(offenders('probe.py', `${doc}\nrun(["git"])`, all, '#').length, 1,
    'а код после шапки гвард по-прежнему видит');
});
