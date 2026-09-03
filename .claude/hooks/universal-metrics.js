#!/usr/bin/env node
// Хук метрик: журнал событий сессии в JSONL. Стоит ПОСЛЕДНИМ в каждой цепочке
// диспетчера и зовётся всегда, даже когда цепочка уже дала решение: решение
// предыдущих хуков он читает из общего состояния события (globalThis, его
// заполняют decide.js и dispatch.js), а не из stdout.
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
// в summarize (lib/metrics.js); здесь он не пересказывается.
//
// Сигналы: повтор реплики и маркер переуказания (repeat, reinstruct у prompt),
// повтор того же вызова и повтор стадии в ходе (repeat_call, stage_repeat у
// pre), ход без прогресса (no_progress у stop). Всё — булевы признаки, текста
// в них нет.
//
// Контракт записи вызова (по нему судят и сводка, и кейсы): pre несёт признаки
// `edit`, `craft_write`, `plan`, `question`, `stage`, `push`, `skill` и
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
import { readEvent, responseIsError } from './lib/event.js';
import {
  append, updateState, reasonClass, turnUsage,
  projectDispatcherAt, currentMetricsLog, transcriptSize,
  refreshSummary, callHash,
  promptHash, looksLikeReinstruction, isProgress, currentSessionId,
} from './lib/metrics.js';
import { toolFlags, looksMutating } from './lib/write-targets.js';
import { repoOf } from './lib/repo-git.js';

const {
  event, tool, cwd, transcript, response, input, prompt,
} = readEvent();
const name = event.hook_event_name || '';
if (!name) process.exit(0);

// Формула сессии одна на слой и живёт в lib/metrics.js: своя копия здесь уже
// расходилась бы с той, по которой резолвится путь журнала.
const sid = currentSessionId();
const log = currentMetricsLog();
if (!log) process.exit(0);

// Пользовательский контур уступает проектному, когда тот зарегистрирован в
// чекауте сессии. Вне диспетчера контур не задан — считается проектным.
if ((globalThis.hookScope || 'project') === 'universal' && projectDispatcherAt(cwd)) process.exit(0);

const now = Date.now();
const ts = new Date(now).toISOString();

// Всё, что трогает состояние, идёт ОДНОЙ залоченной правкой. Внутри неё
// process.exit недопустим: он не разматывает finally, и лок остался бы взятым
// до истечения его срока, то есть следующий хук ждал бы минуты. Поэтому выходы
// внутри — обычные return.
const stop = updateState(log, (state) => {
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
  // Время хуков цепочки до этого: диспетчер складывает замеры в общее состояние.
  const timings = Array.isArray(globalThis.hookTimings) ? globalThis.hookTimings : [];
  const hooksMs = timings.reduce((sum, t) => sum + (Number(t.ms) || 0), 0);
  const hooks = Object.fromEntries(timings.map((t) => [t.name, t.ms]));
  const decision = globalThis.hookDecision && typeof globalThis.hookDecision === 'object'
    ? globalThis.hookDecision : null;

  const id = typeof event.tool_use_id === 'string' ? event.tool_use_id : '';
  const base = { ts, turn: state.turn };

  if (name === 'SessionStart') {
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
      kind: 'session', ...base, source: typeof event.source === 'string' ? event.source : '',
      harness: process.env.CRAFT_HARNESS || 'claude', repo: state.repo, sid,
    });
  } else if (name === 'UserPromptSubmit') {
    state.turn += 1;
    state.turn_started_at = now;
    const flags = globalThis.hookFlags && typeof globalThis.hookFlags === 'object' ? globalThis.hookFlags : {};
    const h = promptHash(prompt);
    const repeat = Boolean(h) && state.prompt_hashes.includes(h);
    if (h) state.prompt_hashes = [...state.prompt_hashes, h].slice(-50);
    // Новый ход — новые стадии, вызовы и прогресс.
    state.turn_calls = {};
    state.turn_stages = {};
    state.turn_tools = 0;
    state.turn_progress = false;
    append(log, {
      kind: 'prompt', ts, turn: state.turn, incident: flags.incident === true,
      repeat, reinstruct: looksLikeReinstruction(prompt),
    });
  } else if (name === 'PreToolUse') {
    if (!tool) return;
    if (id) {
      state.inflight[id] = now;
      // Полёт бывает недописанным (вызов отменён) — держим не больше полусотни.
      const ids = Object.keys(state.inflight);
      if (ids.length > 50) for (const old of ids.slice(0, ids.length - 50)) delete state.inflight[old];
    }
    const kind = decision ? decision.kind : 'allow';
    const record = {
      kind: 'pre', ...base, tool, id, decision: kind,
      by: decision ? decision.hook : '',
      class: decision && (kind === 'deny' || kind === 'ask') ? reasonClass(decision.hook, decision.reason) : '',
      // Хеш вызова: по нему сводка узнаёт «тот же вызов» для ложных отказов.
      // Сам вход в журнал не идёт ни в каком виде.
      h: callHash(tool, input),
      hooks_ms: hooksMs, hooks,
    };
    // Признаки вызова: правка, запись в Craft, показ плана, вопрос, стадия,
    // пуш, имя скилла. Их ставит обёртка, потому что имена инструментов знает
    // она; сводка считает по признакам.
    const flags = toolFlags(tool, input);
    Object.assign(record, flags);
    // Сигналы хода: тот же вызов повторно, стадия повторно.
    state.turn_tools += 1;
    if (state.turn_calls[record.h]) record.repeat_call = true;
    state.turn_calls[record.h] = (state.turn_calls[record.h] || 0) + 1;
    // Потолок тот же, что у полёта и признаков: длинный ход иначе растит
    // состояние без предела, а оно читается и пишется на каждом вызове.
    const hashes = Object.keys(state.turn_calls);
    if (hashes.length > 50) for (const old of hashes.slice(0, hashes.length - 50)) delete state.turn_calls[old];
    if (flags.stage === true) {
      if (state.turn_stages[tool]) record.stage_repeat = true;
      state.turn_stages[tool] = (state.turn_stages[tool] || 0) + 1;
    }
    // Мутирующий вызов помечается ТОЛЬКО у прошедших: у отказанного не будет
    // PostToolUse, а разбор целей записи стоит запусков git на каждую цель.
    if (kind === 'allow') {
      if (looksMutating(tool, input)) record.mutates = true;
      if (id) {
        state.pre_flags[id] = {
          push: record.push === true, stage: record.stage === true, mutates: record.mutates === true,
        };
        const ids = Object.keys(state.pre_flags);
        if (ids.length > 50) for (const old of ids.slice(0, ids.length - 50)) delete state.pre_flags[old];
      }
    }
    append(log, record);
  } else if (name === 'PostToolUse' || name === 'PostToolUseFailure') {
    if (!tool) return;
    const started = id ? Number(state.inflight[id]) : NaN;
    if (id) delete state.inflight[id];
    const record = {
      kind: name === 'PostToolUse' ? 'post' : 'fail', ...base, tool, id, hooks_ms: hooksMs, hooks,
    };
    if (Number.isFinite(started)) record.tool_ms = now - started;
    record.error = name === 'PostToolUseFailure' || responseIsError(response);
    const pre = id ? state.pre_flags[id] : null;
    if (id) delete state.pre_flags[id];
    if (isProgress(pre, record)) state.turn_progress = true;
    append(log, record);
  } else if (name === 'Stop') {
    const { usage, offset } = turnUsage(transcript, Number(state.transcript_offset) || 0);
    state.transcript_offset = offset;
    const record = {
      kind: 'stop', ...base, blocked_by: decision && decision.kind === 'block' ? decision.hook : '',
      hooks_ms: hooksMs, hooks, usage,
    };
    if (Number.isFinite(state.turn_started_at)) record.turn_ms = now - state.turn_started_at;
    // Ход без прогресса: инструменты звались, а ни правки, ни записи, ни плана,
    // ни вопроса, ни пуша не вышло. Ход без единого вызова — разговор, не в счёт.
    record.no_progress = state.turn_tools > 0 && state.turn_progress !== true;
    append(log, record);
    return true;
  }
  return false;
});

// Сводка — свёртка журнала с начала сессии; пишется на каждом Stop заново.
// Складывается ВНЕ правки состояния: у общего лока одна занятость на процесс,
// и вложенный вызов внутри неё не залочился бы вовсе — сводку тогда могла бы
// затереть та, что собирает параллельный фоновый вызов модели.
if (stop) refreshSummary(log, { sid, now, record: true });
