// Что считать записью и куда она метит. Общий дом для гвардов, которые стоят на
// одних и тех же вызовах: план-гейт и гвард якоря сессии. Разъехавшиеся копии
// этих предикатов дали бы поверхность, где одно и то же место у одного гварда
// гейтится, а у другого нет, и заметно это стало бы только на живом прогоне.
import { isIgnored } from './git.js';

// Путь, правка которого системным изменением не является.
export function isEphemeral(fp) {
  // Файл плана пишет план-мод ДО того, как появится одобрение, — гейт на нём
  // заклинил бы само планирование.
  if (/\/plans\/.*\.md$/.test(fp)) return true;
  if (/^(\/tmp\/|\/private\/tmp\/|\/var\/folders\/|\/private\/var\/folders\/)/.test(fp)) return true;
  if (fp.includes('/scratchpad/')) return true;
  const tmp = process.env.TMPDIR;
  if (tmp && fp.startsWith(`${tmp.replace(/\/$/, '')}/`)) return true;
  // ~/.claude: харнесс непрерывно пишет туда служебное состояние (память,
  // сессии, задачи, тудушки) — оно обязано остаться свободным. Гейтятся только
  // СИСТЕМНЫЕ зоны: скиллы, хуки, агенты, правила, команды, воркфлоу, настройки.
  const home = process.env.HOME || '';
  if (home && fp.startsWith(`${home}/.claude/`)) {
    const rel = fp.slice(`${home}/.claude/`.length);
    const gated = /^(skills|hooks|agents|rules|commands|workflows)\//.test(rel)
      || ['settings.json', 'settings.local.json', 'craft.env'].includes(rel);
    return !gated;
  }
  return false;
}

// Игнорируемое гитом эфемерно (сборка, логи) для ЛЮБОГО инструмента записи,
// кроме путей внутри .claude/: там игнор не оправдание.
export function gitEphemeral(fp) {
  if (fp.startsWith('.claude/') || fp.includes('/.claude/')) return false;
  return isIgnored(fp);
}

// Тела heredoc с ЗАКАВЫЧЕННЫМ маркером вычёркиваются ПЕРВЫМИ, до снятия кавычек:
// после снятия маркер <<'PY' неотличим от << и опознать его нечем. Внутри такого
// тела shell-подстановок не бывает по определению, а «больше» там — сравнение кода
// (i>0:), не перенаправление; сама строка-открыватель остаётся в скане целиком,
// потому что перенаправление формы `cat <<'EOF' > файл` стоит именно на ней.
// Незакавыченный маркер не вычёркивается: в его теле живут подстановки.
function stripQuotedHeredocs(text) {
  const out = [];
  let mark = '';
  let inside = false;
  for (const line of text.split('\n')) {
    if (inside) {
      if (line === mark) inside = false;
      continue;
    }
    const found = line.match(/<<[ \t]*["'][A-Za-z_][A-Za-z0-9_]*["']/);
    if (found) {
      mark = found[0].replace(/^<<[ \t]*["']/, '').replace(/["']$/, '');
      inside = true;
    }
    out.push(line);
  }
  return out.join('\n');
}

// Все совпадения регулярки по строкам текста — как их печатал grep -oE.
function matchAll(text, re) {
  const found = [];
  for (const line of text.split('\n')) {
    for (const m of line.matchAll(new RegExp(re, 'g'))) found.push(m[0]);
  }
  return found;
}

const lastField = (s) => s.trim().split(/\s+/).pop();

// Обезвредить закавыченное во ВСЕЙ команде разом, а не построчно.
//
// Содержимое кавычек СОХРАНЯЕТСЯ, меняются только служебные символы внутри них.
// Прежний разбор выбрасывал закавыченное целиком — и `cat > "/repo/README.md"`
// не давал ни одной цели: правка репозитория проходила гейт молча, потому что
// гейтить было нечего. Обратная беда там же: `jq \'select(.size > 10)\'` давала
// ложную цель, если кавычки не учесть вовсе.
//
// Замена одного символа на другой держит оба конца: путь внутри кавычек остаётся
// целым словом и виден как цель, а «больше», труба и точка с запятой внутри
// кавычек перестают выглядеть перенаправлением и разделителем.
//
// Ещё две беды жили в прежних двух заменах регуляркой — сперва одинарные
// кавычки, потом двойные. Вложенные: одинарные внутри двойных съедались первыми,
// двойные оставались непарными, и в остатке всплывал знак «больше». И строка в
// кавычках, охватывающая перевод строки: многострочная `node -e "…"` разбиралась
// по строкам, со второй строки разбор считал себя вне кавычек, и стрелка `=>` в
// JS-коде читалась как запись в файл.
//
// Переводы строк сохраняются: дальнейшие регулярки работают построчно, и склейка
// строк дала бы им чужие соседства.
//
// Кавычка внутри чужих кавычек — обычный символ, а не открывающая: ровно так её
// читает и сам шелл.
const NEUTRAL = '~';

function stripQuoted(text) {
  let out = '';
  let quote = '';
  for (const ch of text) {
    if (ch === '\n') {
      out += ch;
      continue;
    }
    if (quote) {
      if (ch === quote) {
        quote = '';
        continue;
      }
      out += '<>|&;()'.includes(ch) ? NEUTRAL : ch;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }
    out += ch;
  }
  return out;
}

// Цели записи Bash-команды: перенаправление (> >>), tee, правка на месте (-i),
// cp/mv (последний аргумент либо явная цель после -t), запись из интерпретатора
// (open(…,'w'), write_text/bytes). Гейтится цель, а не команда: сборка,
// копирование в игнорируемый путь и любое чтение целей не дают.
export function bashWriteTargets(cmd) {
  // Знак «больше» бывает и сравнением: в кавычках (jq 'select(.size > 10)') и в условных
  // скобках ([[ a > b ]]). Оба места вычёркиваются — но в ОТДЕЛЬНУЮ строку: разбору
  // записи из интерпретатора нужны буквальные кавычки вокруг пути, на вычеркнутой он бы
  // ослеп. Цена — перенаправление в закавыченную цель (> "мой файл") не увидится.
  const scan = stripQuoted(stripQuotedHeredocs(cmd)).split('\n').map((line) => line
    .replace(/\[\[[^\]]*\]\]/g, ' ')
    .replace(/\(\([^)]*\)\)/g, ' ')).join('\n').replace(/\n+$/, '');

  // Команда идёт КУСКАМИ слева направо, и каждый резолвится тем каталогом,
  // который действует В ЭТОМ МЕСТЕ. Брать последний cd на всю команду нельзя:
  // «cat > README.md && cd /tmp» пишет в текущий каталог, а не во временный, и
  // общий cd выдавал бы запись в репозиторий за эфемерную.
  const targets = [];
  let cwd = '';
  for (const piece of scan.split(/(?:\|\||&&|[;|\n])/)) {
    for (const t of pieceTargets(piece)) targets.push(resolveTarget(cwd, t));
    const cd = /(?:^|[ \t])cd[ \t]+(\/[^\s|&;()<>]*)/.exec(piece);
    if (cd) cwd = cd[1].replace(/\/$/, '');
  }
  return targets.concat(interpreterTargets(cmd));
}

// Цели одного куска команды. Разбор целей не зависит от того, где кусок стоит:
// зависит только резолв относительного пути, и он живёт снаружи.
function pieceTargets(piece) {
  const targets = [];
  for (const m of matchAll(piece, />>?[ \t]*[^|&;()<>\s]+/.source)) {
    targets.push(m.replace(/^>>?[ \t]*/, ''));
  }
  for (const m of matchAll(piece, /\btee\b([ \t]+-[a-zA-Z]+)*[ \t]+[^|&;()<>\s]+/.source)) {
    targets.push(lastField(m));
  }
  for (const m of matchAll(piece, /\b(sed|perl)\b[^|&;]*[ \t]-i[^|&;]*/.source)) {
    for (const word of m.split(' ')) if (/[/.]/.test(word)) targets.push(word);
  }
  for (const m of matchAll(piece, /\b(cp|mv)\b[^|&;]*[ \t]-t[ \t]+[^\s|&;]+/.source)) {
    targets.push(m.replace(/^.*[ \t]-t[ \t]+/, ''));
  }
  if (!/[ \t]-t[ \t]/.test(piece)) {
    for (const m of matchAll(piece, /\b(cp|mv)\b[ \t]+[^|&;()<>]+/.source)) {
      targets.push(lastField(m));
    }
  }
  return targets;
}

// Запись из интерпретатора ищется в ИСХОДНОЙ команде, а не в очищенном тексте:
// путь там стоит в кавычках, и на вычеркнутой строке разбор бы ослеп. По кускам
// такие цели не разложить — регулярка смотрит на весь текст, — поэтому они
// собираются отдельно и каталогом перехода не резолвятся: интерпретатор
// запускается со своим рабочим каталогом, и угадывать его разбор не берётся.
function interpreterTargets(cmd) {
  const targets = [];
  for (const m of matchAll(cmd, /open\([ \t]*['"][^'"]+['"][ \t]*,[ \t]*['"][wa]/.source)) {
    targets.push(m.replace(/^open\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  for (const m of matchAll(cmd, /Path\([ \t]*['"][^'"]+['"][ \t]*\)[ \t]*\.[ \t]*write_(text|bytes)/.source)) {
    targets.push(m.replace(/^Path\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  return targets;
}

// Цель записи, приведённая к настоящему пути. Команда часто переходит в каталог
// и пишет уже относительным именем: «cd /tmp/work && cat > notes.md». Цель,
// взятая как написана, начинается не с /tmp — и запись во временный каталог
// гейтилась, хотя та же запись абсолютным путём проходила свободно.
//
// База берётся из АБСОЛЮТНОГО cd: относительный («cd ..») перевёл бы из
// каталога, которого разбор не знает, и склейка соврала бы.
//
// Путь НОРМАЛИЗУЕТСЯ: без этого «cd /tmp && cat > ../repo/README.md» давал
// /tmp/../repo/README.md, и эфемерность решалась по префиксу /tmp — правка
// репозитория проходила бы гейт через переход вверх.
function resolveTarget(cwd, target) {
  if (!target || target.startsWith('-')) return target;
  const joined = target.startsWith('/') || !cwd
    ? target
    : `${cwd}/${target.replace(/^\.\//, '')}`;
  return joined.startsWith('/') ? normalizePath(joined) : joined;
}

// Свёртка «..» и «.» в абсолютном пути. Свой разбор, а не path.posix.normalize:
// поведение здесь должно быть одинаковым для гейта и гварда якоря независимо от
// платформы, на которой их запустили.
function normalizePath(fp) {
  const parts = [];
  for (const part of fp.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') {
      parts.pop();
      continue;
    }
    parts.push(part);
  }
  return `/${parts.join('/')}`;
}

// Цель записи, очищенная от кавычек; дескрипторы и устройства целями не
// являются и отсеиваются здесь же — пустая строка означает «это не цель».
export function cleanTarget(rawTarget) {
  if (!/\S/.test(rawTarget)) return '';
  if (rawTarget.startsWith('/dev/') || rawTarget.startsWith('-')) return '';
  if (['0', '1', '2', '&1', '&2'].includes(rawTarget)) return '';
  return rawTarget.replace(/"$/, '').replace(/^"/, '').replace(/'$/, '').replace(/^'/, '');
}

// --- Трогает ли вызов мир -----------------------------------------------------

// Гейт стоит на правках МИРА: файлы, командная строка, база, внешние сервисы.
// Всё, что мир не трогает, — не его дело. Отсюда два основания пройти, и у
// каждого своё.
//
// Первое: инструмент только ЧИТАЕТ — менять ему нечего.
const READING_TOOLS = new Set([
  'Read', 'Grep', 'Glob', 'LS', 'WebFetch', 'WebSearch', 'ToolSearch', 'BashOutput',
  'TaskList', 'TaskGet', 'TaskOutput', 'ListAgents', 'ListSkills', 'ListPlugins',
  'ListMcpResourcesTool', 'ReadMcpResourceTool', 'ReadNotifications',
]);

// Второе: инструмент правит ход САМОЙ СЕССИИ, а не мир. План, вопрос Владу,
// список работы, расписание пробуждения — это состояние разговора: реестр про
// них ничего не знает и знать не должен, а сверка спрашивала бы гейт про самого
// себя. Тот же принцип уже записан для файлов: служебное состояние харнесса
// эфемерно, и тудушки названы там прямым текстом.
const SESSION_TOOLS = new Set([
  'TaskCreate', 'TaskUpdate', 'TaskStop', 'ExitPlanMode', 'EnterPlanMode',
  'AskUserQuestion', 'Skill', 'ScheduleWakeup', 'SendMessage', 'SendUserFile',
  'ReportFindings', 'SuggestSkills', 'ShowOnboardingRolePicker',
]);

const READING_VERBS = 'get|list|read|search|fetch|show|describe|resolve|status|view|find|count|check';

// Имя MCP-инструмента говорит само за себя, когда в нём стоит глагол чтения.
// Это не догадка о поведении, а признак: сервер, который пишет, называет
// операцию иначе.
function mcpReads(name) {
  const op = String(name).replace(/^mcp__.*?__/, '');
  // Глагол стоит либо в начале имени (list_repos), либо на конце после
  // подчёркивания (craft_read).
  return new RegExp(`^(${READING_VERBS})(_|$)`, 'i').test(op)
    || new RegExp(`_(${READING_VERBS})$`, 'i').test(op);
}

// Подагенты: их запуск сам по себе мир не трогает — трогает то, что делает
// подагент, и решает это имя его роли.
const SUBAGENT_TOOLS = new Set(['Task', 'Agent', 'Workflow']);

// Читающие подагенты названы поимённо: разведка и критика мира не трогают, а
// гейт на их запуске стоил бы вызова модели на каждом плане.
const READING_AGENTS = new Set([
  'Explore', 'Plan', 'plan-critic', 'plan-critic-unit', 'plan-critic-seams',
  'plan-critic-verdict', 'comment-analyzer', 'type-design-analyzer',
  'silent-failure-hunter', 'typescript-reviewer', 'react-reviewer',
  'pr-test-analyzer', 'claude-code-guide',
]);

// Обслуживание СОБСТВЕННОГО хода: подписаться на события своего PR, разбудить
// себя проверкой через час, снять подписку, переименовать сессию. Мир от этого
// не меняется — меняется то, когда и на что агент проснётся, и реестр про такие
// вещи ничего не знает. Без этого правила гейт запирал агента ровно там, где он
// обязан довести работу до зелёного: подписку и отложенную проверку не
// пропускал, и красный PR оставался без присмотра.
const SESSION_OPS = /(subscribe_pr_activity|send_later|_wakeup|set_session_(title|tags))$/;

// Трогает ли вызов мир. Единственный источник этого признака на весь слой:
// на нём стоит план-гейт (что вообще сверять) и метрики (менял ли ход мир).
export function touchesWorld(tool, input = {}) {
  if (READING_TOOLS.has(tool) || SESSION_TOOLS.has(tool)) return false;
  if (SESSION_OPS.test(tool)) return false;
  if (SUBAGENT_TOOLS.has(tool)) return !READING_AGENTS.has(String(input.subagent_type || ''));
  if (/^mcp__/.test(tool)) return !mcpReads(tool);
  return true;
}

// Подкоманда гита из строки команды: пропускаются глобальные флаги (-C dir,
// -c k=v) и берётся первое слово без дефиса. Слово в аргументах подкомандой не
// становится — иначе `git log --grep push` читался бы как пуш, а
// `git stash push` как отправка в origin.
const GIT_GLOBAL_WITH_VALUE = new Set(['-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path']);

export function gitSubcommand(command) {
  return gitParts(command).sub;
}

// Подкоманда и оставшиеся за ней слова без флагов.
function gitParts(command) {
  const words = String(command || '').trim().split(/\s+/);
  const at = words.findIndex((w) => w === 'git' || w.endsWith('/git'));
  if (at < 0) return { sub: '', rest: [] };
  let sub = '';
  const rest = [];
  for (let i = at + 1; i < words.length; i += 1) {
    const word = words[i];
    if (!sub && GIT_GLOBAL_WITH_VALUE.has(word)) { i += 1; continue; }
    if (word.startsWith('-')) continue;
    if (!sub) sub = word;
    else rest.push(word);
  }
  return { sub, rest };
}

// Подкоманды гита, которые меняют репозиторий или рабочее дерево.
export const GIT_MUTATIONS = new Set([
  'push', 'commit', 'merge', 'rebase', 'reset', 'checkout', 'switch', 'restore',
  'stash', 'tag', 'cherry-pick', 'am', 'apply', 'revert', 'clean', 'rm', 'mv', 'add',
]);

// У части мутирующих подкоманд есть ЧИТАЮЩИЕ формы, и решает их следующее
// слово: `git stash list` и `git stash show` ничего не меняют, `git tag` без
// аргументов просто перечисляет метки.
const GIT_READING_SUBVERBS = new Set(['list', 'show']);

export function gitMutates(command) {
  const { sub, rest } = gitParts(command);
  if (!sub || !GIT_MUTATIONS.has(sub)) return false;
  if (sub === 'stash') return rest.length === 0 || !GIT_READING_SUBVERBS.has(rest[0]);
  if (sub === 'tag') return rest.length > 0;
  return true;
}

