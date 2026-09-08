import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
// Разбор Bash-команды: что она пишет, доказано ли, что она только читает, и что
// она прочитала. Своего разбора синтаксиса здесь БОЛЬШЕ НЕТ — дерево команды
// приходит от пакета-адаптера `command-tree-shell` (возможность `command_tree`),
// и всё ниже считается ПО ДЕРЕВУ. Реализация разбора одна на слой: вторая,
// написанная на другом языке, разъехалась бы с первой — а разъезд тут стоит либо
// пропущенной правки, либо ложного отказа.
//
// commandTargets(текст) → пути записи, очищенные и без пустых: главный вход.
// bashWriteTargets(текст) → те же цели как найдены; cleanTarget очищает одну.
// classifyCommand(текст) → {readOnly, cause, offender} — доказано ли чтение.
// commandReads(текст) → {reads, mutates, targets} — что прочитано.
// commandWords(текст) → слова команды; isUnresolved(слово) — несёт ли слово
//   метку нераскрытой переменной.
//
// НЕТ РАЗБОРА — НЕТ ОТВЕТА. Адаптер не найден, `python3` не запустился, строка не
// разобралась — ответ явный: цели пусты, чтение НЕ доказано, и о причине пишется
// в служебный вывод. Резки регулярками взамен нет (решение 14): догадка на месте
// разбора и есть тот фолбэк, ради устранения которого адаптер заведён.

// --- дерево команды ------------------------------------------------------------
//
// Возможность, которой этот файл закрыт. Имя ВОЗМОЖНОСТИ, а не пакета: какой
// пакет её закрывает, решает установка, а не этот файл.
const CAPABILITY = 'command_tree';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// Разбор не должен вешать событие: команда бывает длинной, но не бесконечной.
// Срок стоит и снаружи, и внутри питона — снаружи он ловит зависший процесс,
// внутри тот ловит зависший разбор.
const PARSE_TIMEOUT_MS = 10000;

// Реализация возможности ищется по ИЗВЕСТНЫМ КОРНЯМ, ровно как её ищет обёртка
// пакета (runtime/pylib/decision.py): список корней от установки, манифест
// источника в каждом корне, возможность из имени и `for`. Свой корень — первым:
// перенесённый чекаут продолжает работать и без списка.
//
// Жёсткий путь к пакету здесь стоять не может: адаптер, положенный позже в
// известный корень, обязан работать со следующего события, а имя пакета — не
// наше дело. Переопределяется переменной — ею кейсы показывают слою пустое
// окружение.
function adapterPath() {
  if (process.env.COMMAND_TREE_ADAPTER) return process.env.COMMAND_TREE_ADAPTER;
  for (const root of sourceRoots()) {
    for (const manifest of sourceIndex(root)) {
      if (capabilityOf(manifest) !== CAPABILITY) continue;
      const file = path.join(root, 'modules', String(manifest.name || ''),
        'scripts', 'adapters', 'adapter.py');
      if (fs.existsSync(file)) return file;
    }
  }
  return '';
}

// Возможность пакета — из его имени и `for`: у адаптера снимается хвост
// инструмента, дефисы становятся подчёркиваниями. Формула одна с pylib.
function capabilityOf(manifest) {
  let base = String(manifest.name || '');
  const forValue = String(manifest.for || '');
  const tail = forValue.includes(':') ? forValue.slice(forValue.indexOf(':') + 1) : '';
  if (tail && base.endsWith(`-${tail}`)) base = base.slice(0, -tail.length - 1);
  return base.split('-').join('_');
}

function sourceRoots() {
  const own = path.join(HERE, '..', '..', '..');
  const roots = [own];
  const share = process.env.XDG_DATA_HOME
    || path.join(process.env.HOME || '', '.local', 'share');
  try {
    for (const line of fs.readFileSync(path.join(share, 'jarvis', 'sources.list'), 'utf8').split('\n')) {
      const root = line.trim();
      if (root && !roots.includes(root)) roots.push(root);
    }
  } catch {
    // Списка нет — законная пустота: свой корень уже в списке, а чужих у этой
    // машины просто не заведено.
  }
  return roots;
}

// Что лежит в корне. Спрашивается ДВАЖДЫ и в этом порядке: сперва манифест
// источника, который кладёт установка, потом сами манифесты пакетов.
//
// Второй источник не запасной путь и не догадка: это тот же вопрос «какие тут
// пакеты», заданный дереву напрямую. Он нужен потому, что манифест источника
// пишет установка, а слой обязан работать и в свежем чекауте — иначе разбор
// команд пропадал бы ровно там, где систему и разрабатывают.
function sourceIndex(root) {
  const listed = readIndex(path.join(root, 'modules.index.json'));
  return listed.length ? listed : readManifests(path.join(root, 'modules'));
}

function readIndex(file) {
  try {
    const index = JSON.parse(fs.readFileSync(file, 'utf8'));
    return Array.isArray(index.modules) ? index.modules : [];
  } catch {
    // Манифеста источника нет или он испорчен: спросим сами пакеты.
    return [];
  }
}

// Имя и `for` из фронтматтера каждого пакета — ровно те два поля, по которым
// выводится возможность. Остальной манифест читает установщик; здесь его разбор
// был бы второй копией того, что уже есть в tools/jarvis.py.
function readManifests(dir) {
  const out = [];
  let names;
  try {
    names = fs.readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of names) {
    let text;
    try {
      text = fs.readFileSync(path.join(dir, name, 'SKILL.md'), 'utf8');
    } catch {
      continue;
    }
    if (!text.startsWith('---')) continue;
    const end = text.indexOf('\n---', 3);
    const front = end < 0 ? '' : text.slice(3, end);
    const field = (key) => (front.match(new RegExp(`^${key}:\\s*(\\S+)\\s*$`, 'm')) || [])[1] || '';
    out.push({ name: field('name'), for: field('for') });
  }
  return out;
}

// Коды перенаправлений оболочки (их отдаёт разбор числами). Запись в файл — вот
// эти; дублирование дескрипторов (`2>&1`, `<&3`) записью не является.
const WRITE_OPS = new Set([54, 55, 60, 64, 65]);
const READ_OP = 56;
const HEREDOC_OPS = new Set([61, 62, 63]);

// Одно событие — один процесс, и на команду в нём смотрят четверо: гейт, гвард
// якоря, метрики и журнал. Разбор поэтому считается ОДИН раз на текст.
const trees = new Map();

let complained = false;

function tree(command) {
  const text = String(command ?? '');
  if (trees.has(text)) return trees.get(text);
  const answer = ask(text);
  trees.set(text, answer);
  return answer;
}

// Дерево или null. Null значит «разбора нет», и это ОТВЕТ: молчаливое «команда
// ничего не пишет» соврало бы про каждый ход, где работали шеллом.
function ask(text) {
  const adapter = adapterPath();
  if (!adapter) return complain(`реализации возможности ${CAPABILITY} нет ни в одном известном корне`);
  const done = spawnSync('python3', [adapter], {
    input: JSON.stringify({ event: {}, args: { command: text } }),
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
    timeout: PARSE_TIMEOUT_MS,
  });
  if (done.error || done.status !== 0) return complain(`разбор не запустился: ${done.error || done.status}`);
  let answer;
  try {
    answer = JSON.parse(done.stdout);
  } catch {
    return complain('разбор вернул не JSON');
  }
  if (answer && answer.unsupported) return complain(String(answer.unsupported));
  return answer && Array.isArray(answer.statements) ? answer : complain('разбор вернул не дерево');
}

// Причина называется вслух ОДИН раз на процесс: без строки в служебном выводе
// пропавший разбор выглядит как «гвард придирается», а сто строк подряд на
// каждую команду хода прячут её сами.
function complain(why) {
  if (!complained) {
    complained = true;
  process.stderr.write(`[write-targets-bash] unsupported: ${CAPABILITY} (${why})\n`);
  }
  return null;
}

const texts = (statement) => (statement.words || []).map((w) => w.text);

// --- переход каталога ----------------------------------------------------------

// Переход каталога по словам ОДНОГО утверждения. Правило одно на обе стороны
// файла — на цели записи и на цели чтения, — потому что синтаксис у них один.
//
// `cd` считается переходом, только если он ПЕРВОЕ слово утверждения. Свободный
// поиск слова `cd` где угодно давал обход гвардов: у `grep -n cd /tmp/a.txt &&
// cat > README.md` цель записи уезжала под `/tmp/`, объявлялась эфемерной, и
// правка рабочего файла проходила мимо план-гейта и гварда якоря сессии.
//
// Неабсолютный путь СБРАСЫВАЕТ каталог, а не оставляет прежний: переход
// состоялся, а куда — неизвестно, и держаться за старый значит приклеивать его к
// чужим путям. Нераскрытая переменная в самом каталоге — такая же выдумка.
export function nextCwd(current, words) {
  if (words[0] !== 'cd') return current;
  const to = words[1];
  if (typeof to !== 'string' || !to.startsWith('/') || isUnresolved(to)) return '';
  return to.replace(/\/$/, '');
}

// Переход по САМОМУ утверждению: нераскрытость видна по слову дерева, а не по
// метке внутри его текста.
function cwdAfter(current, statement) {
  if (texts(statement)[0] !== 'cd') return current;
  const to = (statement.words || [])[1];
  if (!to || to.expanded || !to.text.startsWith('/')) return '';
  return to.text.replace(/\/$/, '');
}

// Каталог ПОСЛЕ утверждения — одно правило на обе стороны файла.
//
// Переход НА ГЛУБИНЕ каталог не уточняет, а ОБЕСЦЕНИВАЕТ. Фигурные скобки и
// ветка условия исполняются в ТЕКУЩЕЙ оболочке, и переход из них действует
// дальше; подоболочка и подстановка — нет. Отличить одно от другого по дереву
// нечем, а ошибки этих двух сторон не равны: удержанный чужой каталог даёт
// АБСОЛЮТНЫЙ путь, который легко оказывается временным, и правка репозитория
// прошла бы мимо гейта. Поэтому вложенный переход означает «каталог
// неизвестен»: цель остаётся относительной, то есть скорее долговечной, и под
// гейт попадает.
function nextDirectory(cwd, statement) {
  if (texts(statement)[0] !== 'cd') return cwd;
  return statement.depth === 0 ? cwdAfter(cwd, statement) : '';
}

// --- цели записи ---------------------------------------------------------------

// Цели записи: перенаправление, tee, правка на месте (-i), cp/mv (последний
// аргумент либо явная цель после -t), запись из интерпретатора и то же самое
// ВНУТРИ обёртки запуска (`bash -c "…"`). Гейтится цель, а не команда: сборка,
// копирование в игнорируемый путь и любое чтение целей не дают.
export function bashWriteTargets(cmd) {
  return targetsOf(cmd).concat(wrapperTargets(cmd));
}

// Цели БЕЗ спуска в обёртку запуска. Отдельной функцией она стоит ровно затем,
// чтобы спуск был РОВНО ОДИН уровень: тело обёртки разбирается этим же разбором,
// а он внутрь второй обёртки уже не идёт.
function targetsOf(cmd) {
  const parsed = tree(cmd);
  if (!parsed) return [];
  const cfg = rules();
  const targets = [];
  let cwd = '';
  for (const statement of parsed.statements) {
    for (const t of statementTargets(statement, cfg)) targets.push(resolveTarget(cwd, t));
    cwd = nextDirectory(cwd, statement);
  }
  return targets.concat(interpreterTargets(parsed.source || ''));
}

// Ключи правки на месте. Сверка по НАЧАЛУ слова: `sed -i.bak` и
// `--in-place=.bak` — та же правка, что `sed -i`, и точное равенство их
// пропускало.
const IN_PLACE = ['-i', '--in-place'];

// Цели одного утверждения. Имя команды берётся после снятия присваиваний
// окружения и запускающих обёрток — тем же способом, что у доказательства
// чтения: два разных ответа на вопрос «какая это команда» разъехались бы.
function statementTargets(statement, cfg) {
  const targets = [];
  for (const redirect of statement.redirects || []) {
    if (WRITE_OPS.has(redirect.op)) targets.push(targetText(redirect.target));
  }
  if (!cfg) return targets;
  const list = dropWrappers(dropEnvPrefix(texts(statement)), cfg);
  const name = list[0] || '';
  const operands = list.slice(1).filter((w) => !w.startsWith('-'));
  if (name === 'tee') targets.push(...operands);
  if ((name === 'sed' || name === 'perl') && list.slice(1).some((w) => usesFlag(w, IN_PLACE))) {
    // Здесь цель не отличить от программы правки: `s/a/b/` выглядит путём так
    // же, как `README.md`. Названы обе — лишняя цель гейта не открывает, а
    // пропущенная пропускает правку файла мимо него.
    for (const word of list.slice(1)) if (/[/.]/.test(word)) targets.push(word);
  }
  if (name === 'cp' || name === 'mv') {
    const at = list.indexOf('-t');
    if (at > 0 && list[at + 1]) targets.push(list[at + 1]);
    else if (operands.length) targets.push(operands[operands.length - 1]);
  }
  return targets;
}

// Текст цели записи. Слово с ПОДСТАНОВКОЙ несёт метку: раскрыть её нечем, и
// `cat > $HOME/notes.md` дал бы целью `/notes.md` — путь, которого не
// существует. Отбросить такую цель тоже нельзя: пустой список целей у гейта
// значит «команда ничего не пишет», и настоящая запись прошла бы мимо. Метка
// держит оба конца: цель остаётся в списке и под гейтом, а прочитать её как
// настоящий путь уже невозможно. Пустой остаток метки не получает — там и
// литерала нет, называть нечего.
function targetText(word) {
  if (!word.expanded || word.text === '') return word.text;
  return word.text + UNRESOLVED;
}

// Тела обёрток запуска: `bash -c "…"`, `sh -c '…'` и остальные оболочки словаря.
// Тело приходит от разбора ОДНИМ словом, поэтому спуск в него — это повторный
// вызов того же разбора, и другого способа тут не нужно.
//
// На стороне ЧТЕНИЯ обёртки целей по-прежнему не дают, и это не забывчивость:
// потерянная цель чтения — лишний отказ гварда, потерянная цель записи —
// пропущенная правка. Стороны не симметричны, и строгость нужна только на одной.
function wrapperTargets(cmd) {
  const parsed = tree(cmd);
  const cfg = rules();
  if (!parsed || !cfg) return [];
  const out = [];
  for (const statement of parsed.statements) {
    const body = wrapperBodyOf(statement, cfg);
    if (!body) continue;
    // Нераскрытая переменная отбрасывает ОДНУ цель, а не всё тело: у
    // `bash -c "$CMD > README.md"` цель перенаправления буквальная, и выбросив
    // тело целиком, разбор терял настоящую запись.
    for (const t of targetsOf(body)) if (t) out.push(t);
  }
  return out;
}

// Ключи обёртки, у которых СВОЁ значение: `-o pipefail`, `-O extglob`,
// `--rcfile файл`. Без них поиск ключа `-c` обрывался на значении, и
// `bash -O extglob -c "…"` проходил молча. Короткий ключ сверяется КЛАСТЕРОМ:
// `bash -euo pipefail` — обычная форма, и точное равенство её пропускало.
const WRAPPER_VALUE_FLAG = /^[-+][A-Za-z]*[oO]$/;
const WRAPPER_VALUE_LONG = new Set(['--rcfile', '--init-file']);
const wrapperName = (word) => String(word || '').replace(/^.*\//, '').toLowerCase();

// Тело обёртки одного утверждения, если это утверждение — обёртка.
//
// После имени идут только КЛЮЧИ обёртки. Первое слово, не похожее на ключ и не
// являющееся значением ключа, поиск обрывает: в `bash script.sh -c конфиг.yml`
// ключ принадлежит скрипту, и назвать конфиг целью записи значило бы завести
// ложный отказ на обычной форме запуска.
function wrapperBodyOf(statement, cfg) {
  const list = dropWrappers(dropEnvPrefix(texts(statement)), cfg);
  if (!list.length || !cfg.shellWrappers.includes(wrapperName(list[0]))) return '';
  for (let i = 1; i < list.length; i += 1) {
    const flag = list[i];
    if (!flag.startsWith('-') && !flag.startsWith('+')) return '';
    if (/^-[A-Za-z]*c$/.test(flag)) return list[i + 1] || '';
    if (WRAPPER_VALUE_LONG.has(flag) || WRAPPER_VALUE_FLAG.test(flag)) i += 1;
  }
  return '';
}

// Запись из интерпретатора: она живёт ВНУТРИ строки-программы, деревом оболочки
// не описана и описана быть не может — это чужой язык. Ищется поэтому по
// исходному тексту и каталогом перехода не резолвится: интерпретатор
// запускается со своим рабочим каталогом, и угадывать его разбор не берётся.
function interpreterTargets(source) {
  const targets = [];
  for (const line of source.split('\n')) {
    for (const m of line.matchAll(/open\([ \t]*['"]([^'"]+)['"][ \t]*,[ \t]*['"][wa]/g)) targets.push(m[1]);
    for (const m of line.matchAll(/Path\([ \t]*['"]([^'"]+)['"][ \t]*\)[ \t]*\.[ \t]*write_(?:text|bytes)/g)) targets.push(m[1]);
  }
  return targets;
}

// Цель записи, приведённая к настоящему пути. Команда часто переходит в каталог
// и пишет уже относительным именем: «cd /tmp/work && cat > notes.md». Цель,
// взятая как написана, начинается не с /tmp — и запись во временный каталог
// гейтилась, хотя та же запись абсолютным путём проходила свободно.
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

// Цели записи команды, очищенные, без пустых и БЕЗ ПОВТОРОВ. Повтор возможен
// там, где одну и ту же цель видят два разбора: `cat a | bash -c "tee /repo/o"`
// — снаружи по слову `tee`, внутри обёртки по нему же. Гвардам повтор
// безразличен, а журнал это ЛЕДЖЕР: дважды названный файл сборка изменений
// посчитала бы дважды.
export function commandTargets(cmd) {
  return [...new Set(bashWriteTargets(cmd).map(cleanTarget).filter(Boolean))];
}

// --- доказательство «команда только читает» -----------------------------------
//
// Не «что она пишет», а «доказано ли, что она НИЧЕГО не пишет». Прежний гвард
// спрашивал первое и искал цели по списку шаблонов записи. Такой список
// разрешает по умолчанию: чего в нём нет — проходит, а своя команда записи есть
// у любого стороннего инструмента. Здесь вопрос обратный, и умолчание обратное:
// недоказанное закрыто.
//
// ЧТО СЧИТАЕТСЯ ДОКАЗАТЕЛЬСТВОМ — данные в vendor/read-only-rules.json. Там
// отобранные под этот предикат команды, их запрещённые флаги и read-only
// подкоманды инструментов. Имена инструментов лежат В ДАННЫХ, и гвард имён
// (tests/unit/no-tool-names.test.mjs) их не считает: он читает только `.js`.
// Значит перенос имён из кода в этот словарь гвард не заметит — это названо в
// его шапке и остаётся так намеренно.
//
// ЧЕМУ НАУЧИЛИ ЧУЖИЕ ПОЛОМКИ:
//   — проверяется КАЖДОЕ утверждение, а не первое: приписка через && обходила
//     гварды, смотревшие только на начало строки;
//   — имя, заданное путём, доказательством не считается: подложенный рядом
//     бинарник с именем известной утилиты обходил сверку по имени файла;
//   — обёртки снимаются: запуск через оболочку и ограничитель времени прятали
//     внутреннюю команду;
//   — содержимое подстановки судится как отдельная команда: запрещать её целиком
//     нельзя — соседние гварды сами предписывают рецепты с подстановкой.
//
// НЕИЗВЕСТНОСТЬ — НЕ РАЗРЕШЕНИЕ. Нет разбора, не читаются правила, встретилась
// незнакомая конструкция — вердикт «не доказано», а не проход.

const RULES_PATH = process.env.READ_ONLY_RULES
  || path.join(HERE, 'vendor', 'read-only-rules.json');

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

// Перенаправление в файл — запись; отвод в пустое устройство и дескрипторы —
// нет, иначе половина обычных читающих вызовов получала бы отказ.
function redirectsToFile(statement, cfg) {
  return (statement.redirects || []).some((redirect) => WRITE_OPS.has(redirect.op)
    && !cfg.nullSinks.includes(redirect.target.text));
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
// якоря.
function usesFlag(word, denied) {
  return denied.some((flag) => word === flag
    || word.startsWith(`${flag}=`)
    // Короткий ключ со СЛИПШИМСЯ значением (`-i.bak`). У длинных так не бывает.
    || (!flag.startsWith('--') && flag.length === 2 && word.startsWith(flag) && word.length > 2));
}

// Одно утверждение: доказано чтение или нет.
function judgeStatement(statement, cfg, depth) {
  if (redirectsToFile(statement, cfg)) {
    return verdict(false, 'mutates', texts(statement)[0] || 'команда');
  }
  // Не вызов команды (`[[ … ]]`, арифметика, объявление): слов у него нет, и
  // судить нечего — доказательством чтения это не является.
  if (statement.kind !== 'call') return verdict(false, 'unproven', 'конструкция оболочки');

  const list = dropWrappers(dropEnvPrefix(texts(statement)), cfg);
  const name = list[0];
  if (!name) return verdict(true, null, null);

  // Обёртка запуска: судится ТЕЛО, и ровно один уровень вглубь. Разобрать
  // вложенную обёртку нечем, и назвать её содержимое доказанным значило бы
  // выдумать.
  if (depth < 1 && cfg.shellWrappers.includes(wrapperName(name))) {
    const body = wrapperBodyOf(statement, cfg);
    if (body) return judgeTree(body, cfg, depth + 1);
  }

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

// Все утверждения команды. Достаточно ОДНОГО недоказанного, чтобы весь вызов
// перестал быть доказанно читающим: содержимое подстановки, тело цикла и вторая
// строка — тоже утверждения, и приписка через любое из них обходила бы гвард
// ровно как приписка через `&&`.
function judgeTree(cmd, cfg, depth) {
  const parsed = tree(cmd);
  if (!parsed) return verdict(false, 'unparsed', firstWord(cmd));
  for (const statement of parsed.statements) {
    const answer = judgeStatement(statement, cfg, depth);
    if (!answer.readOnly) return answer;
  }
  return verdict(true, null, null);
}

const firstWord = (cmd) => String(cmd || '').trim().split(/\s+/)[0] || 'команда';

// classifyCommand(строка) → { readOnly, cause, offender }.
//   cause: 'mutates'   — команда меняет состояние, ждать её незачем;
//          'unproven'  — про неё не видно, что она только читает;
//          'unparsed'  — строка не разобралась, судить нечем.
export function classifyCommand(command) {
  const cfg = rules();
  if (!cfg) return verdict(false, 'unproven', 'правила недоступны');
  if (typeof command !== 'string' || command.trim() === '') return verdict(true, null, null);
  return judgeTree(command, cfg, 0);
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
// ЧТО СЧИТАЕТСЯ ФАЙЛОВЫМ ОПЕРАНДОМ — знание про КАЖДУЮ команду, а не общее
// правило «слово похоже на путь». Асимметрия здесь жёсткая: пропущенная цель
// стоит агенту лишнего чтения, а ЛОЖНАЯ разрешает править то, чего никто не
// читал. Поэтому три сита подряд — список команд, таблица ключей со значением и
// вид пути, — и умолчание «целей нет».

// У этих команд ВСЕ неключевые операнды суть файлы: `cat a b`, `head -20 a.js`.
// Каждое имя здесь ДОСТИЖИМО: цели спрашиваются только у команды, про которую
// словарь читаемости уже доказал, что она ничего не пишет. Список, обещающий
// больше, чем делает, врёт своему читателю, поэтому достижимость каждого имени
// держит юнит. Экспортируется РАДИ ЭТОЙ ПРОВЕРКИ: юнит, перечисляющий имена
// своим списком, зеленел бы и с вернувшимся мёртвым именем.
export const FILE_OPERANDS = new Set([
  'cat', 'head', 'tail', 'less', 'more', 'bat', 'nl', 'wc', 'od', 'xxd',
  'strings', 'file', 'cksum', 'md5sum', 'sha256sum',
  'diff', 'cmp',
]);
// `stat` в этот список НЕ входит: он читает метаданные, а не содержимое, и по
// смыслу гварда «не правь того, чего не читал» назвать его файл прочитанным
// значило бы разрешить правку того, чего агент не открывал. `file` оставлен: он
// действительно читает начало файла.

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

// Метка нераскрытой переменной. Разбор подстановку НЕ РАСКРЫВАЕТ и текста ей не
// даёт: `cat $HOME/секрет.md` дало бы целью `/секрет.md` — путь, которого не
// существует, вместо настоящего прочитанного файла. Слово с меткой целью не
// становится. Метка живёт в СЛОВАХ, отдаваемых наружу (commandWords): у
// соседнего адаптера свой вопрос к тем же словам, и отличать нераскрытое ему
// нужно так же. Литерал в одинарных кавычках переменной не является и метки не
// получает.
const UNRESOLVED = ' нераскрыто ';

// Похоже ли слово на ПУТЬ К ФАЙЛУ. Третье сито: числа (значения ключей), куски
// образца, маркеры heredoc и прочее целями не становятся. Каталог целью чтения
// тоже не является — рекурсивный поиск читает файлы под ним, а назвать их нечем,
// и выдать каталог за прочитанный файл значило бы соврать.
function looksLikePath(word) {
  if (typeof word !== 'string' || word === '' || word.startsWith('-')) return false;
  if (word.endsWith('/')) return false;
  // Символы подстановки и тильду разбор не раскрывает, а мы раскрыть не можем:
  // записать их прочитанным путём значило бы назвать файл, которого нет.
  if (/[{}[\]*?~]/.test(word)) return false;
  const base = word.slice(word.lastIndexOf('/') + 1);
  if (base === '' || base === '.' || base === '..') return false;
  return word.includes('/') || /\.[A-Za-z0-9_]{1,8}$/.test(base);
}

// Файловые операнды ОДНОГО утверждения.
function readOperandsOf(statement, cfg) {
  const inputs = [];
  for (const redirect of statement.redirects || []) {
    // Вход из файла — чтение. Выход и дескрипторы целями чтения не являются.
    if (redirect.op === READ_OP && !redirect.target.expanded) inputs.push(redirect.target.text);
  }
  const all = statement.words || [];
  const list = dropWrappers(dropEnvPrefix(texts(statement)), cfg);
  const name = list[0] || '';
  // Те же слова, что и в списке имён, но объектами: нераскрытость видна по
  // слову дерева, а не по метке внутри его текста.
  const words = all.slice(all.length - list.length);
  const everyOperand = FILE_OPERANDS.has(name);
  if (!everyOperand && !PATTERN_THEN_FILES.has(name)) return inputs;
  if (RECURSIVE_CAPABLE.has(name) && list.some((word) => RECURSIVE_FLAG.test(word))) return inputs;
  const valued = new Set(FLAG_TAKES_VALUE.get(name) || []);
  const out = [...inputs];
  let skip = !everyOperand;
  for (let i = 1; i < words.length; i += 1) {
    const word = words[i].text;
    if (word.startsWith('-')) {
      if (PATTERN_FLAGS.has(word)) skip = false;
      if (valued.has(word)) i += 1;
      continue;
    }
    // Аргумент с плюсом — тоже ключ, просто в старой форме: у просмотрщиков это
    // строка поиска или номер строки (`more +/образец`, `tail +5`), а не файл.
    if (word.startsWith('+')) continue;
    if (skip) { skip = false; continue; }
    if (words[i].expanded) continue;
    if (looksLikePath(word)) out.push(word);
  }
  return out;
}

// Слова команды: только слова, без операторов и целей перенаправления.
// Отдаётся наружу, потому что тем же вопросом «какие тут слова на самом деле»
// задаётся адаптер базы заметок: ключ внутри закавыченного тела заметки ключом
// не является, а разбор оставляет тело ОДНИМ словом. Нераскрытое несёт метку:
// адресом блока такое слово является не больше, чем путём.
export function commandWords(cmd) {
  const parsed = tree(cmd);
  if (!parsed) return [];
  const out = [];
  for (const statement of parsed.statements) {
    for (const word of statement.words || []) out.push(word.expanded ? word.text + UNRESOLVED : word.text);
  }
  return out;
}

// Чего не хватило, чтобы ответить про эту команду: имя ВОЗМОЖНОСТИ либо пустота.
//
// Спрашивают это гварды, которые зовут цели напрямую. Пустой список целей и
// «команда ничего не пишет» — с виду одно и то же, и без этого вопроса пропавший
// разбор читался бы как разрешение на любую запись шеллом. Неизвестность
// разрешением не является, и называется она ИМЕНЕМ (решение 14).
export function commandTreeGap(cmd) {
  return tree(cmd) === null ? CAPABILITY : '';
}

// Слова КАЖДОГО утверждения по отдельности. Спрашивает адаптер git: вызов
// инструмента — это утверждение целиком, а не текст между разделителями, и
// «printf 'git push'» вызовом не является именно потому, что тело кавычек
// осталось одним словом.
export function commandStatements(cmd) {
  const parsed = tree(cmd);
  return parsed ? parsed.statements.map(texts) : [];
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
  const parsed = tree(text);
  if (!parsed) return { reads: true, mutates: false, targets: [] };

  // Тело heredoc и here-string словами команды не являются, а КАНАЛ процессной
  // подстановки прочитанным файлом не является вовсе — и что именно из него
  // прочли, назвать нечем. У такой команды целей не называем: промах безопаснее
  // лжи, а печатать текст через heredoc агент не перестанет.
  const piped = parsed.statements.some((s) => (s.redirects || []).some((r) => HEREDOC_OPS.has(r.op))
    || (s.words || []).some((w) => w.process));
  if (piped) return { reads: true, mutates: false, targets: [] };

  // ЦЕЛИ берутся у утверждений верхнего уровня: содержимое подстановки —
  // операнды ЧУЖОЙ команды. А вот КАТАЛОГ ведут все, тем же правилом, что у
  // целей записи: переход внутри фигурных скобок действует и дальше, и держать
  // на нём прежний каталог значило бы назвать прочитанным файл, которого никто
  // не открывал.
  const all = parsed.statements;
  const targets = [];
  let cwd = '';
  for (const [at, statement] of all.entries()) {
    if (statement.depth === 0) {
      for (const t of readOperandsOf(statement, cfg)) targets.push(resolveTarget(cwd, t));
    }
    // Переход каталога в звене пайпа на соседа не влияет: пайп запускает звено в
    // подоболочке, как и скобки.
    const nextSep = (all[at + 1] || {}).sep || '';
    if (statement.depth === 0 && (nextSep === '|' || nextSep === '|&' || nextSep === '&')) continue;
    cwd = nextDirectory(cwd, statement);
  }
  return { reads: true, mutates: false, targets: targets.map(cleanTarget).filter(Boolean) };
}
