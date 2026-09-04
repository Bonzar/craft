#!/usr/bin/env node
// Хук метрик: журнал событий сессии в JSONL. Стоит ПОСЛЕДНИМ в каждой цепочке
// диспетчера и зовётся всегда, даже когда цепочка уже дала решение: решение
// предыдущих хуков он читает из ЖУРНАЛА РЕШЕНИЙ в каталоге состояния
// (lib/decision-log.js), а не из stdout и не из общей памяти процесса.
//
// События: SessionStart (старт, харнес, репо), UserPromptSubmit (номер хода),
// PreToolUse (инструмент, исход гейта и класс причины, время хуков),
// PostToolUse / PostToolUseFailure (длительность вызова, ошибка инструмента),
// Stop (блокировка по имени хука, длительность хода, токены хода по usage
// записей assistant транскрипта). Вызовы модели из хуков пишет сама обёртка
// вызова (classifier.js) — они идут из фоновых процессов.
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
// `edit`, `note_write`, `plan`, `question`, `stage`, `push`, `skill` и
// `mutates` — «этот вызов менял мир». Их ставит здесь обёртка, потому что имена
// инструментов знает она; сводка считает по признакам и про инструменты не
// знает ничего.
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
import { decisionFor, timingsFor, hasFlag } from './lib/decision-log.js';
import { mutationOf } from './lib/write-targets.js';
import { toolFlags, callShape, toolScope, semanticInput } from './lib/tool-flags-claude.js';
import { commandTargets } from './lib/write-targets-bash.js';
import { gitMutates } from './lib/write-targets-git.js';
import { repoOf, isIgnored } from './lib/repo-git.js';

// Адаптеры инструментов для общей части. Собирает их ОБЁРТКА: сама общая часть
// ни одного инструмента по имени не знает и ничего себе не выбирает — без
// адаптера она отвечает `unsupported`, и это видно строкой журнала. Команда
// интерпретатора разбирается двумя: шелл даёт цели записи, git — правку, которая
// целями не видна («git commit» ничего не перенаправляет); третьим приходит
// вопрос «игнорирует ли путь репозиторий».
const ADAPTERS = {
  commandWrites: (text) => ({ mutates: gitMutates(text), targets: commandTargets(text) }),
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
    kind: 'session', ...ctx.base, source: event.source,
    harness: event.harness, repo: state.repo, sid,
  });
}

function onPrompt(state, ctx) {
  state.turn += 1;
  state.turn_started_at = now;
  const incident = hasFlag(event, 'incident');
  const h = promptHash(prompt);
  const repeat = Boolean(h) && state.prompt_hashes.includes(h);
  if (h) state.prompt_hashes = [...state.prompt_hashes, h].slice(-50);
  // Новый ход — новые стадии, вызовы и прогресс.
  state.turn_calls = {};
  state.turn_stages = {};
  state.turn_tools = 0;
  state.turn_progress = false;
  append(log, {
    kind: 'prompt', ts: ctx.ts, turn: state.turn, incident,
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
  // Решение предыдущих хуков цепочки — из журнала решений, а не из общей памяти
  // процесса: строку пишет тот, кто решил, ещё до выхода, поэтому решение видно и
  // когда решивший хук следом упал. Строки нет — решения не было, то есть проход.
  // Класс причины считает решивший: текста причины в журнале нет вовсе.
  const kind = ctx.decision ? ctx.decision.outcome : 'allow';
  const record = {
    kind: 'pre', ...ctx.base, tool, id, decision: kind,
    by: ctx.decision ? ctx.decision.hook : '',
    class: ctx.decision ? ctx.decision.class || '' : '',
    // Хеш вызова: по нему сводка узнаёт «тот же вызов» для ложных отказов.
    // Служебные поля входа этого харнеса отсеиваются ЗДЕСЬ — общая часть их имён
    // не знает. Сам вход в журнал не идёт ни в каком виде.
    h: callHash(tool, semanticInput(tool, input)),
    hooks_ms: ctx.hooksMs, hooks: ctx.hooks,
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
  // Мутирующий вызов помечается ТОЛЬКО у прошедших: у отказанного не будет
  // PostToolUse, а разбор целей записи стоит запусков git на каждую цель.
  if (kind === 'allow') {
    const mutation = mutationOf(toolScope(tool, input), callShape(tool, input), ADAPTERS);
    if (mutation.status !== 'ok') record.unsupported = mutation.capability;
    else if (mutation.mutates) record.mutates = true;
    if (id) {
      state.pre_flags[id] = {
        push: record.push === true, stage: record.stage === true, mutates: record.mutates === true,
      };
      capMap(state.pre_flags);
    }
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
    ...ctx.base, tool, id, hooks_ms: ctx.hooksMs, hooks: ctx.hooks,
  };
  if (Number.isFinite(started)) record.tool_ms = now - started;
  record.error = ctx.name === EVENTS.POST_TOOL_FAILURE || responseIsError(response);
  // Прогресс хода держится на идентификаторе вызова: без него признаки события до
  // вызова не с чем связать. Молча писать «прогресса не было» нельзя — это ложь
  // про ход; непокрытое называется явно (решение 8).
  if (!id) {
    record.unsupported = 'call-id';
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
  const record = {
    kind: 'stop', ...ctx.base,
    blocked_by: ctx.decision && ctx.decision.outcome === 'block' ? ctx.decision.hook : '',
    hooks_ms: ctx.hooksMs, hooks: ctx.hooks,
  };
  if (missing) record.unsupported = unsupported(missing).capability;
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
}

// Всё, что трогает состояние, идёт ОДНОЙ залоченной правкой. Внутри неё
// process.exit недопустим: он не разматывает finally, и лок остался бы взятым
// до истечения его срока, то есть следующий хук ждал бы минуты. Поэтому выходы
// внутри — обычные return.
const stop = updateState(log, (state) => {
  ensureShape(state);
  // Замеры времени хуков цепочки — из журнала: их кладёт туда диспетчер одной
  // строкой перед последним хуком.
  const timings = timingsFor(event);
  const ctx = {
    name,
    ts,
    id: event.call_id,
    hooksMs: Object.values(timings).reduce((sum, ms) => sum + (Number(ms) || 0), 0),
    hooks: timings,
    decision: decisionFor(event),
    base: { ts, turn: state.turn },
  };

  if (name === EVENTS.SESSION_START) onSessionStart(state, ctx);
  else if (name === EVENTS.PROMPT) onPrompt(state, ctx);
  else if (name === EVENTS.PRE_TOOL) onPre(state, ctx);
  else if (name === EVENTS.POST_TOOL || name === EVENTS.POST_TOOL_FAILURE) onPost(state, ctx);
  else if (name === EVENTS.STOP) {
    onStop(state, ctx);
    return true;
  }
  return false;
});

// Сводка — свёртка журнала с начала сессии; пишется на каждом конце хода заново.
// Складывается ВНЕ правки состояния: свёртка читает журнал целиком, и держать под
// ней лок хода незачем — ход ждал бы чтения, которое его не касается.
if (stop) refreshSummary(log, { sid, now, record: true });
