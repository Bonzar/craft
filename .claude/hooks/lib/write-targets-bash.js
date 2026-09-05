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

// Переход каталога по словам ОДНОГО звена цепочки. Правило ОДНО на обе стороны
// файла — на цели записи и на цели чтения, — потому что синтаксис у них один, а
// два правила в одном файле разъезжаются тем же манером, что два файла: об этом
// прямо предупреждает шапка.
//
// `cd` считается переходом, только если он ПЕРВОЕ слово звена. Свободный поиск
// слова `cd` где угодно давал обход гвардов: у `grep -n cd /tmp/a.txt && cat >
// README.md` цель записи уезжала под `/tmp/`, объявлялась эфемерной, и правка
// рабочего файла проходила мимо план-гейта, гварда якоря и защиты конфигов.
//
// Неабсолютный путь СБРАСЫВАЕТ каталог, а не оставляет прежний: переход
// состоялся, а куда — неизвестно, и держаться за старый значит приклеивать его к
// чужим путям. Пустой каталог оставляет цель относительной, то есть скорее
// долговечной, — сторона осторожная.
export function nextCwd(current, words) {
  if (words[0] !== 'cd') return current;
  const to = words[1];
  // Нераскрытая переменная в САМОМ каталоге перехода — такая же выдумка, как в
  // операнде: отсев стоял только у операндов, и `cd /repo/$SUB` давал путь со
  // служебной меткой внутри.
  if (typeof to !== 'string' || !to.startsWith('/') || isUnresolved(to)) return '';
  return to.replace(/\/$/, '');
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
    cwd = nextCwd(cwd, piece.trim().split(/\s+/).filter(Boolean));
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

// Тела heredoc — не команды. Снимаются ОБЕ формы маркера, в отличие от
// stripQuotedHeredocs выше: там незакавыченный маркер оставляют нарочно, потому
// что в его теле живут подстановки, а здесь тело режется на строки и каждая
// судилась бы отдельной командой. Текст «rm -rf …» внутри печатаемого документа
// командой не является, и отказ на нём был бы ложным. Подстановки при этом
// разбираются раньше, целиком по строке.
function stripHeredocBodies(text) {
  const out = [];
  let mark = '';
  let inside = false;
  for (const line of String(text).split('\n')) {
    if (inside) {
      if (line.trim() === mark) inside = false;
      continue;
    }
    const found = line.match(/<<-?[ \t]*(["']?)([A-Za-z_][A-Za-z0-9_]*)\1/);
    if (found) {
      [, , mark] = found;
      inside = true;
    }
    out.push(line);
  }
  return out.join('\n');
}

// Строки команды: перевод строки разделяет команды ровно так же, как `;`, но
// токенизатор его НЕ ВЫДАЁТ — для него это обычный пробел. Оттого многострочная
// команда схлопывалась в одну: имя бралось из первой строки, а слова остальных
// становились её операндами, и `cat a.js` с `rm -rf …` на второй строке
// доказывался читающим. На этом предикате стоит отказ гварда якоря сессии.
//
// Режется только по НЕЗАКАВЫЧЕННЫМ переводам строки: многострочная строка в
// кавычках — один аргумент, и рвать её значило бы судить её обрывки командами.
export function commandLines(text) {
  const body = stripHeredocBodies(text);
  const lines = [];
  let quote = '';
  let start = 0;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (quote) {
      if (ch === quote) quote = '';
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; continue; }
    if (ch === '\\') { i += 1; continue; }
    if (ch === '\n') {
      lines.push(body.slice(start, i));
      start = i + 1;
    }
  }
  lines.push(body.slice(start));
  return lines.filter((line) => line.trim() !== '');
}

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

// Использован ли ЗАПРЕЩЁННЫЙ ключ. Сверка по НАЧАЛУ слова, а не точным
// равенством: `sed -i.bak` и `--in-place=.bak` — та же правка на месте, что
// `sed -i`, и точное равенство их пропускало. На этом предикате стоит отказ
// гварда якоря сессии, то есть пропуск здесь — правка файла репозитория без
// якоря. Форма `--ключ=значение` уже разбиралась так у подкоманд; здесь она была
// забыта вместе с суффиксной.
function usesFlag(word, denied) {
  return denied.some((flag) => word === flag
    || word.startsWith(`${flag}=`)
    // Короткий ключ со СЛИПШИМСЯ значением (`-i.bak`). У длинных так не бывает.
    || (!flag.startsWith('--') && flag.length === 2 && word.startsWith(flag) && word.length > 2));
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
    if (list.some((word) => usesFlag(word, denied))) return verdict(false, 'mutates', name);
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
  // Каждая СТРОКА — самостоятельная команда, и достаточно одной непрочитанной,
  // чтобы весь вызов перестал быть доказанно читающим.
  const lines = commandLines(command);
  if (lines.length > 1) {
    for (const line of lines) {
      const lineVerdict = judgeString(line, cfg, depth);
      if (!lineVerdict.readOnly) return lineVerdict;
    }
    return verdict(true, null, null);
  }

  const unwrapped = stripShellWrapper(lines[0] === undefined ? command : lines[0]);

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
// команды, про которую доказано, что она ничего не пишет.
//
// РАЗБОР ИДЁТ ПО ТОКЕНАМ, а не по словам обезвреженного текста, и это не
// педантизм. `stripQuoted` кавычки снимает, содержимое СОХРАНЯЯ, — многословный
// образец распадался на слова, и `grep -rn "см. README.md" .` называл
// прочитанным файл README.md, которого никто не открывал. Токенизатор оставляет
// закавыченный аргумент ОДНИМ токеном; он же отделяет операторы от слов и
// подстановку от операндов.
//
// ЧТО СЧИТАЕТСЯ ФАЙЛОВЫМ ОПЕРАНДОМ — знание про КАЖДУЮ команду, а не общее
// правило «слово похоже на путь». Асимметрия здесь жёсткая: пропущенная цель
// стоит агенту лишнего чтения, а ЛОЖНАЯ разрешает править то, чего никто не
// читал. Поэтому три сита подряд — список команд, таблица ключей со значением и
// вид пути, — и умолчание «целей нет».

// У этих команд ВСЕ неключевые операнды суть файлы: `cat a b`, `head -20 a.js`.
const FILE_OPERANDS = new Set([
  'cat', 'head', 'tail', 'less', 'more', 'bat', 'nl', 'wc', 'od', 'xxd', 'hexdump',
  'strings', 'file', 'stat', 'cksum', 'md5sum', 'sha1sum', 'sha256sum', 'shasum',
  'diff', 'cmp',
]);

// У этих первый неключевой операнд — ОБРАЗЕЦ или ПРОГРАММА, файлы идут за ним:
// `grep образец файл`, `sed -n 1,5p файл`.
const PATTERN_THEN_FILES = new Set([
  'grep', 'egrep', 'fgrep', 'sed', 'awk', 'jq', 'yq',
]);

// Рекурсивный обход целей чтения не даёт: операнд у него — КАТАЛОГ, а какие
// файлы под ним прочитаны, назвать нечем. Выдать каталог за прочитанный файл
// нельзя, а хвостовой косой чертой обычная его форма себя не выдаёт. Поиск,
// рекурсивный ПО УМОЛЧАНИЮ (rg, ag, ack), по этой же причине в список выше не
// входит вовсе: у него операнд неотличим от каталога никаким флагом.
// Ключ рекурсии ищется в СВЯЗКЕ коротких (`-rn`), и только у тех команд, у
// которых он значит именно рекурсию: у потокового редактора `-r` — это
// расширенные регулярки, и общее правило зря лишало бы его целей.
const RECURSIVE_CAPABLE = new Set(['grep', 'egrep', 'fgrep', 'diff']);
const RECURSIVE_FLAG = /^(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive|--dereference-recursive)$/;

// Ключи, забирающие значение СЛЕДУЮЩИМ словом. Без этой таблицы значение занимает
// слот образца, а сам образец уезжает в цели: у `grep -A 3 package.json src/a.js`
// прочитанным файлом оказывался `package.json`, то есть образец.
const SEARCH_VALUE_FLAGS = [
  '-e', '-f', '-m', '-A', '-B', '-C', '-D', '-d', '--regexp', '--file', '--max-count',
  '--after-context', '--before-context', '--context', '--include', '--exclude',
  '--exclude-dir', '--exclude-from', '--label',
  '--binary-files', '--devices', '--directories',
];
const FLAG_TAKES_VALUE = new Map([
  ['grep', SEARCH_VALUE_FLAGS], ['egrep', SEARCH_VALUE_FLAGS], ['fgrep', SEARCH_VALUE_FLAGS],
  ['rg', SEARCH_VALUE_FLAGS], ['ag', SEARCH_VALUE_FLAGS], ['ack', SEARCH_VALUE_FLAGS],
  ['sed', ['-l', '--line-length']],
  ['awk', ['-v', '--assign', '-F', '--field-separator']],
  ['jq', ['--arg', '--argjson', '--slurpfile', '--rawfile', '--indent']],
  ['yq', ['--arg', '--indent']],
  ['head', ['-c', '-n', '--bytes', '--lines']],
  ['tail', ['-c', '-n', '--bytes', '--lines', '--pid']],
  ['nl', ['-b', '-d', '-f', '-h', '-i', '-l', '-n', '-s', '-v', '-w']],
  ['od', ['-A', '-j', '-N', '-t', '-w']],
  ['xxd', ['-c', '-g', '-l', '-s']],
  ['diff', ['-U', '--unified', '-D', '--ifdef', '--label', '-I', '--ignore-matching-lines',
    '-x', '--exclude', '-X', '--exclude-from', '-S', '--starting-file', '-F',
    '--show-function-line', '-W', '--width']],
  ['less', ['-p', '--pattern', '-j', '--jump-target', '-x', '--tabs', '-P', '--prompt']],
  ['more', ['-p']],
  ['bat', ['-l', '--language', '-r', '--line-range', '-H', '--highlight-line', '--theme',
    '--style', '--pager', '-m', '--map-syntax', '--file-name']],
]);

// Ключи, которые САМИ несут образец: после них первый операнд — уже файл, и
// съедать его как образец нельзя.
const PATTERN_FLAGS = new Set(['-e', '-f', '--regexp', '--file']);

// Метка нераскрытой переменной. Токенизатор подставляет неизвестную переменную
// ПУСТОТОЙ, и `cat $HOME/секрет.md` давал целью `/секрет.md` — путь, которого не
// существует, вместо настоящего прочитанного файла. Это догадка на месте факта, и
// её надо не чинить, а отбрасывать: слово с меткой целью не становится. Литерал
// в одинарных кавычках переменной не является и метки не получает.
const UNRESOLVED = '\u0000нераскрыто\u0000';

// Похоже ли слово на ПУТЬ К ФАЙЛУ. Третье сито: числа (значения ключей), куски
// образца, маркеры heredoc и прочее целями не становятся. Каталог целью чтения
// тоже не является — рекурсивный поиск читает файлы под ним, а назвать их нечем,
// и выдать каталог за прочитанный файл значило бы соврать.
function looksLikePath(word) {
  if (typeof word !== 'string' || word === '' || word.startsWith('-')) return false;
  if (word.endsWith('/')) return false;
  // Символы подстановки и тильду токенизатор не раскрывает, а мы раскрыть не
  // можем: записать их прочитанным путём значило бы назвать файл, которого нет.
  if (/[{}[\]*?~]/.test(word)) return false;
  const base = word.slice(word.lastIndexOf('/') + 1);
  if (base === '' || base === '.' || base === '..') return false;
  return word.includes('/') || /\.[A-Za-z0-9_]{1,8}$/.test(base);
}

// Файловые операнды ОДНОЙ простой команды, разобранной в токены.
function readOperandsOf(tokens, cfg) {
  const inputs = [];
  const plain = [];
  let depth = 0;
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token && typeof token === 'object') {
      if (token.op === '(') depth += 1;
      else if (token.op === ')') depth = Math.max(0, depth - 1);
      // Вход из файла — чтение. Выход и дескрипторы целями чтения не являются, и
      // цель перенаправления забирается вместе с оператором.
      else if (token.op === '<' || token.op === '>' || token.op === '>>') {
        if (token.op === '<' && depth === 0 && typeof tokens[i + 1] === 'string'
            && !tokens[i + 1].includes(UNRESOLVED)) {
          inputs.push(tokens[i + 1]);
        }
        i += 1;
      }
      continue;
    }
    // Содержимое подстановки — операнд ЧУЖОЙ команды, а не этой.
    if (depth > 0 || token === '$') continue;
    plain.push(token);
  }

  const list = dropWrappers(dropEnvPrefix(plain), cfg);
  const name = list[0] || '';
  const all = FILE_OPERANDS.has(name);
  if (!all && !PATTERN_THEN_FILES.has(name)) return inputs;
  if (RECURSIVE_CAPABLE.has(name) && list.some((word) => RECURSIVE_FLAG.test(word))) return inputs;
  const valued = new Set(FLAG_TAKES_VALUE.get(name) || []);
  const out = [...inputs];
  let skip = !all;
  for (let i = 1; i < list.length; i += 1) {
    const word = list[i];
    if (word.startsWith('-')) {
      if (PATTERN_FLAGS.has(word)) skip = false;
      if (valued.has(word)) i += 1;
      continue;
    }
    // Аргумент с плюсом — тоже ключ, просто в старой форме: у просмотрщиков это
    // строка поиска или номер строки (`more +/образец`, `tail +5`), а не файл.
    if (word.startsWith('+')) continue;
    if (skip) { skip = false; continue; }
    if (word.includes(UNRESOLVED)) continue;
    if (looksLikePath(word)) out.push(word);
  }
  return out;
}

// Слова команды: только строки-токены, без операторов. Отдаётся наружу, потому
// что тем же вопросом «какие тут слова на самом деле» задаётся адаптер базы
// заметок: ключ внутри закавыченного тела заметки ключом не является, а
// обезвреживание кавычек их содержимое СОХРАНЯЕТ и от этого не спасает.
export function commandWords(cmd) {
  try {
    return parse(String(cmd || ''), () => UNRESOLVED).filter((token) => typeof token === 'string');
  } catch {
    return [];
  }
}

// Несёт ли слово метку нераскрытой переменной. Спрашивает адаптер базы заметок:
// адресом блока такая метка не является так же, как не является путём.
export const isUnresolved = (word) => String(word || '').includes(UNRESOLVED);

// commandReads(текст) → {reads, mutates, targets}.
//   reads   — доказано ли, что команда только читает;
//   mutates — доказано ли ОБРАТНОЕ: разбор знает, что она меняет состояние. Это
//             третий ответ, а не отрицание первого: «не читает» и «не разобрали»
//             для леджера разные вещи, и выдать второе за первое значит
//             промолчать про запись, которую разбор установил;
//   targets — что именно прочитано, насколько это разобрано.
export function commandReads(cmd) {
  const text = String(cmd || '');
  // Пустая команда чтением не является: classifyCommand зовёт её читающей (ей
  // нечего запрещать), но строки журнала за ней не стоит.
  if (text.trim() === '') return { reads: false, mutates: false, targets: [] };
  const cfg = rules();
  if (!cfg) return { reads: false, mutates: false, targets: [] };
  const answer = classifyCommand(text);
  if (answer.readOnly !== true) {
    return { reads: false, mutates: answer.cause === 'mutates', targets: [] };
  }

  // Разбор по КУСКАМ с тем же резолвом каталога, что у целей записи: `cd /repo &&
  // cat a.js` читает файл репозитория, а не текущего каталога.
  // Тело heredoc, here-string и подстановка процесса словами команды НЕ
  // являются, а токенизатор их от операндов не отделяет: `cat <<EOF` со строкой
  // «смотри README.md» внутри называл README.md прочитанным файлом. Отделить
  // тело от операндов надёжно нечем, поэтому у такой команды целей не называем
  // вовсе — промах безопаснее лжи. Агент печатает текст через heredoc постоянно,
  // так что вход этот не экзотический.
  if (/<<|<\(|>\(/.test(text)) return { reads: true, mutates: false, targets: [] };

  // Строки — самостоятельные команды: без этого операнды второй строки уезжали
  // в файловый слот ПЕРВОЙ, и правка на месте отмывалась в чтение переписанного
  // файла. Токены собираются построчно и склеиваются как звенья одной цепочки.
  let tokens;
  try {
    tokens = commandLines(text).flatMap((line, at) => (at === 0
      ? parse(line, () => UNRESOLVED)
      : [{ op: ';' }, ...parse(line, () => UNRESOLVED)]));
  } catch {
    return { reads: true, mutates: false, targets: [] };
  }
  // Подоболочка меняет каталог ТОЛЬКО внутри себя, и уследить за её границами по
  // звеньям нечем: `(cd /tmp) && cat a.js` читает `./a.js`, а не `/tmp/a.js`.
  // Скобки в команде — отслеживание каталога выключено целиком.
  const subshell = tokens.some((t) => t && typeof t === 'object' && (t.op === '(' || t.op === ')'));
  // Звенья с РАЗДЕЛИТЕЛЕМ, которым отделено следующее: переход каталога в звене
  // пайпа на соседа не влияет — пайп запускает звено в подоболочке, как и скобки.
  const pieces = [];
  let current = [];
  let sep = '';
  for (const token of tokens) {
    if (token && typeof token === 'object' && CHAIN_OPS.has(token.op)) {
      pieces.push({ sep, tokens: current });
      sep = token.op;
      current = [];
      continue;
    }
    current.push(token);
  }
  pieces.push({ sep, tokens: current });

  const targets = [];
  let cwd = '';
  for (const [at, piece] of pieces.entries()) {
    for (const t of readOperandsOf(piece.tokens, cfg)) targets.push(resolveTarget(cwd, t));
    if (subshell) continue;
    const nextSep = (pieces[at + 1] || {}).sep || '';
    if (nextSep === '|' || nextSep === '|&' || nextSep === '&') continue;
    // `cd` считается переходом только ПЕРВЫМ словом звена: словом-образцом он
    // бывает чаще (`grep -n cd файл`), и приняв его за переход, разбор резолвил
    // бы им остаток цепочки и выдал путь, которого никто не открывал.
    cwd = nextCwd(cwd, piece.tokens.filter((w) => typeof w === 'string'));
  }
  return { reads: true, mutates: false, targets: targets.map(cleanTarget).filter(Boolean) };
}
