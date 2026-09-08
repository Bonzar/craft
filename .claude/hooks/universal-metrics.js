#!/usr/bin/env node
// Хук метрик: журнал событий сессии в JSONL. Стоит ПЕРВЫМ в каждой цепочке
// диспетчера — первое решение цепочку обрывает, и наблюдатель, стоящий последним,
// не звался бы ровно на отказах, которые он и считает.
//
// Отсюда правило: событие он записывает ДО решения, поэтому исхода вызова в его
// записи нет вовсе. Решение по событию пишет тот, кто решил, — строкой в ЖУРНАЛ
// РЕШЕНИЙ (lib/decision-log.js), а наблюдатель переносит новые строки этого
// журнала в свой на СЛЕДУЮЩЕМ событии и запоминает смещение. Сшивает их обратно
// свёртка — по паре «ключ события и его имя» (lib/metrics-summary.js).
//
// Почему переносом, а не чтением журнала решений на месте — в шапке
// lib/decision-log.js; здесь это не пересказывается.
//
// Событие, чьи строки ещё не приехали, свёртка называет НЕИЗВЕСТНЫМ, а не
// прошедшим: на хвосте сессии переносить решение уже некому, и пустота там значила
// бы «никто не отказал» — то есть меняла бы знак. Конец сессии (SessionEnd) стоит
// в таблице ради того же: он добирает строки последнего хода.
//
// События: SessionStart (старт, харнес, репо), UserPromptSubmit (номер хода),
// PreToolUse (инструмент и признаки вызова), PostToolUse / PostToolUseFailure
// (длительность вызова, ошибка инструмента, правил ли вызов мир),
// Stop (длительность хода, токены хода по usage записей assistant транскрипта).
// Вызовы модели из хуков пишет сама обёртка вызова (classifier.js) — они идут из
// фоновых процессов.
//
// На Stop, следом за строкой хода, в журнал ложится СВОДКА сессии одной строкой
// (kind: summary) — свёртка всего журнала с начала сессии; её копия лежит в
// `<журнал>.summary.json`, откуда её забирает хранение. Состав полей сводки —
// в summarize (lib/metrics-summary.js); здесь он не пересказывается.
//
// Сигналы: повтор реплики и маркер переуказания (repeat, reinstruct у prompt),
// повтор того же вызова и повтор стадии в ходе (repeat_call, stage_repeat у
// pre), ход без прогресса (no_progress у stop). Всё — булевы признаки, текста
// в них нет.
//
// Контракт записи вызова (по нему судят и сводка, и кейсы): pre несёт признаки
// `edit`, `note_write`, `plan`, `question`, `stage`, `push` и `skill`; признак
// `mutates` — «этот вызов менял мир» — стоит на записи СОСТОЯВШЕГОСЯ вызова
// (post), потому что разбор целей записи стоит запусков git, а до решения ещё
// неизвестно, состоится ли вызов вообще. Их ставит здесь обёртка, потому что имена
// инструментов знает она; сводка считает по признакам и про инструменты не
// знает ничего.
//
// Ключ сшивки — `key` события (lib/event-key.js): он же стоит в строке журнала
// решений. Одного ключа мало — событие до вызова и событие после него несут один
// идентификатор вызова, — поэтому сшивают по паре «ключ и событие», а событие
// свёртка узнаёт по виду записи (lib/metrics-summary.js).
//
// В журнал не попадает содержимое: ни правок, ни команд, ни промптов, ни
// текста отказов. Ничего не печатает, сети и модели не зовёт, укладывается в
// миллисекунды. Без идентификатора сессии (ни в событии, ни в окружении, ни
// переопределением) молчит: относить события не к чему.
//
// Двойная регистрация (проектная и пользовательская) — два ПРОЦЕССА, и общее
// состояние события у каждого своё. Пишет тот, кто вёл полную цепочку: при
// проектной регистрации в чекауте сессии пользовательский контур молчит —
// иначе он записал бы «allow» там, где проектный гвард отказал. Этой же
// проверкой и решается двойная запись, поэтому уступки по hookOnce здесь нет:
// её ключ для событий без идентификатора вызова — хеш события со сроком в
// секунды, и два одинаковых Stop подряд (заблокированный конец хода) или две
// одинаковые короткие реплики теряли бы вторую запись.
import { readEvent, responseIsError } from './lib/event-claude.js';
import { EVENTS, missingFact, unsupported } from './lib/event.js';
import {
  append, updateState, currentMetricsLog,
  refreshSummary, promptHash, looksLikeReinstruction, isProgress,
} from './lib/metrics.js';
import { callHash } from './lib/call-hash.js';
import { turnUsage, transcriptSize } from './lib/usage-claude.js';
import { projectDispatcherAt } from './lib/registration-claude.js';
import { readSince } from './lib/decision-log.js';
import { mutationOf } from './lib/write-targets.js';
import { toolFlags, callShape, toolScope, semanticInput } from './lib/tool-flags-claude.js';
import { commandTargets, commandTreeGap } from './lib/write-targets-bash.js';
import { gitMutates } from './lib/write-targets-git.js';
import { repoOf, isIgnored } from './lib/repo-git.js';

// Адаптеры инструментов для общей части. Собирает их ОБЁРТКА: сама общая часть
// ни одного инструмента по имени не знает и ничего себе не выбирает — без
// адаптера она отвечает `unsupported`, и это видно строкой журнала. Команда
// интерпретатора разбирается двумя: шелл даёт цели записи, git — правку, которая
// целями не видна («git commit» ничего не перенаправляет); третьим приходит
// вопрос «игнорирует ли путь репозиторий».
const ADAPTERS = {
  // Разбора нет — ответа нет, и это непокрытое С ИМЕНЕМ: пустой список целей
  // здесь читался бы как «команда ничего не пишет».
  commandWrites: (text) => (commandTreeGap(text)
    ? { unsupported: commandTreeGap(text) }
    : { mutates: gitMutates(text), targets: commandTargets(text) }),
  ignored: isIgnored,
};

const event = readEvent();
const {
  tool, cwd, transcript, response, input, prompt,
} = event;
const name = event.event;
if (!name) process.exit(0);

// Сессия берётся из ядра события — по ней же резолвится путь журнала.
const sid = event.session_id;
const log = currentMetricsLog();
if (!log) process.exit(0);

// Пользовательский контур уступает проектному, когда тот зарегистрирован в
// чекауте сессии. Вне диспетчера контур не задан — считается проектным.
if ((process.env.CRAFT_HOOK_SCOPE || 'project') === 'universal' && projectDispatcherAt(cwd)) process.exit(0);

const now = Date.now();
const ts = new Date(now).toISOString();

// По функции на событие: у каждого свой вопрос, и в одном теле они не
// помещались на экран. Каждая получает состояние и общий контекст хода
// (ts, base, id, hooksMs, hooks, decision) и дописывает свою строку в журнал.
// Возвращают true только там, где ход закончился (Stop): по этому и решается,
// пересобирать ли сводку.

// Непокрытое называется СЛОВОМ, а не нулём и не умолчанием (решение 8). Имён
// бывает больше одного разом — на конце хода могут и токены не измериться, и
// канал решений оборваться, — поэтому они копятся через запятую: потерянное
// второе имя снова выдавало бы неизвестное за известное.
function markUnsupported(record, capability) {
  record.unsupported = record.unsupported ? `${record.unsupported},${capability}` : capability;
}

function onSessionStart(state, ctx) {
  // Старт бывает не только первым: компакт и возобновление дают SessionStart
  // с тем же идентификатором. Счётчики и начало сессии при этом не сбрасываются
  // — журнал продолжается, а причина старта пишется полем source.
  if (!state.repo) state.repo = repoOf(cwd);
  // Засев смещения транскрипта — ВСЕГДА по текущему размеру файла. Всё, что
  // написано до старта, принадлежит прошлым ходам, чем бы старт ни был вызван.
  // Условный засев («только если файла стало меньше») пропускал обратный
  // случай: после нечистого выхода или компакта в БОЛЬШИЙ транскрипт файл
  // длиннее сохранённого смещения, и первый же Stop записывал дорезумную
  // историю в новый ход.
  state.transcript_offset = transcriptSize(transcript);
  append(log, {
    kind: 'session', ...ctx.base, key: ctx.key, source: event.source,
    harness: event.harness, repo: state.repo, sid,
  });
}

function onPrompt(state, ctx) {
  state.turn += 1;
  state.turn_started_at = now;
  const h = promptHash(prompt);
  const repeat = Boolean(h) && state.prompt_hashes.includes(h);
  if (h) state.prompt_hashes = [...state.prompt_hashes, h].slice(-50);
  // Новый ход — новые стадии, вызовы и прогресс.
  state.turn_calls = {};
  state.turn_stages = {};
  state.turn_tools = 0;
  state.turn_progress = false;
  // Признак инцидента ставит другой хук той же цепочки — и ставит ПОСЛЕ
  // наблюдателя, поэтому в записи его нет: он придёт строкой журнала решений и
  // будет сшит по ключу события, как и решение.
  append(log, {
    // Общая часть — из ctx.base, как у всех записей: своя копия «ts + turn»
    // теряла признак диспетчера, и свёртка переставала спрашивать у реплики, а
    // доехали ли её строки. Номер хода тут свой: он только что вырос.
    kind: 'prompt', ...ctx.base, turn: state.turn, key: ctx.key,
    repeat, reinstruct: looksLikeReinstruction(prompt),
  });
}

// Потолок у всех карт состояния один: длинный ход иначе растит состояние без
// предела, а оно читается и пишется на каждом вызове.
function capMap(map, cap = 50) {
  const keys = Object.keys(map);
  if (keys.length > cap) for (const old of keys.slice(0, keys.length - cap)) delete map[old];
}

function onPre(state, ctx) {
  if (!tool) return;
  const { id } = ctx;
  if (id) {
    state.inflight[id] = now;
    capMap(state.inflight); // полёт бывает недописанным: вызов отменён
  }
  // Исхода вызова в записи НЕТ: наблюдатель зовётся до решателей, и в эту секунду
  // решения ещё не существует. Оно придёт строкой журнала решений, а сшито будет
  // по ключу события.
  const record = {
    kind: 'pre', ...ctx.base, key: ctx.key, tool, id,
    // Хеш вызова: по нему сводка узнаёт «тот же вызов» для ложных отказов.
    // Служебные поля входа этого харнеса отсеиваются ЗДЕСЬ — общая часть их имён
    // не знает. Сам вход в журнал не идёт ни в каком виде.
    h: callHash(tool, semanticInput(tool, input)),
  };
  // Признаки вызова: правка, запись в базу заметок, показ плана, вопрос, стадия,
  // пуш, имя скилла. Их ставит обёртка, потому что имена инструментов знает
  // она; сводка считает по признакам.
  const flags = toolFlags(tool, input);
  Object.assign(record, flags);
  // Сигналы хода: тот же вызов повторно, стадия повторно.
  state.turn_tools += 1;
  if (state.turn_calls[record.h]) record.repeat_call = true;
  state.turn_calls[record.h] = (state.turn_calls[record.h] || 0) + 1;
  capMap(state.turn_calls);
  if (flags.stage === true) {
    if (state.turn_stages[tool]) record.stage_repeat = true;
    state.turn_stages[tool] = (state.turn_stages[tool] || 0) + 1;
  }
  if (id) {
    state.pre_flags[id] = { push: record.push === true, stage: record.stage === true };
    capMap(state.pre_flags);
  }
  append(log, record);
}

function onPost(state, ctx) {
  if (!tool) return;
  const { id } = ctx;
  const started = id ? Number(state.inflight[id]) : NaN;
  if (id) delete state.inflight[id];
  const record = {
    kind: ctx.name === EVENTS.POST_TOOL ? 'post' : 'fail',
    ...ctx.base, key: ctx.key, tool, id,
  };
  if (Number.isFinite(started)) record.tool_ms = now - started;
  record.error = ctx.name === EVENTS.POST_TOOL_FAILURE || responseIsError(response);
  // «Менял ли вызов мир» считается на СОСТОЯВШЕМСЯ вызове, а не до него: разбор
  // целей записи стоит запусков git на каждую цель, и платить их за вызов, который
  // ещё могут запретить, незачем. До решения этого и не узнать — наблюдатель
  // зовётся первым.
  const mutation = mutationOf(toolScope(tool, input), callShape(tool, input), ADAPTERS);
  if (mutation.status !== 'ok') markUnsupported(record, mutation.capability);
  else if (mutation.mutates) record.mutates = true;
  // Прогресс хода держится на идентификаторе вызова: без него признаки события до
  // вызова не с чем связать. Молча писать «прогресса не было» нельзя — это ложь
  // про ход; непокрытое называется явно (решение 8).
  if (!id) {
    markUnsupported(record, 'call-id');
  } else {
    const pre = state.pre_flags[id];
    delete state.pre_flags[id];
    if (isProgress(pre, record)) state.turn_progress = true;
  }
  append(log, record);
}


// Хуку метрик нужен ФАКТ `tokens`. Даёт его обёртка: она читает транскрипт своего
// харнеса и отдаёт числа. Транскрипта нет — факта нет, и запись говорит об этом
// полем `unsupported`, а не нулями: неизмеренные токены и измеренный ноль — разные
// вещи. СВЁРТКА пока и то и другое складывает в ноль (metrics-summary.js), то есть
// поле сегодня видно только в журнале; довести его до сводки — отдельный шаг.
function onStop(state, ctx) {
  const from = Number(state.transcript_offset) || 0;
  const measured = transcript ? turnUsage(transcript, from) : null;
  const fact = measured ? { tokens: measured.usage } : {};
  const missing = missingFact(fact, ['tokens']);
  if (measured) state.transcript_offset = measured.offset;
  // Кто заблокировал конец хода — узнается из журнала решений: гвард решает ПОСЛЕ
  // наблюдателя. Строка сшивается по ключу события, а блокировка не теряется — за
  // блокированным концом хода всегда идёт следующий, и он её перенесёт.
  const record = { kind: 'stop', ...ctx.base, key: ctx.key };
  if (missing) markUnsupported(record, unsupported(missing).capability);
  else record.usage = fact.tokens;
  if (Number.isFinite(state.turn_started_at)) record.turn_ms = now - state.turn_started_at;
  // Ход без прогресса: инструменты звались, а ни правки, ни записи, ни плана,
  // ни вопроса, ни пуша не вышло. Ход без единого вызова — разговор, не в счёт.
  record.no_progress = state.turn_tools > 0 && state.turn_progress !== true;
  append(log, record);
}

// Поля состояния, которых может не быть: журнал переживает и старую сессию, и
// битый файл состояния.
function ensureShape(state) {
  if (!state.started_at) state.started_at = now;
  if (!Number.isFinite(state.turn)) state.turn = 0;
  if (!state.inflight || typeof state.inflight !== 'object') state.inflight = {};
  // Состояние хода для сигналов: хеши вызовов и стадии ЭТОГО хода, признак
  // прогресса; хеши прежних реплик — для повторов.
  if (!Array.isArray(state.prompt_hashes)) state.prompt_hashes = [];
  if (!state.turn_calls || typeof state.turn_calls !== 'object') state.turn_calls = {};
  if (!state.turn_stages || typeof state.turn_stages !== 'object') state.turn_stages = {};
  if (!Number.isFinite(state.turn_tools)) state.turn_tools = 0;
  if (!state.pre_flags || typeof state.pre_flags !== 'object') state.pre_flags = {};
  if (!Number.isFinite(state.decisions_offset)) state.decisions_offset = 0;
  if (typeof state.decisions_head !== 'string') state.decisions_head = '';
}

// Перенести в свой журнал всё, что дописали в журнал решений с прошлого раза.
// Смещение помнится в состоянии; журнал подмели или он начался заново — читаем с
// начала, а повторно перенесённая строка не двоится: свёртка складывает строки по
// ключу, а не по счёту.
//
// Не прочитался журнал — это НЕ «решений не было»: пропуск называется словом, и
// видно его строкой в журнале, а не пустотой в сводке.
function drainDecisions(state) {
  const { records, offset, head, status } = readSince(event, {
    offset: Number(state.decisions_offset) || 0,
    head: String(state.decisions_head || ''),
  });
  if (status !== 'ok') {
    // Журнал не прочитался — это НЕ «решений не было». Непокрытое называется
    // словом. КАКИЕ события остались без исхода, здесь не гадают: у каждой записи
    // это спрашивает свёртка, и вторая линейка для того же вопроса давала бы
    // ложную тревогу на параллельных вызовах — их строки приходят позже.
    append(log, {
      kind: 'skip', ts, what: 'decisions', capability: 'decision-log',
    });
    return;
  }
  // Перенос ОДНОРАЗОВЫЙ: журнал решений потом подметут, и строка, не легшая в
  // журнал метрик, исчезает навсегда. Поэтому смещение двигается только за теми
  // строками, которые ДЕЙСТВИТЕЛЬНО легли: отказ записи (кончилось место, снялась
  // квота) посреди переноса иначе оставил бы замеры на месте, а решение потерял —
  // и сшивка прочитала бы отказ как проход, потому что доказательство доставки
  // есть, а решения нет. Повторный перенос с того же смещения не двоит: сшивка
  // идёт по паре «ключ и событие», а считается всё по записям событий.
  let moved = 0;
  for (const rec of records) {
    if (!append(log, rec)) break;
    moved += 1;
  }
  if (moved < records.length) return;
  state.decisions_offset = offset;
  state.decisions_head = head;
}

// Всё, что трогает состояние, идёт ОДНОЙ залоченной правкой. Внутри неё
// process.exit недопустим: он не разматывает finally, и лок остался бы взятым
// до истечения его срока, то есть следующий хук ждал бы минуты. Поэтому выходы
// внутри — обычные return.
// Идёт ли этот хук под диспетчером. Спрашивается один раз: по этому же признаку
// решается, ждать ли строк канала от прошлого события.
const dispatched = Boolean(process.env.CRAFT_HOOK_SCOPE);

const stop = updateState(log, (state) => {
  ensureShape(state);
  // Перенос строк журнала решений — ПЕРВЫМ делом и внутри той же залоченной
  // правки: смещение лежит в состоянии, и без лока два процесса перенесли бы
  // одно и то же дважды. Строки прошлых событий (решение, признак, замеры) ложатся
  // в журнал метрик как есть, ключом им служит ключ события.
  drainDecisions(state);
  const ctx = {
    name,
    ts,
    id: event.call_id,
    key: event.key,
    // `disp` — «запись сделана под диспетчером». Там у события ОБЯЗАНЫ появиться
    // строки канала (замеры диспетчер кладёт на каждом), и по их отсутствию
    // свёртка отличает «решения не было» от «строки не доехали». Вне диспетчера
    // такой обязанности нет, признак не ставится, и пустота значит проход.
    base: { ts, turn: state.turn, ...(dispatched ? { disp: true } : {}) },
  };

  if (name === EVENTS.SESSION_START) onSessionStart(state, ctx);
  else if (name === EVENTS.PROMPT) onPrompt(state, ctx);
  else if (name === EVENTS.PRE_TOOL) onPre(state, ctx);
  else if (name === EVENTS.POST_TOOL || name === EVENTS.POST_TOOL_FAILURE) onPost(state, ctx);
  else if (name === EVENTS.STOP) {
    onStop(state, ctx);
    return true;
  } else if (name === EVENTS.SESSION_END) {
    // Конец сессии своей записи не имеет: события хода он не описывает. Он нужен
    // ради ПЕРЕНОСА — строки последнего хода приезжают сюда, — и ради пересборки
    // сводки по ним: заблокированный последний конец хода иначе не попал бы в ту
    // сводку, что уезжает в хранение.
    return true;
  }
  return false;
});

// Сводка — свёртка журнала с начала сессии; пишется на каждом конце хода заново.
// Складывается ВНЕ правки состояния: свёртка читает журнал целиком, и держать под
// ней лок хода незачем — ход ждал бы чтения, которое его не касается.
if (stop) refreshSummary(log, { sid, now, record: true });
