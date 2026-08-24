#!/usr/bin/env node
// PreToolUse plan-gate: рабочие правки — код, система, Craft — по умолчанию
// закрыты; открывает их ПЕРИМЕТР одобренного плана, а не факт одобрения.
//
// Маркер (universal-plan-gate-approve.sh) — список целей одобренного: строки
// «- где:» одобренных планов И семантические разрешения (строки «button:цель»,
// дописывает permissionGrant по вердикту классификатора над окном разрешений —
// парами «вопрос + ответ» и репликами-указаниями Влада). Гейт открывает только
// совпадение с целью; одобрения складываются, гасит их смена сессии — отзыв по
// просьбе Влада выполняется как обычная работа, магической фразы нет.
//
// Поверхности:
//   - Write|Edit|MultiEdit|NotebookEdit — правки файлов где угодно, кроме
//     эфемерного (планы, tmp/scratchpad, служебное ~/.claude) и игнорируемого
//     гитом ВНЕ .claude/ — внутри .claude/ живут игнорируемые, но системные
//     файлы (settings.local.json, кэш предодобренной зоны), их игнор-лазейка
//     открывала бы без плана;
//   - Bash — разбор команды на цели записи: перенаправление, tee, sed/perl -i,
//     cp/mv, запись из интерпретатора. Команда со смешанными целями проходит
//     только когда ВСЕ цели в периметре;
//   - craft_write — каждый UUID команды обязан быть в периметре; отдельно и
//     раньше периметра — предодобренная зона (exempt-scope, напр. «Продукты»).
//
// CRAFT_AUTONOMOUS=1 обходит гейт целиком — рутины и headless-евалы
// предавторизованы, интерактивного Влада там нет.
//
// Защита от протечки привязана к источнику пути маркера: путь, выведенный из
// ПУСТОГО session-id (общий default), не читается и не пишется; путь из
// env-переопределения используется всегда — тесты герметичны через него.
//
// Непокрыто и названо честно: неопознанная конструкция записи; пакетные
// менеджеры и операции гита над рабочим деревом (пишут своей логикой, не
// перенаправлением); перенаправление в закавыченную цель — кавычки
// вычёркиваются, чтобы «больше» в сравнении не считалось записью.
//
// Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
import fs from 'node:fs';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { isIgnored } from './lib/git.js';
import {
  planGateMarker, approvedPlans, buttonPlans, permissionWindow, classifierDegraded,
  lastInputTrace, exemptScopeFile,
} from './lib/paths.js';
import { classify, classifierPath, classifierAvailable } from './lib/classifier.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const { raw, event, tool, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

// Явный клик Влада старше гейта: режимы acceptEdits и bypassPermissions он
// включает сам штатным переключателем — белый список, ровно два значения.
// auto в списке НЕТ намеренно (решение Влада): авто-режим — доверие харнесса,
// а не человека; все прочие значения и отсутствие поля оставляют гейт работать.
const mode = event.permission_mode || '';
if (mode === 'acceptEdits' || mode === 'bypassPermissions') process.exit(0);

// Отладочный след последнего входа (эфемерный): по нему проверяются факты о
// составе hook-входа (напр. поле permission_mode) без правки харнесса.
try {
  fs.writeFileSync(lastInputTrace('plan-gate'), raw);
} catch { /* след не записался — на решение гейта это не влияет */ }

// Craft-запись опознаётся по СУФФИКСУ имени, а не по полному: префикс MCP-сервера
// Craft меняется на переподключении (mcp__Craft__… в одной сессии, mcp__‹uuid›__…
// в следующей) — точное сравнение молча перестало бы гейтить на первой ротации.
const isCraftWrite = /__craft_write$/.test(tool);
const isFileEdit = !isCraftWrite && ['Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(tool);
const isBash = !isCraftWrite && tool === 'Bash';
if (!isCraftWrite && !isFileEdit && !isBash) process.exit(0);

const marker = planGateMarker();
let scopelist = '';
try {
  scopelist = fs.readFileSync(marker, 'utf8').replace(/\n+$/, '');
} catch { /* маркера нет — периметр пуст */ }

const PWD = process.env.PWD || process.cwd();

// Срез по БАЙТАМ с хвостовым переводом строки, как его делал bash: подстановка
// команды добавляла к тексту перевод строки, резала head -c и снимала хвостовые
// переводы обратно. Возвращается буфер — срез посреди многобайтного символа
// обязан дать те же байты, что давал шелл.
function headBytes(text, limit) {
  const cut = Buffer.from(`${text}\n`, 'utf8').subarray(0, limit);
  let end = cut.length;
  while (end > 0 && cut[end - 1] === 0x0a) end -= 1;
  return cut.subarray(0, end);
}

// inScope(цель) — цель входит в периметр: точное имя, вложенность в названный
// каталог, либо сам идентификатор строкой (Craft-UUID). Кнопочные строки
// «button:цель» матчатся наравне с плановыми; источник совпадения остаётся в
// matchSrc — цель, входящая в оба, считается плановой.
let matchSrc = '';
function inScope(target) {
  if (!scopelist) return false;
  let hitButton = false;
  for (const line of scopelist.split('\n')) {
    if (!line) continue;
    const isButton = line.startsWith('button:');
    const entry = isButton ? line.slice('button:'.length) : line;
    const dir = `${entry.replace(/\/$/, '')}/`;
    if (target === entry || target.startsWith(dir)) {
      if (!isButton) {
        matchSrc = 'plan';
        return true;
      }
      hitButton = true;
    }
  }
  if (hitButton) {
    matchSrc = 'button';
    return true;
  }
  return false;
}

// Плановая часть периметра — lock ветки времянок: кнопочные цели её не открывают.
function planScopeNonempty() {
  return scopelist.split('\n').some((line) => !line.startsWith('button:') && /\S/.test(line));
}

function denyScope(target) {
  deny(`Заблокировано план-гейтом: цель (${target}) не входит в одобренный план и не времянка. Пути дальше: дельта плана с этой целью, предметный вопрос-разрешение или прямое указание Влада, режим acceptEdits. Автономному прогону — CRAFT_AUTONOMOUS=1.`);
}

// --- Классификатор содержания (tools/plan-scope-classifier.sh) ---------------
// Путь-матч по периметру — грубый фильтр; точность даёт LLM-сверка содержания
// правки с планом. Вне периметра классификатор отвечает на вопрос «очевидная
// времянка?» — там он единственная защита, и его недоступность закрывает.
// В периметре недоступность деградирует к путь-матчу — мягко, но ВИДИМО:
// первый отказ за сессию оставляет файл-след рядом с маркером и строку в stderr,
// исправный и мёртвый классификатор обязаны различаться по признаку.
const classifier = classifierPath();

function classifierDegradedMark() {
  const trace = classifierDegraded();
  if (trace && !fs.existsSync(trace)) {
    try {
      fs.writeFileSync(trace, '');
    } catch { /* след не записался — предупреждение всё равно уходит в stderr */ }
    process.stderr.write('[plan-gate] классификатор недоступен — периметр живёт путь-матчем\n');
  }
}

function nonEmptyFile(file) {
  try {
    return fs.statSync(file).size > 0;
  } catch {
    return false;
  }
}

function tempFile(prefix) {
  const dir = process.env.TMPDIR || '/tmp';
  return path.join(dir, `${prefix}.${randomBytes(3).toString('hex')}`);
}

// scopeContentCheck(описание) → решение по правке в периметре.
// Сверка идёт против НАКОПИТЕЛЯ одобренных планов «‹маркер›.plans» (его пишет
// universal-plan-gate-approve.sh при каждом одобрении, окно последних 5), а не
// против живого файла плана: маркер держит цели всех одобрений сессии, и
// правка старой цели не должна сверяться с новейшей дельтой, а правка файла
// плана после одобрения не должна менять одобренное. Сверка со всеми планами
// сразу — принятое огрубление вместо адресной привязки «цель → её план».
// Накопителя нет (одобрение до этой правки) — видимая деградация к путь-матчу.
function scopeContentCheck(desc) {
  if (!classifierAvailable(classifier)) return classifierDegradedMark();
  const plans = approvedPlans();
  if (!nonEmptyFile(plans)) return classifierDegradedMark();
  const verdict = classify(classifier, 'match', [plans], desc);
  if (verdict.startsWith('NOMATCH:')) {
    deny(`Заблокировано план-гейтом: правка цели из периметра расходится с одобренным планом — ${verdict.slice('NOMATCH:'.length)}. Пути дальше: дельта плана, предметный вопрос-разрешение или прямое указание Влада, режим acceptEdits.`);
  }
  if (verdict === 'MATCH') return undefined;
  return classifierDegradedMark();
}

// permissionGrant(описание, цели…) — семантическое разрешение: правка вне
// периметра сверяется классификатором с окном разрешений «‹маркер›.qa-window»
// (пары «вопрос + ответ» кнопочного хука и реплики-указания Влада из
// reset-хука). Вердикт «разрешает» дописывает цели в периметр строками
// «button:цель» и окно-на-момент-разрешения в файл микро-планов
// «‹маркер›.button-plans» секциями «Цель: …» — предмет сверки содержания для
// повторных правок цели. Пустое окно модель не зовёт — мгновенный отказ ветки.
// Бюджет вызова уменьшен до 15 с (не env-дефолт 20): на отказе следом идёт
// ещё вызов времянки, и пара 15+20 укладывается в лимит PreToolUse-хука
// (дефолт 60 с) — его превышение убивает хук БЕЗ deny, то есть открыло бы
// гейт молча. Меньше нельзя: холодный вызов haiku живьём не влез в 10 с.
// «Не разрешает» и недоступность классификатора ветку не открывают.
function permissionGrant(desc, ...targets) {
  if (!marker) return false;
  const qa = permissionWindow();
  if (!nonEmptyFile(qa)) return false;
  if (!classifierAvailable(classifier)) return false;
  const verdict = classify(classifier, 'permission', [qa], desc, {
    timeoutSec: process.env.PLAN_CLASSIFIER_TIMEOUT || 15,
  });
  if (!verdict.startsWith('PERMIT')) return false;
  let window = '';
  try {
    window = fs.readFileSync(qa, 'utf8');
  } catch { /* окно исчезло между вердиктом и записью — секция останется пустой */ }
  for (const t of targets) {
    if (!t) continue;
    try {
      fs.appendFileSync(marker, `button:${t}\n`);
    } catch { /* периметр не пополнился — следующая правка спросит заново */ }
    try {
      fs.appendFileSync(buttonPlans(), `## Цель: ${t}\n${window}\n`);
    } catch { /* микро-плана нет — сверка содержания деградирует к путь-матчу */ }
  }
  return true;
}

// buttonContentCheck(описание, цель, отн.цель) — сверка правки разрешённой цели
// против секции её окна из файла микро-планов; секции нет — сверка невозможна:
// пропуск по путь-матчу с видимым следом деградации.
function buttonContentCheck(desc, target, rel) {
  const bp = buttonPlans();
  let text = '';
  try {
    text = fs.readFileSync(bp, 'utf8');
  } catch {
    return classifierDegradedMark();
  }
  if (!classifierAvailable(classifier)) return classifierDegradedMark();

  const heads = [`## Цель: ${target}`];
  if (rel) heads.push(`## Цель: ${rel}`);
  let keep = false;
  const section = [];
  for (const line of text.split('\n')) {
    if (heads.includes(line)) {
      keep = true;
      section.push(line);
      continue;
    }
    if (/^## Цель: /.test(line)) keep = false;
    if (keep) section.push(line);
  }
  if (section.length === 0) return classifierDegradedMark();

  const file = tempFile('btn-plan');
  try {
    fs.writeFileSync(file, `${section.join('\n')}\n`);
  } catch {
    return classifierDegradedMark();
  }
  const verdict = classify(classifier, 'match', [file], desc);
  try {
    fs.rmSync(file, { force: true });
  } catch { /* временный файл переживёт прогон, это не влияет на вердикт */ }
  if (verdict.startsWith('NOMATCH:')) {
    deny(`Заблокировано план-гейтом: правка разрешённой цели расходится с одобренным вопросом или указанием — ${verdict.slice('NOMATCH:'.length)}. Пути дальше: задай предметный вопрос-разрешение заново или покажи план.`);
  }
  return undefined;
}

// mixedContentCheck(описание) — смешанная команда: цели плана и разрешений
// сверяются против накопителя одобренных планов и файла микро-планов вместе;
// deny-текст плановый.
function mixedContentCheck(desc) {
  let combined = '';
  for (const file of [approvedPlans(), buttonPlans()]) {
    if (!file) continue;
    try {
      combined += fs.readFileSync(file, 'utf8');
    } catch { /* одного из источников нет — сверяемся по второму */ }
  }
  if (!combined) return classifierDegradedMark();
  const file = tempFile('mixed-plan');
  try {
    fs.writeFileSync(file, combined);
  } catch {
    return classifierDegradedMark();
  }
  const verdict = classify(classifier, 'match', [file], desc);
  try {
    fs.rmSync(file, { force: true });
  } catch { /* временный файл переживёт прогон, это не влияет на вердикт */ }
  if (verdict.startsWith('NOMATCH:')) {
    deny(`Заблокировано план-гейтом: правка цели из периметра расходится с одобренным планом — ${verdict.slice('NOMATCH:'.length)}. Пути дальше: дельта плана, предметный вопрос-разрешение или прямое указание Влада, режим acceptEdits.`);
  }
  return undefined;
}

// throwawayCheck(описание, цель) → пропуск времянки или deny.
function throwawayCheck(desc, target) {
  if (classifierAvailable(classifier)) {
    if (classify(classifier, 'throwaway', [], desc) === 'THROWAWAY') return;
  }
  denyScope(target);
}

// isEphemeral(путь) — путь, правка которого системным изменением не является.
// Общий для правки файлов и для Bash-записи: разъехавшиеся списки дали бы
// поверхность, где одно и то же место то гейтится, то нет.
function isEphemeral(fp) {
  // Файл плана пишет план-мод ДО того, как появится маркер, — гейт на нём
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

// gitEphemeral(путь) — игнорируемое гитом эфемерно (сборка, логи) для ЛЮБОГО
// инструмента записи, кроме путей внутри .claude/: там игнор не оправдание.
function gitEphemeral(fp) {
  if (fp.startsWith('.claude/') || fp.includes('/.claude/')) return false;
  return isIgnored(fp);
}

function relativeTo(fp) {
  return fp.startsWith(`${PWD}/`) ? fp.slice(PWD.length + 1) : fp;
}

// --- Правки файлов (Write/Edit/MultiEdit/NotebookEdit) -----------------------
if (isFileEdit) {
  const fp = input.file_path || input.notebook_path || '';
  if (!fp) process.exit(0);
  if (isEphemeral(fp)) process.exit(0);
  if (gitEphemeral(fp)) process.exit(0);

  // Текст правки по инструменту: Edit/Write — new_string/content, MultiEdit —
  // все edits[].new_string, NotebookEdit — new_source; без них классификатор
  // видел бы пустую правку и не мог ловить выход за одобренное. Заменяемый
  // текст (old_string) сериализуется тоже: без него у Edit новый текст читается
  // как ДОБАВЛЕНИЕ целиком, и якорные строки замены дают ложное «сверх плана».
  const edits = Array.isArray(input.edits) ? input.edits : [];
  const joined = (key) => edits.map((e) => (e && e[key]) || '').join('\n---\n');
  const oldText = input.old_string ?? (joined('old_string') || '');
  const newText = input.new_string ?? input.content ?? input.new_source
    ?? (joined('new_string') || '');

  const desc = Buffer.concat([
    Buffer.from(`инструмент: ${tool}\nфайл: ${fp}\nзаменяемый текст:\n`, 'utf8'),
    headBytes(oldText, 2000),
    Buffer.from('\nновый текст:\n', 'utf8'),
    headBytes(newText, 4000),
  ]);

  // Абсолютный путь к цели внутри текущего репо матчится и по репо-относительной
  // записи плана: строки «- где:» пишутся от корня репозитория.
  const rel = relativeTo(fp);
  if (inScope(fp) || (rel !== fp && inScope(rel))) {
    if (matchSrc === 'button') buttonContentCheck(desc, fp, rel);
    else scopeContentCheck(desc);
    process.exit(0);
  }

  if (permissionGrant(desc, rel)) process.exit(0);
  if (planScopeNonempty()) {
    throwawayCheck(desc, fp);
    process.exit(0);
  }
  deny(`Заблокировано план-гейтом: правка файла (${fp}) без одобренного плана. Правки кода и системы идут через план-гейт: план-мод → ExitPlanMode (одобрение Влада именно тулзой, не текстом) → правки целей плана. Автономному прогону — CRAFT_AUTONOMOUS=1.`);
}

// --- Bash-записи -------------------------------------------------------------
// Разбор строки на ЦЕЛИ записи. Гейтится цель, а не команда: сборка, копирование
// в игнорируемый путь и любое чтение проходят.
if (isBash) {
  const cmd = input.command || '';
  if (!cmd) process.exit(0);

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

  // Знак «больше» бывает и сравнением: в кавычках (jq 'select(.size > 10)') и в условных
  // скобках ([[ a > b ]]). Оба места вычёркиваются — но в ОТДЕЛЬНУЮ строку: разбору
  // записи из интерпретатора нужны буквальные кавычки вокруг пути, на вычеркнутой он бы
  // ослеп. Цена — перенаправление в закавыченную цель (> "мой файл") не увидится.
  const scan = stripQuotedHeredocs(cmd).split('\n').map((line) => line
    .replace(/'[^']*'/g, ' ')
    .replace(/"[^"]*"/g, ' ')
    .replace(/\[\[[^\]]*\]\]/g, ' ')
    .replace(/\(\([^)]*\)\)/g, ' ')).join('\n').replace(/\n+$/, '');

  // Все совпадения регулярки по строкам текста — как их печатал grep -oE.
  const matchAll = (text, re) => {
    const found = [];
    for (const line of text.split('\n')) {
      for (const m of line.matchAll(new RegExp(re, 'g'))) found.push(m[0]);
    }
    return found;
  };
  const lastField = (s) => s.trim().split(/\s+/).pop();

  // Цели: перенаправление (> >>), tee, правка на месте (-i), cp/mv (последний аргумент
  // либо явная цель после -t), запись из интерпретатора (open(…,'w'), write_text/bytes).
  const targets = [];
  for (const m of matchAll(scan, />>?[ \t]*[^|&;()<>\s]+/.source)) {
    targets.push(m.replace(/^>>?[ \t]*/, ''));
  }
  for (const m of matchAll(scan, /\btee\b([ \t]+-[a-zA-Z]+)*[ \t]+[^|&;()<>\s]+/.source)) {
    targets.push(lastField(m));
  }
  for (const m of matchAll(scan, /\b(sed|perl)\b[^|&;]*[ \t]-i[^|&;]*/.source)) {
    for (const word of m.split(' ')) if (/[/.]/.test(word)) targets.push(word);
  }
  for (const m of matchAll(scan, /\b(cp|mv)\b[^|&;]*[ \t]-t[ \t]+[^\s|&;]+/.source)) {
    targets.push(m.replace(/^.*[ \t]-t[ \t]+/, ''));
  }
  const noDashT = scan.split('\n').filter((line) => !/[ \t]-t[ \t]/.test(line)).join('\n');
  for (const m of matchAll(noDashT, /\b(cp|mv)\b[ \t]+[^|&;()<>]+/.source)) {
    targets.push(lastField(m));
  }
  for (const m of matchAll(cmd, /open\([ \t]*['"][^'"]+['"][ \t]*,[ \t]*['"][wa]/.source)) {
    targets.push(m.replace(/^open\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  for (const m of matchAll(cmd, /Path\([ \t]*['"][^'"]+['"][ \t]*\)[ \t]*\.[ \t]*write_(text|bytes)/.source)) {
    targets.push(m.replace(/^Path\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  if (!targets.some((t) => /\S/.test(t))) process.exit(0);

  // Команда проходит, только когда КАЖДАЯ неэфемерная цель в периметре: смешанная
  // команда (одна цель из плана, другая нет) не проезжает по половине разрешения.
  let offender = '';
  let scoped = false;
  let sawButton = false;
  const bashGoals = [];
  for (const rawTarget of targets) {
    if (!/\S/.test(rawTarget)) continue;
    // Дескрипторы и устройства целями записи в дерево не являются.
    if (rawTarget.startsWith('/dev/') || rawTarget.startsWith('-')) continue;
    if (['0', '1', '2', '&1', '&2'].includes(rawTarget)) continue;
    const t = rawTarget.replace(/"$/, '').replace(/^"/, '').replace(/'$/, '').replace(/^'/, '');
    if (isEphemeral(t)) continue;
    if (gitEphemeral(t)) continue;
    const rel = relativeTo(t);
    bashGoals.push(rel);
    if (inScope(t) || (rel !== t && inScope(rel))) {
      scoped = true;
      if (matchSrc === 'button') sawButton = true;
      continue;
    }
    if (!offender) offender = t;
  }

  const bdesc = Buffer.concat([
    Buffer.from('инструмент: Bash\nкоманда:\n', 'utf8'),
    headBytes(cmd, 4000),
  ]);

  if (!offender) {
    // Сверка содержания одним вызовом на команду — только когда хоть одна цель
    // прошла именно по периметру: чисто эфемерная запись классификатора не стоит.
    // Кнопочные цели в команде сверяются против файла микро-планов вместе с
    // планом сессии (смешанная команда), deny-текст — плановый.
    if (scoped && scopelist) {
      if (sawButton) mixedContentCheck(bdesc);
      else scopeContentCheck(bdesc);
    }
    process.exit(0);
  }

  if (permissionGrant(bdesc, ...bashGoals)) process.exit(0);
  if (planScopeNonempty()) {
    throwawayCheck(bdesc, offender);
    process.exit(0);
  }
  deny(`Заблокировано план-гейтом: запись в файл (${offender}) через Bash без одобренного плана. Шелл-запись — та же правка файла, что Write/Edit, и идёт через тот же гейт: план-мод → ExitPlanMode (одобрение Влада именно тулзой, не текстом) → правки целей плана. Сборка, вывод во временный каталог и в игнорируемый гитом путь проходят без плана. Автономному прогону — CRAFT_AUTONOMOUS=1.`);
}

// --- Craft-записи ------------------------------------------------------------
// Обход по предодобренной зоне: без плана проходит команда, чьи цели ЦЕЛИКОМ
// лежат внутри страницы прямого редактирования. Ключ — ЦЕЛЬ ЗАПИСИ, а не
// формулировка: настоящая запись в проект или сферу несёт block-ID вне зоны и
// плана всё равно требует.
const scopeFile = exemptScopeFile();
const craftCmd = input.command || '';
const UUID = /[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}/g;
const ids = [...new Set(craftCmd.match(UUID) || [])].sort();

if (ids.length && nonEmptyFile(scopeFile)) {
  let allowed = [];
  try {
    allowed = fs.readFileSync(scopeFile, 'utf8').split('\n');
  } catch { /* зона нечитаема — идём дальше по периметру */ }
  if (ids.every((id) => allowed.includes(id.replace(/[a-f]/g, (c) => c.toUpperCase())))) {
    process.exit(0);
  }
}

const cdesc = Buffer.concat([
  Buffer.from('инструмент: craft_write\nкоманда:\n', 'utf8'),
  headBytes(craftCmd, 4000),
]);
const lowerIds = ids.map((id) => id.replace(/[A-F]/g, (c) => c.toLowerCase()));

// Периметр плана: каждый UUID команды обязан быть в списке целей. Команда без
// единого UUID адресуемой цели не несёт — остаётся deny, как раньше. Смешанные
// источники совпадений (часть UUID из плана, часть из разрешений) сверяются
// против одобренных планов и микро-планов вместе, как в Bash-ветке.
if (scopelist && ids.length) {
  let allIn = true;
  let craftSawButton = false;
  let craftSawPlan = false;
  for (const id of ids) {
    const lid = id.replace(/[A-F]/g, (c) => c.toLowerCase());
    if (inScope(lid) || inScope(id)) {
      if (matchSrc === 'button') craftSawButton = true;
      else craftSawPlan = true;
    } else {
      allIn = false;
      break;
    }
  }
  if (allIn) {
    if (craftSawButton && craftSawPlan) mixedContentCheck(cdesc);
    else if (craftSawButton) buttonContentCheck(cdesc, lowerIds[0], '');
    else scopeContentCheck(cdesc);
    process.exit(0);
  }
  if (permissionGrant(cdesc, ...lowerIds)) process.exit(0);
  denyScope(`craft: ${headBytes(ids.join('\n'), 120).toString('utf8').replace(/\n/g, ' ')} `);
}

// Команда с UUID при недоступном периметре: цели известны — разрешение оставляет
// след; без единого UUID целей нет — пропуск по вердикту без следа (правило по
// признаку).
if (ids.length) {
  if (permissionGrant(cdesc, ...lowerIds)) process.exit(0);
} else if (permissionGrant(cdesc)) process.exit(0);

deny('Заблокировано план-гейтом: запись в Craft без одобренного плана. Сначала покажи план и получи ок Влада (план-мод → ExitPlanMode), потом пиши цели плана. Запись целиком внутри предодобренной зоны (напр. «Продукты») проходит без плана. Автономному прогону (рутина, евал) — CRAFT_AUTONOMOUS=1.');
