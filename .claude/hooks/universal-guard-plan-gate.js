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
// Выходов у гейта нет: ни автономного прогона, ни режима харнесса. Снять
// проверки можно только тапом Влада — рубильником, который живёт записью в
// реестре и гаснет со сменой сессии.
//
// Непокрыто и названо честно: неопознанная конструкция записи; пакетные
// менеджеры и операции гита над рабочим деревом (пишут своей логикой, не
// перенаправлением); перенаправление в закавыченную цель — кавычки
// вычёркиваются, чтобы «больше» в сравнении не считалось записью.
import fs from 'node:fs';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { readEvent } from './lib/event-claude.js';
import { deny } from './lib/decide-claude.js';
import { hookOnce } from './lib/once.js';
import { isEphemeral, ignoredEphemeral, touchesWorld } from './lib/write-targets.js';
import { isIgnored } from './lib/repo-git.js';
import { bashWriteTargets, cleanTarget } from './lib/write-targets-bash.js';
import { toolScope } from './lib/tool-flags-claude.js';
import { exemptScopeFile, approvalRegistry } from './lib/paths.js';
import {
  waitForParsing, readRegistry, render, switchAt, appendLog,
} from './lib/registry.js';
import { classify, classifierPath } from './lib/classifier.js';

const { raw, core, tool, input } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);

// Режимов харнесса гейт больше не слушает — ни bypassPermissions, ни acceptEdits.
// Снять проверки можно, но только тапом Влада, и след этого живёт записью в
// реестре: переменная окружения и режим клиента такого следа не оставляют.
// Автономный прогон тоже не выход: его задание ложится в реестр целью, и правки
// рутины сверяются с ним наравне со всеми.

// Craft-запись опознаётся по СУФФИКСУ имени, а не по полному: префикс MCP-сервера
// Craft меняется на переподключении (mcp__Craft__… в одной сессии, mcp__‹uuid›__…
// в следующей) — точное сравнение молча перестало бы гейтить на первой ротации.
const isCraftWrite = /__craft_write$/.test(tool);
const isFileEdit = !isCraftWrite && ['Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(tool);
const isBash = !isCraftWrite && tool === 'Bash';

// Гейт стоит на правках МИРА: файлы, командная строка, база, внешние сервисы.
// Всё, что мир не трогает, — не его дело. Сам предикат живёт в write-targets.js
// и имён инструментов не знает: область вызова ему приносит адаптер харнеса
// (toolScope). По тому же предикату метрики решают, менял ли ход мир, и
// разъехавшиеся копии дали бы поверхность, где одно и то же у гварда гейтится,
// а у метрик не считается.
if (!isCraftWrite && !isFileEdit && !isBash && !touchesWorld(toolScope(tool, input))) process.exit(0);

// Предел описания правки. Раньше он был 2000 байт на заменяемый текст и 4000 на
// новый и стоял из-за потолка в 128 КБ на один аргумент командной строки:
// описание уходило модели в argv. Промпт давно идёт на СТАНДАРТНЫЙ ВВОД, потолка
// нет, а обрез остался и резал живую правку посреди кода — сверка видела обрубок
// и отказывала «текст обрезан на середине».
//
// Число здесь — страховка от бинарного мусора (случайный дамп, картинка), а не
// бюджет: правка такого размера в промпт не помещается по смыслу, а не по форме.
const DESC_LIMIT = 200000;

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

// Разрешающие исходы сверки. Остальное — отказ либо неответ.
const ALLOWING = /^(OVERRIDE|COVERED|DRAFT)/;

// Сверка — вызов модели, и на пограничной правке она отвечает по-разному на
// одном и том же входе: замер пяти прогонов подряд дал четыре отказа и одно
// «покрыта». Цена разброса ложится на Влада — он повторяет разрешение по два-три
// раза, пока не повезёт.
//
// Поэтому отказ не окончателен с первого раза: он переспрашивается, а разошлись
// ответы — решает третий голос. Проход остаётся проходом сразу: лишние вызовы
// тратятся только там, где иначе Влад теряет минуты. Отказ возвращается ПЕРВЫЙ
// из полученных — его причина уже написана про эту правку.
function steadyVerdict(view, desc) {
  const first = classify(classifier, 'cover', [view], desc);
  if (ALLOWING.test(first)) return first;

  const second = classify(classifier, 'cover', [view], desc);
  const agree = ALLOWING.test(first) === ALLOWING.test(second);
  if (agree) return first;

  const third = classify(classifier, 'cover', [view], desc);
  if (ALLOWING.test(third)) return ALLOWING.test(second) ? second : third;
  return ALLOWING.test(first) ? second : first;
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

const classifier = classifierPath();

// --- Сверка по реестру одобренного -------------------------------------------
// Одна сверка вместо трёх веток. Прежние спрашивали каждая про своё — план,
// окно разрешений, времянка, — и правка сверялась то с одним, то с другим.
//
// Пять исходов: запрещено записью, разрешено поверх запрета, покрыта задачей,
// черновое, не покрыта. Запрет проверяется раньше покрытия, а более позднее
// явное разрешение бьёт запрет.
//
// Нет решения — отказ, всегда: ни падение проверки, ни недоступность модели, ни
// смерть самого хука проходом не становятся. Мягкой деградации к путь-матчу
// больше нет — пути перестали быть основанием для решения.
function coverCheck(desc) {
  const registry = approvalRegistry();
  // Реплика и ответ кнопкой разбираются в фоне, чтобы не задерживать ход. Правка
  // сразу за ними успевает раньше разбора, и без ожидания сверка читала бы ещё
  // пустой реестр — то есть отклоняла бы только что разрешённое.
  waitForParsing(registry);
  const goals = readRegistry(registry);

  // Рубильник Влада: проверки сняты, но след остаётся — каждая пропущенная
  // правка ложится в лог его записи. Молча пропускать нельзя: иначе непонятно,
  // что делалось, пока проверки не работали.
  const off = switchAt(goals);
  if (off >= 0) {
    appendLog(registry, off, `без сверки · ${String(desc).split('\n')[0].slice(0, 120)}`);
    return;
  }
  // Пустой реестр модель не зовёт: одобренного нет, и спрашивать не о чем.
  if (!goals.length) {
    deny('Заблокировано план-гейтом: одобренного нет — реестр пуст. Покажи план и получи ок Влада, либо задай предметный вопрос-разрешение.');
  }

  const view = tempFile('registry-view');
  try {
    fs.writeFileSync(view, render(goals));
  } catch {
    deny('Заблокировано план-гейтом: реестр одобренного не читается, сверить правку не с чем.');
  }
  // Потолка сверке гейт не назначает: решения ждём, сколько бы оно ни заняло.
  // Своё число у неё есть — страховка от зависшего вызова, и живёт оно там же,
  // где сама проверка.
  const verdict = steadyVerdict(view, desc);
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
  deny('Заблокировано план-гейтом: сверка не дала решения, а без него правка не идёт. Нет решения — блок: ни падение проверки, ни недоступность модели проходом не становятся.');
}

// --- Правки файлов (Write/Edit/MultiEdit/NotebookEdit) -----------------------
if (isFileEdit) {
  const fp = input.file_path || input.notebook_path || '';
  if (!fp) process.exit(0);
  if (isEphemeral(fp)) process.exit(0);
  if (ignoredEphemeral(fp, isIgnored)) process.exit(0);

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
    headBytes(oldText, DESC_LIMIT),
    Buffer.from('\nновый текст:\n', 'utf8'),
    headBytes(newText, DESC_LIMIT),
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
    return !isEphemeral(t) && !ignoredEphemeral(t, isIgnored);
  });
  if (!realTargets.length) process.exit(0);

  const bdesc = Buffer.concat([
    Buffer.from('инструмент: Bash\nкоманда:\n', 'utf8'),
    headBytes(cmd, DESC_LIMIT),
  ]);

  coverCheck(bdesc);
  process.exit(0);
}

// --- Прочие инструменты ------------------------------------------------------
// Всё, про что не видно, что оно только читает: чужие MCP-серверы, подагенты,
// которые правят, и то, чего сегодня ещё нет. Сверяется тем же вопросом —
// описанием служит имя инструмента и его вход.
if (!isCraftWrite) {
  const odesc = Buffer.concat([
    Buffer.from(`инструмент: ${tool}\nвход:\n`, 'utf8'),
    headBytes(JSON.stringify(input), DESC_LIMIT),
  ]);
  coverCheck(odesc);
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
  headBytes(craftCmd, DESC_LIMIT),
]);
// Craft-запись сверяется тем же вопросом, что правки файлов и шелла: решает
// реестр одобренного, а не совпадение идентификаторов блоков со списком целей.
coverCheck(cdesc);
