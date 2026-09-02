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
// Сигналы (каталог 1.4): повтор реплики и переуказание (repeat, reinstruct у
// prompt), повтор того же вызова и повтор стадии в ходе (repeat_call,
// stage_repeat у pre), ход без прогресса и счёт ошибок хода (no_progress,
// tool_errors у stop). Всё — булевы признаки и числа, текста нет.
//
// На Stop, следом за строкой хода, в журнал ложится СВОДКА сессии одной строкой
// (kind: summary) — свёртка всего журнала с начала сессии: ходы, токены,
// вызовы модели, отказы гейта и ложные отказы, циклы плана, инциденты, время
// до первой правки, исход по записям в Craft и пушу. Её же копия — в
// `<журнал>.summary.json`, откуда её забирает хранение.
//
// В журнал не попадает содержимое: ни правок, ни команд, ни промптов, ни
// текста отказов. Ничего не печатает, сети и модели не зовёт, укладывается в
// миллисекунды. Без идентификатора сессии (ни в событии, ни в окружении, ни
// переопределением) молчит: относить события не к чему.
//
// Двойная регистрация (проектная и пользовательская) — два ПРОЦЕССА, и общее
// состояние события у каждого своё. Пишет тот, кто вёл полную цепочку: при
// проектной регистрации в чекауте сессии пользовательский контур молчит —
// иначе он записал бы «allow» там, где проектный гвард отказал.
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { metricsLog, sessionId } from './lib/paths.js';
import { sha256 } from './lib/hash.js';
import {
  append, loadState, saveState, reasonClass, repoOf, turnUsage, responseIsError,
  readJournal, summarize, writeSummary,
  promptHash, looksLikeReinstruction, STAGE_TOOLS, isProgress, looksMutating,
  projectDispatcherAt,
} from './lib/metrics.js';

const {
  raw, event, tool, cwd, transcript, response, input, prompt,
} = readEvent();
const name = event.hook_event_name || '';
if (!name) process.exit(0);

const sid = (typeof event.session_id === 'string' && event.session_id) || sessionId();
if (!sid && !process.env.CRAFT_METRICS_LOG) process.exit(0);
const log = metricsLog(sid);

// Пользовательский контур уступает проектному, когда тот зарегистрирован в
// чекауте сессии. Вне диспетчера контур не задан — считается проектным.
if ((globalThis.hookScope || 'project') === 'universal' && projectDispatcherAt(cwd)) process.exit(0);

// Хук зарегистрирован в двух контурах — второй вызов того же события уступает,
// иначе каждое событие считалось бы дважды.
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const now = Date.now();
const ts = new Date(now).toISOString();
const state = loadState(log);
if (!state.started_at) state.started_at = now;
if (!Number.isFinite(state.turn)) state.turn = 0;
if (!state.inflight || typeof state.inflight !== 'object') state.inflight = {};
// Состояние хода для сигналов: хеши вызовов и стадии этого хода, признак
// прогресса, счётчики; хеши прежних реплик — для повторов.
if (!Array.isArray(state.prompt_hashes)) state.prompt_hashes = [];
if (!state.turn_calls || typeof state.turn_calls !== 'object') state.turn_calls = {};
if (!state.turn_stages || typeof state.turn_stages !== 'object') state.turn_stages = {};
if (!Number.isFinite(state.turn_tools)) state.turn_tools = 0;
if (!Number.isFinite(state.turn_errors)) state.turn_errors = 0;
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
  // Новый ход — новые стадии и вызовы.
  state.turn_calls = {};
  state.turn_stages = {};
  state.turn_tools = 0;
  state.turn_errors = 0;
  state.turn_progress = false;
  append(log, {
    kind: 'prompt', ts, turn: state.turn, incident: flags.incident === true,
    repeat, reinstruct: looksLikeReinstruction(prompt),
  });
} else if (name === 'PreToolUse') {
  if (!tool) process.exit(0);
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
    // Хеш вызова (инструмент + вход): по нему сводка узнаёт «тот же вызов»
    // для ложных отказов. Сам вход в журнал не идёт.
    h: sha256(`${tool}\n${JSON.stringify(input)}`).slice(0, 16),
    hooks_ms: hooksMs, hooks,
  };
  // Признаки исхода сессии и инцидентного контура: пуш и имя вызванного скилла.
  if (tool === 'Bash' && /\bgit\b[^|;&]*\bpush\b/.test(String(input.command || ''))) record.push = true;
  if (tool === 'Skill' && typeof input.skill === 'string') record.skill = input.skill;
  // Сигналы хода: тот же вызов повторно, стадия повторно.
  state.turn_tools += 1;
  if (state.turn_calls[record.h]) record.repeat_call = true;
  state.turn_calls[record.h] = (state.turn_calls[record.h] || 0) + 1;
  if (STAGE_TOOLS.has(tool)) {
    if (state.turn_stages[tool]) record.stage_repeat = true;
    state.turn_stages[tool] = (state.turn_stages[tool] || 0) + 1;
  }
  // Мутирующий вызов помечается: по нему на Stop решается прогресс хода.
  if (looksMutating(tool, input)) record.mut = true;
  if (id) {
    state.pre_flags[id] = { tool, push: record.push === true, mutates: record.mut === true };
    const ids = Object.keys(state.pre_flags);
    if (ids.length > 50) for (const old of ids.slice(0, ids.length - 50)) delete state.pre_flags[old];
  }
  append(log, record);
} else if (name === 'PostToolUse' || name === 'PostToolUseFailure') {
  if (!tool) process.exit(0);
  const started = id ? Number(state.inflight[id]) : NaN;
  if (id) delete state.inflight[id];
  const record = {
    kind: name === 'PostToolUse' ? 'post' : 'fail', ...base, tool, id, hooks_ms: hooksMs, hooks,
  };
  if (Number.isFinite(started)) record.tool_ms = now - started;
  record.error = name === 'PostToolUseFailure' || responseIsError(response);
  if (record.error) state.turn_errors += 1;
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
  record.tool_errors = state.turn_errors;
  append(log, record);
  // Сводка — свёртка журнала с начала сессии; пишется на каждом Stop заново.
  const summary = summarize(readJournal(log), { sid, now });
  append(log, { kind: 'summary', ts, ...summary });
  writeSummary(log, { ts, ...summary });
} else {
  process.exit(0);
}
saveState(log, state);
