import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import parse from './vendor/shell-quote/parse.js';
import { stripShellWrapper, extractSubstitutions } from './vendor/gemini-shell-guards.js';
// Разбор Bash-команды: что она пишет и куда. Адаптер инструмента — здесь и
// только здесь слой знает синтаксис шелла (перенаправления, кавычки, heredoc,
// переходы между каталогами). Общая часть (write-targets.js) получает отсюда
// ЦЕЛИ и решает по ним сама.
//
// commandTargets(текст) → пути записи, очищенные и без пустых: главный вход,
//   которым пользуется общая часть.
// bashWriteTargets(текст) → те же цели как они найдены, без очистки; cleanTarget
//   очищает одну — гварды печатают и то и другое.
// stripQuoted / stripQuotedHeredocs → обезвреженный текст команды; ими же
//   пользуется адаптер git, потому что команда git приходит той же строкой.

// Тела heredoc с ЗАКАВЫЧЕННЫМ маркером вычёркиваются ПЕРВЫМИ, до снятия кавычек:
// после снятия маркер <<'PY' неотличим от << и опознать его нечем. Внутри такого
// тела shell-подстановок не бывает по определению, а «больше» там — сравнение кода
// (i>0:), не перенаправление; сама строка-открыватель остаётся в скане целиком,
// потому что перенаправление формы `cat <<'EOF' > файл` стоит именно на ней.
// Незакавыченный маркер не вычёркивается: в его теле живут подстановки.
export function stripQuotedHeredocs(text) {
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

export function stripQuoted(text) {
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

// Цели записи команды, очищенные и без пустых. Общая часть спрашивает именно
// это: дескрипторы, устройства и флаги целями не являются, и решать это должен
// тот, кто знает синтаксис.
export function commandTargets(cmd) {
  return bashWriteTargets(cmd).map(cleanTarget).filter(Boolean);
}

// --- доказательство «команда только читает» -----------------------------------
//
// Второй разбор той же shell-команды: не «что она пишет», а «доказано ли, что она
// НИЧЕГО не пишет». Живёт здесь, потому что это тот же инструмент и тот же
// синтаксис; двумя файлами разборы уже расходились в понимании цепочек и кавычек.
// Словарь читающих команд лежит ДАННЫМИ (lib/vendor/read-only-rules.json).
//
// ЗАЧЕМ ИМЕННО ТАК. Прежний гвард спрашивал «куда команда пишет» и искал цели по
// списку шаблонов записи. Такой список разрешает по умолчанию: чего в нём нет —
// проходит, а своя команда записи есть у любого стороннего инструмента. Здесь
// вопрос обратный, и умолчание тоже обратное: недоказанное закрыто.
//
// ЧТО СЧИТАЕТСЯ ДОКАЗАТЕЛЬСТВОМ — данные в vendor/read-only-rules.json. Там
// отобранные под этот предикат команды, их запрещённые флаги и read-only
// подкоманды инструментов. Имена инструментов лежат В ДАННЫХ, и гвард имён
// (tests/unit/no-tool-names.test.mjs) их не считает: он читает только `.js`.
// Значит перенос имён из кода в этот словарь гвард не заметит — это названо в его
// шапке и остаётся так намеренно.
//
// ЧЕМУ НАУЧИЛИ ЧУЖИЕ ПОЛОМКИ:
//   — проверяется КАЖДОЕ звено цепочки, а не первое: приписка через && обходила
//     гварды, смотревшие только на начало строки;
//   — имя, заданное путём, доказательством не считается: подложенный рядом
//     бинарник с именем известной утилиты обходил сверку по имени файла;
//   — обёртки снимаются: запуск через оболочку и ограничитель времени прятали
//     внутреннюю команду;
//   — содержимое подстановки судится как отдельная команда: запрещать её целиком
//     нельзя — соседние гварды сами предписывают рецепты с подстановкой.
//
// НЕИЗВЕСТНОСТЬ — НЕ РАЗРЕШЕНИЕ. Сломался разбор, не читаются правила, встретилась
// незнакомая конструкция — вердикт «не доказано», а не проход.

const RULES_PATH = process.env.READ_ONLY_RULES
  || path.join(path.dirname(fileURLToPath(import.meta.url)), 'vendor', 'read-only-rules.json');

let rulesCache;
function rules() {
  if (rulesCache !== undefined) return rulesCache;
  try {
    rulesCache = JSON.parse(fs.readFileSync(RULES_PATH, 'utf8'));
  } catch {
    // Правила не читаются — это не повод разрешить всё. Молчать тоже нельзя:
    // без строки в служебном выводе поломка выглядит как «гвард придирается».
    process.stderr.write('[write-targets-bash] правила не прочитаны — команды считаются недоказанными\n');
    rulesCache = null;
  }
  return rulesCache;
}

const verdict = (readOnly, cause, offender) => ({ readOnly, cause, offender });

// Обёртки запуска — из тех же правил, что судят читаемость: третий список этих
// слов разъехался бы с ними молча.
const GIT_LIKE_WRAPPERS = new Set([
  'sudo', 'env', 'command', 'time', 'nice', 'ionice', 'nohup', 'stdbuf', 'builtin', 'timeout',
]);

// Операторы, по которым строка распадается на самостоятельные команды. Пайп в
// этом же ряду: звено пайпа — такая же команда, и `cat a | tee out` пишет.
const CHAIN_OPS = new Set(['&&', '||', ';', '|', '&', '|&']);

// Токены команды: shell-quote отдаёт строки и объекты-операторы. Перенаправления
// приходят объектами {op:'>'} — цель следующим токеном.
function splitChain(tokens) {
  const commands = [];
  let current = [];
  for (const token of tokens) {
    if (token && typeof token === 'object' && CHAIN_OPS.has(token.op)) {
      if (current.length) commands.push(current);
      current = [];
      continue;
    }
    current.push(token);
  }
  if (current.length) commands.push(current);
  return commands;
}

// Перенаправление в файл — запись; отвод в пустое устройство и дескрипторы —
// нет, иначе половина обычных читающих вызовов получала бы отказ.
function redirectsToFile(tokens, cfg) {
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (!token || typeof token !== 'object') continue;
    if (token.op !== '>' && token.op !== '>>') continue;
    const target = tokens[i + 1];
    if (typeof target !== 'string') return true;
    if (cfg.nullSinks.includes(target)) continue;
    return true;
  }
  return false;
}

// Слова команды без операторов и без целей перенаправления.
function words(tokens) {
  const out = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token && typeof token === 'object') {
      if (token.op === '>' || token.op === '>>' || token.op === '<') i += 1;
      continue;
    }
    out.push(token);
  }
  return out;
}

// Префикс переменных окружения: `LC_ALL=C.UTF-8 grep …` — команда здесь grep.
function dropEnvPrefix(list) {
  let i = 0;
  while (i < list.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(list[i])) i += 1;
  return list.slice(i);
}

// Обёртки, у которых команда идёт дальше по строке. Флаги обёртки пропускаются
// вместе с их значениями настолько, насколько это видно без её собственной
// грамматики: неизвестное всё равно упрётся в «не доказано».
function dropWrappers(list, cfg) {
  let current = list;
  for (let guard = 0; guard < 4; guard += 1) {
    const head = current[0];
    if (!head || !cfg.wrappers.includes(head)) return current;
    let i = 1;
    while (i < current.length && current[i].startsWith('-')) i += 1;
    // У ограничителя времени первым идёт срок — не команда.
    if (head === 'timeout' && i < current.length && /^[0-9]/.test(current[i])) i += 1;
    current = current.slice(i);
  }
  return current;
}

function flagsOf(list) {
  return list.filter((word) => typeof word === 'string' && word.startsWith('-'));
}

// Одна простая команда: доказано чтение или нет.
function judgeSimple(tokens, cfg) {
  if (redirectsToFile(tokens, cfg)) {
    const name = words(tokens)[0] || 'команда';
    return verdict(false, 'mutates', name);
  }

  const list = dropWrappers(dropEnvPrefix(words(tokens)), cfg);
  const name = list[0];
  if (!name) return verdict(true, null, null);

  // Имя, заданное путём, доказательством не считается: рядом с рабочим каталогом
  // можно положить свой бинарник и назвать его как известную утилиту.
  if (name.includes('/')) return verdict(false, 'unproven', name);

  const spec = cfg.readOnly[name];
  const nested = cfg.readOnlyNested[name];
  const subs = cfg.readOnlySubcommands[name];

  // Заведомо меняющие состояние названы отдельно ради текста отказа: ждать такую
  // команду незачем, её надо не переформулировать, а отложить до якоря.
  const mutating = cfg.mutating || [];
  const mutatingSubs = (cfg.mutatingSubcommands || {})[name] || [];
  if (mutating.includes(name)) return verdict(false, 'mutates', name);
  if (mutatingSubs.length) {
    const sub = list.slice(1).find((word) => !word.startsWith('-'));
    if (sub && mutatingSubs.includes(sub)) return verdict(false, 'mutates', `${name} ${sub}`);
  }

  if (spec) {
    const denied = spec.denyFlags || [];
    const used = flagsOf(list);
    for (const flag of denied) {
      if (used.includes(flag) || list.includes(flag)) return verdict(false, 'mutates', name);
    }
    return verdict(true, null, null);
  }

  if (subs || nested) {
    const sub = list.slice(1).find((word) => !word.startsWith('-'));
    if (!sub) return verdict(false, 'unproven', name);
    if (nested && nested[sub]) {
      const third = list.slice(list.indexOf(sub) + 1).find((word) => word);
      if (third && nested[sub].includes(third)) return verdict(true, null, null);
      return verdict(false, 'unproven', `${name} ${sub}`);
    }
    if (subs && subs.includes(sub)) {
      const denied = (cfg.denySubcommandFlags[name] || {})[sub] || [];
      for (const flag of denied) {
        if (list.some((word) => word === flag || word.startsWith(`${flag}=`))) {
          return verdict(false, 'mutates', `${name} ${sub}`);
        }
      }
      return verdict(true, null, null);
    }
    return verdict(false, 'unproven', `${name} ${sub}`);
  }

  // Незнакомая утилита. Это не «безопасно» и не «опасно» — это «не доказано»,
  // и лечится оно ответом Влада, а не догадкой гварда.
  return verdict(false, 'unproven', name);
}

// Разбор одной строки: подстановки судятся первыми, потом сама строка.
function judgeString(command, cfg, depth) {
  if (depth > 3) return verdict(false, 'unparsed', command.trim().split(/\s+/)[0] || 'команда');

  const unwrapped = stripShellWrapper(command);

  const { substitutions, broken } = extractSubstitutions(unwrapped);
  if (broken) return verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда');
  for (const inner of substitutions) {
    const innerVerdict = judgeString(inner, cfg, depth + 1);
    if (!innerVerdict.readOnly) return innerVerdict;
  }

  let tokens;
  try {
    tokens = parse(unwrapped);
  } catch {
    return verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда');
  }
  // Незакрытая кавычка не бросает исключение — она возвращает хвост одним
  // куском. Считаем такую строку неразобранной: судить по ней нечего.
  if (/^[^']*'[^']*$/.test(unwrapped) || /^[^"]*"[^"]*$/.test(unwrapped)) {
    return verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда');
  }
  if (tokens.some((token) => token && typeof token === 'object' && token.comment !== undefined)) {
    return verdict(false, 'unparsed', unwrapped.trim().split(/\s+/)[0] || 'команда');
  }

  for (const simple of splitChain(tokens)) {
    const simpleVerdict = judgeSimple(simple, cfg);
    if (!simpleVerdict.readOnly) return simpleVerdict;
  }
  return verdict(true, null, null);
}

// classifyCommand(строка) → { readOnly, cause, offender }.
//   cause: 'mutates'   — команда меняет состояние, ждать её незачем;
//          'unproven'  — про неё не видно, что она только читает;
//          'unparsed'  — строка не разобралась, судить нечем.
export function classifyCommand(command) {
  const cfg = rules();
  if (!cfg) return verdict(false, 'unproven', 'правила недоступны');
  if (typeof command !== 'string' || command.trim() === '') return verdict(true, null, null);
  return judgeString(command, cfg, 0);
}

// --- цели ЧТЕНИЯ ---------------------------------------------------------------
//
// Что команда ПРОЧИТАЛА. Спрашивает журнал событий: гвард «не правь того, чего не
// читал» иначе даёт ЛОЖНЫЙ ОТКАЗ на файле, который агент посмотрел `cat`-ом, а не
// читающим инструментом харнеса. Смотреть файл командой — обычный способ работы,
// а ложные отказы и есть то, чем гвард делают невыносимым.
//
// Доказательство берётся у classifyCommand выше: цели называются ТОЛЬКО у
// команды, про которую доказано, что она ничего не пишет. Недоказанная не даёт
// целей вовсе — не потому, что их нет, а потому что назвать их было бы догадкой.
//
// ЧТО СЧИТАЕТСЯ ФАЙЛОВЫМ ОПЕРАНДОМ — знание про КАЖДУЮ команду, а не общее
// правило «слово похоже на путь». Общее правило давало бы ЛОЖНЫЕ цели: у поиска
// первый операнд — образец, и `foo.bar` из образца легло бы в журнал прочитанным
// файлом. Асимметрия здесь жёсткая: пропущенная цель стоит агенту лишнего
// чтения, а ЛОЖНАЯ разрешает править то, чего никто не читал.
//
// Отсюда два узких списка и умолчание «целей нет». Всё, чего в них нет (обход
// каталогов, перечисления, подкоманды инструментов), целей чтения не даёт: их
// операнды — каталоги и имена, а не прочитанное содержимое.

// Все неключевые операнды суть файлы: `cat a b`, `head -20 lib/a.js`.
const FILE_OPERANDS = new Set([
  'cat', 'head', 'tail', 'less', 'more', 'bat', 'nl', 'wc', 'od', 'xxd', 'hexdump',
  'strings', 'file', 'stat', 'cksum', 'md5sum', 'sha1sum', 'sha256sum', 'shasum',
  'diff', 'cmp',
]);

// Первый неключевой операнд — ОБРАЗЕЦ или ПРОГРАММА, файлы идут за ним:
// `grep образец файл`, `sed -n 1,5p файл`.
const PATTERN_THEN_FILES = new Set([
  'grep', 'egrep', 'fgrep', 'rg', 'ag', 'ack', 'sed', 'awk', 'jq', 'yq',
]);

// Перенаправление: сам оператор и то, что за ним. Вход из файла (`< файл`) —
// чтение, выход (`> файл`) целью чтения не является. У доказанно читающей команды
// выход бывает только в пустоту (/dev/null), и cleanTarget его всё равно отсеет.
const REDIRECT_WORD = /^[0-9]*(?:<<?|>>?|&>|>&|<&)$/;

// Файловые операнды ОДНОГО куска команды.
function readOperands(piece) {
  const words = piece.trim().split(/\s+/).filter(Boolean);
  let i = 0;
  // Присваивания окружения и обёртки запуска впереди вызова его не отменяют — тем
  // же правилом, что у разбора вызовов git.
  while (i < words.length
    && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]) || GIT_LIKE_WRAPPERS.has(words[i]))) i += 1;
  const name = words[i] || '';
  const all = FILE_OPERANDS.has(name);
  if (!all && !PATTERN_THEN_FILES.has(name)) return [];
  let skip = !all;
  const out = [];
  for (i += 1; i < words.length; i += 1) {
    const word = words[i];
    if (REDIRECT_WORD.test(word)) {
      const next = words[i + 1] || '';
      i += 1;
      if (word.includes('<') && !word.includes('&')) out.push(next);
      continue;
    }
    // Приклеенное перенаправление (`<файл`, `2>файл`): у входа цель читается, у
    // выхода слово целиком не операнд.
    const glued = /^[0-9]*([<>])(.+)$/.exec(word);
    if (glued) {
      if (glued[1] === '<') out.push(glued[2]);
      continue;
    }
    if (word.startsWith('-')) continue;
    if (skip) { skip = false; continue; }
    out.push(word);
  }
  return out;
}

// commandReads(текст) → {reads, targets}. `reads` — доказано ли, что команда
// только читает; `targets` — что именно, насколько это разобрано.
export function commandReads(cmd) {
  const text = String(cmd || '');
  // Пустая команда чтением не является: classifyCommand зовёт её читающей (ей
  // нечего запрещать), но строки журнала за ней не стоит.
  if (text.trim() === '') return { reads: false, targets: [] };
  if (classifyCommand(text).readOnly !== true) return { reads: false, targets: [] };

  // Разбор по КУСКАМ с тем же резолвом каталога, что у целей записи: `cd /repo &&
  // cat a.js` читает файл репозитория, а не текущего каталога.
  const scan = stripQuoted(stripQuotedHeredocs(text));
  const targets = [];
  let cwd = '';
  for (const piece of scan.split(/(?:\|\||&&|[;|\n])/)) {
    for (const t of readOperands(piece)) targets.push(resolveTarget(cwd, t));
    const cd = /(?:^|[ \t])cd[ \t]+(\/[^\s|&;()<>]*)/.exec(piece);
    if (cd) cwd = cd[1].replace(/\/$/, '');
  }
  return { reads: true, targets: targets.map(cleanTarget).filter(Boolean) };
}
