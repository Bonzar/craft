#!/usr/bin/env node
// PreToolUse plan-gate: рабочие правки — код, система, Craft — по умолчанию
// закрыты; открывает их РЕЕСТР ОДОБРЕННОГО, а не совпадение пути со списком.
//
// Одна сверка на все поверхности: правка вместе с реестром уходит модели, и та
// отвечает одним из пяти исходов — запрещено записью, разрешено поверх запрета,
// покрыта задачей, черновое, не покрыта. Пути из «где:» остаются подсказкой в
// рендере, но основанием для отказа быть перестали: план вида «переименовать во
// всех местах использования» файлов не перечисляет.
//
// Поверхности:
//   - Write|Edit|MultiEdit|NotebookEdit — правки файлов где угодно, кроме
//     эфемерного (планы, tmp/scratchpad, служебное ~/.claude) и игнорируемого
//     гитом ВНЕ .claude/ — внутри .claude/ живут игнорируемые, но системные
//     файлы (settings.local.json, кэш предодобренной зоны), их игнор-лазейка
//     открывала бы без плана;
//   - Bash — разбор команды на цели записи: перенаправление, tee, sed/perl -i,
//     cp/mv, запись из интерпретатора. Эфемерные цели отсеиваются, остальное
//     идёт в сверку одной командой;
//   - craft_write — сверяется так же; отдельно и раньше сверки проходит
//     предодобренная зона (exempt-scope, напр. «Продукты»).
//
// Приём материала в реестр идёт фоном, и сверка ЖДЁТ его конца: правка, которую
// Влад только что разрешил репликой, иначе упёрлась бы в гейт.
//
// CRAFT_AUTONOMOUS=1 обходит гейт целиком — рутины и headless-евалы
// предавторизованы, интерактивного Влада там нет.
//
// Непокрыто и названо честно: неопознанная конструкция записи; пакетные
// менеджеры и операции гита над рабочим деревом (пишут своей логикой, не
// перенаправлением); перенаправление в закавыченную цель — кавычки
// вычёркиваются, чтобы «больше» в сравнении не считалось записью.
import fs from 'node:fs';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import {
  isEphemeral, gitEphemeral, bashWriteTargets, cleanTarget,
} from './lib/write-targets.js';
import { lastInputTrace, exemptScopeFile, approvalRegistry } from './lib/paths.js';
import { waitForParsing, readRegistry, render } from './lib/registry.js';
import { classify, classifierPath } from './lib/classifier.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const { raw, event, tool, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

// Гейт открывает только bypassPermissions: он снимает и вопросы харнесса, то
// есть Влад им действительно отказывается от проверок целиком.
//
// acceptEdits в списке БОЛЬШЕ НЕТ. Он назывался разрешением, но давал худшее из
// двух: смысловую сверку снимал, а вопросы харнесса оставлял — записи в .claude
// лежат среди его защищённых путей, и правила разрешений их не пре-одобряют.
// Влад всё равно жал ок на каждую правку, только уже без проверки.
const mode = event.permission_mode || '';
if (mode === 'bypassPermissions') process.exit(0);

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

function tempFile(prefix) {
  const dir = process.env.TMPDIR || '/tmp';
  return path.join(dir, `${prefix}.${randomBytes(3).toString('hex')}`);
}

function nonEmptyFile(file) {
  try {
    return fs.statSync(file).size > 0;
  } catch {
    return false;
  }
}


function relativeTo(fp) {
  return fp.startsWith(`${PWD}/`) ? fp.slice(PWD.length + 1) : fp;
}

const classifier = classifierPath();

// --- Сверка по реестру одобренного -------------------------------------------
// Одна сверка вместо трёх веток. Прежние спрашивали каждая про своё — план,
// окно разрешений, времянка, — и правка сверялась то с одним, то с другим.
//
// Пять исходов: запрещено записью, разрешено поверх запрета, покрыта задачей,
// черновое, не покрыта. Запрет проверяется раньше покрытия, а более позднее
// явное разрешение бьёт запрет.
//
// Классификатор недоступен — отказ. Мягкой деградации к путь-матчу больше нет:
// пути перестали быть основанием для решения.
function coverCheck(desc) {
  const registry = approvalRegistry();
  const goals = readRegistry(registry);
  // Пустой реестр модель не зовёт: одобренного нет, и спрашивать не о чем.
  if (!goals.length) {
    deny('Заблокировано план-гейтом: одобренного нет — реестр пуст. Покажи план и получи ок Влада, либо задай предметный вопрос-разрешение. Автономному прогону — CRAFT_AUTONOMOUS=1.');
  }

  const view = tempFile('registry-view');
  try {
    fs.writeFileSync(view, render(goals));
  } catch {
    deny('Заблокировано план-гейтом: реестр одобренного не читается, сверить правку не с чем.');
  }
  const verdict = classify(classifier, 'cover', [view], desc, {
    timeoutSec: process.env.PLAN_CLASSIFIER_TIMEOUT || 60,
  });
  try { fs.rmSync(view, { force: true }); } catch { /* временный вид не убрался */ }

  if (verdict.startsWith('OVERRIDE')) return;
  if (verdict.startsWith('FORBIDDEN')) {
    deny(`Заблокировано план-гейтом: это запрещено твоей же записью —${verdict.slice('FORBIDDEN:'.length)}. Пути дальше: сними запрет прямо в диалоге, либо покажи план с этой целью.`);
  }
  if (verdict.startsWith('COVERED')) return;
  if (verdict === 'DRAFT') {
    // Черновое проходит только при плановой цели в реестре: иначе в свежей
    // сессии достаточно, чтобы модель назвала правку отладочной. Надгробие
    // считается наравне с живой целью — оно тоже доказывает, что план в этой
    // сессии одобряли, а закрытая работа права на времянку не отнимает.
    if (goals.some((g) => g.source === 'plan')) return;
    deny('Заблокировано план-гейтом: правка выглядит черновой, но одобренного плана в реестре нет — чернового без работы не бывает.');
  }
  if (verdict.startsWith('UNCOVERED')) {
    deny(`Заблокировано план-гейтом: одобренное этого не покрывает —${verdict.slice('UNCOVERED:'.length)}. Пути дальше: покажи план с этой целью или дай прямое разрешение в диалоге.`);
  }
  deny('Заблокировано план-гейтом: сверка недоступна, а без неё решение принять нечем. Аварийный выключатель — PLAN_CLASSIFIER=off, автономному прогону — CRAFT_AUTONOMOUS=1.');
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
  coverCheck(desc);
  process.exit(0);
}

// --- Bash-записи -------------------------------------------------------------
// Разбор строки на ЦЕЛИ записи. Гейтится цель, а не команда: сборка, копирование
// в игнорируемый путь и любое чтение проходят.
if (isBash) {
  const cmd = input.command || '';
  if (!cmd) process.exit(0);

  const targets = bashWriteTargets(cmd);
  if (!targets.some((t) => /\S/.test(t))) process.exit(0);

  // Эфемерные цели отсеиваются здесь же: временный файл, вывод сборки и
  // игнорируемый гитом путь гейта не касаются, и звать на них модель незачем.
  const realTargets = targets.filter((rawTarget) => {
    const t = cleanTarget(rawTarget);
    if (!t) return false;
    return !isEphemeral(t) && !gitEphemeral(t);
  });
  if (!realTargets.length) process.exit(0);

  const bdesc = Buffer.concat([
    Buffer.from('инструмент: Bash\nкоманда:\n', 'utf8'),
    headBytes(cmd, 4000),
  ]);

  coverCheck(bdesc);
  process.exit(0);
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

// Craft-запись сверяется тем же вопросом, что правки файлов и шелла: решает
// реестр одобренного, а не совпадение идентификаторов блоков со списком целей.
coverCheck(cdesc);
