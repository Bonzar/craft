// Метрики слоя: что считается по событиям хуков и как это ложится на диск.
//
// Журнал — JSONL в файле сессии (metricsLog из paths.js): по строке на событие.
// Содержимого правок, команд и промптов в журнале нет — только имена, классы
// исходов, длительности и числа. Текст отказа сюда не пишется: из него
// вычисляется КЛАСС причины, и только он попадает в строку.
//
// Состояние между событиями (номер хода, начало хода, вызовы в полёте, смещение
// прочитанного транскрипта) живёт рядом с журналом в `<журнал>.state.json`.
// Все функции fail quiet: сломанные метрики не должны трогать ход.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { metricsLog, sessionId } from './paths.js';
import { sha256 } from './hash.js';
import {
  isEphemeral, gitEphemeral, bashWriteTargets, cleanTarget,
} from './write-targets.js';

export function append(file, record) {
  if (!file) return;
  try {
    fs.appendFileSync(file, `${JSON.stringify(record)}\n`);
  } catch { /* журнал не пополнился — метрика потеряна, ход цел */ }
}

// Журнал текущего процесса: переопределение, иначе сессия из прочитанного
// события (его кладёт readEvent), иначе из окружения. Ничего из этого нет —
// пустая строка: относить записи не к чему.
export function currentMetricsLog() {
  if (process.env.CRAFT_METRICS_LOG) return process.env.CRAFT_METRICS_LOG;
  const ev = globalThis.hookEvent;
  const sid = (ev && typeof ev.session_id === 'string' && ev.session_id) || sessionId();
  return sid ? metricsLog(sid) : '';
}

// Окружение для дочернего процесса (фоновый приём реестра): журнал передаётся
// явно, потому что события у дочернего процесса нет.
export function childEnv(base = process.env) {
  const log = currentMetricsLog();
  return log ? { ...base, CRAFT_METRICS_LOG: log } : { ...base };
}

// Вызов модели из хука: пишется самим местом вызова (обёртка в classifier.js),
// а не хуком метрик — приём реестра идёт в отдельном фоновом процессе, и хуку
// метрик его не видно.
export function recordModelCall({ mode, ms, outcome }) {
  const log = currentMetricsLog();
  if (!log) return;
  append(log, {
    kind: 'model', ts: new Date().toISOString(), mode: String(mode || ''), ms, outcome,
  });
}

// Есть ли у чекаута, в котором идёт сессия, СВОЯ регистрация диспетчера
// (проектный .claude/settings.json с dispatch.js). Если есть, проектный
// диспетчер ведёт полную цепочку, и пользовательскому контуру метрики писать
// нельзя: он не видел проектных хуков и записал бы «allow» там, где проектный
// гвард отказал. Ищется вверх от рабочего каталога события.
export function projectDispatcherAt(cwd) {
  if (!cwd) return false;
  let probe;
  try {
    if (!fs.statSync(cwd).isDirectory()) return false;
    probe = fs.realpathSync(cwd);
  } catch {
    return false;
  }
  while (probe && probe !== path.dirname(probe)) {
    const settings = path.join(probe, '.claude', 'settings.json');
    try {
      return fs.readFileSync(settings, 'utf8').includes('dispatch.js');
    } catch { /* здесь настроек нет — выше */ }
    probe = path.dirname(probe);
  }
  return false;
}

// Класс ответа модели: первый токен вердикта либо «json» у разбора. Текст
// ответа целиком в журнал не идёт.
export function verdictClass(verdict) {
  const text = String(verdict || '').trim();
  if (!text || text === 'UNAVAILABLE') return 'unavailable';
  if (text.startsWith('{')) return 'json';
  const m = text.match(/^[A-ZА-ЯЁ_]+/);
  return m ? m[0].slice(0, 20) : 'other';
}

// --- состояние ---------------------------------------------------------------

function stateFile(log) {
  return `${log}.state.json`;
}

export function loadState(log) {
  try {
    const parsed = JSON.parse(fs.readFileSync(stateFile(log), 'utf8'));
    if (parsed && typeof parsed === 'object') return parsed;
  } catch { /* состояния ещё нет */ }
  return {};
}

export function saveState(log, state) {
  try {
    fs.writeFileSync(stateFile(log), JSON.stringify(state));
  } catch { /* состояние не сохранилось — следующий ход начнёт заново */ }
}

// --- классы причин -----------------------------------------------------------

// Короткое имя хука: без контурного префикса.
export function hookShort(name) {
  return String(name || '').replace(/^(universal|craft)-/, '');
}

// Класс причины отказа по хуку и тексту. Текст нужен только план-гейту и
// дельте: у остальных хуков один исход на файл. Возвращается класс, текст
// дальше не идёт.
const GATE_CLASSES = [
  ['реестр пуст', 'gate.empty'],
  ['запрещено твоей же записью', 'gate.forbidden'],
  ['не покрывает', 'gate.uncovered'],
  ['черновой', 'gate.draft'],
  ['не читается', 'gate.unreadable'],
  ['не дала решения', 'gate.no-verdict'],
];

export function reasonClass(hook, reason) {
  const short = hookShort(hook);
  const text = String(reason || '');
  if (short === 'guard-plan-gate') {
    const hit = GATE_CLASSES.find(([needle]) => text.includes(needle));
    return hit ? hit[1] : 'gate.other';
  }
  if (short === 'guard-plan-delta') {
    return text.includes('повторяет') ? 'delta.repeats' : 'delta.unavailable';
  }
  return short || 'unknown';
}

// --- репозиторий -------------------------------------------------------------

// Репо сессии по remote origin: host/owner/repo без схемы, учётки и .git.
// Не репозиторий, нет remote — пустая строка.
export function repoOf(cwd) {
  if (!cwd) return '';
  const res = spawnSync('git', ['-C', cwd, 'config', '--get', 'remote.origin.url'], {
    encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'],
  });
  if (res.status !== 0) return '';
  return normalizeRemote((res.stdout || '').trim());
}

export function normalizeRemote(url) {
  let s = String(url || '').trim();
  if (!s) return '';
  s = s.replace(/^[a-z+]+:\/\//i, '');       // схема
  s = s.replace(/^[^@/]+@/, '');            // учётка перед хостом
  s = s.replace(/^([^:/]+):(?!\/)/, '$1/'); // scp-форма host:owner/repo
  s = s.replace(/\.git$/, '').replace(/\/+$/, '');
  return s;
}

// --- токены хода из транскрипта ------------------------------------------------

// Сумма usage записей assistant, начиная с байтового смещения. Один ответ модели
// лежит в транскрипте несколькими записями с одним message.id (по записи на
// блок содержимого) и одним и тем же usage — считается один раз, по последней
// записи. Возвращается сумма и смещение за последней ПОЛНОЙ строкой: хвост без
// перевода строки ещё дописывается и будет прочитан в следующий раз.
export function turnUsage(transcript, from = 0) {
  const empty = {
    input: 0, output: 0, cache_read: 0, cache_create: 0, messages: 0,
  };
  if (!transcript) return { usage: empty, offset: from };
  let fd;
  let text = '';
  try {
    const size = fs.statSync(transcript).size;
    if (size <= from) return { usage: empty, offset: from };
    fd = fs.openSync(transcript, 'r');
    const buf = Buffer.alloc(size - from);
    fs.readSync(fd, buf, 0, buf.length, from);
    text = buf.toString('utf8');
  } catch {
    return { usage: empty, offset: from };
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
  const lastNl = text.lastIndexOf('\n');
  if (lastNl < 0) return { usage: empty, offset: from };
  const complete = text.slice(0, lastNl + 1);
  const offset = from + Buffer.byteLength(complete, 'utf8');

  const byId = new Map();
  let anon = 0;
  for (const line of complete.split('\n')) {
    if (!line.trim()) continue;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    if (!entry || entry.type !== 'assistant') continue;
    const message = entry.message;
    if (!message || !message.usage || typeof message.usage !== 'object') continue;
    const id = typeof message.id === 'string' && message.id ? message.id : `anon-${anon += 1}`;
    byId.set(id, message.usage);
  }
  const usage = { ...empty };
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
  for (const u of byId.values()) {
    usage.input += num(u.input_tokens);
    usage.output += num(u.output_tokens);
    usage.cache_read += num(u.cache_read_input_tokens);
    usage.cache_create += num(u.cache_creation_input_tokens);
    usage.messages += 1;
  }
  return { usage, offset };
}

// --- сводка сессии -------------------------------------------------------------

// Записи журнала без строк сводки. Битые строки пропускаются.
export function readJournal(log) {
  let text = '';
  try {
    text = fs.readFileSync(log, 'utf8');
  } catch {
    return [];
  }
  const out = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const rec = JSON.parse(line);
      if (rec && typeof rec === 'object' && rec.kind !== 'summary') out.push(rec);
    } catch { /* битая строка — не запись */ }
  }
  return out;
}

export function writeSummary(log, summary) {
  try {
    fs.writeFileSync(`${log}.summary.json`, `${JSON.stringify(summary)}\n`);
  } catch { /* копия сводки не легла — в журнале она есть */ }
}

const EDIT_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit']);
const isCraftWrite = (tool) => /__craft_write$/.test(String(tool || ''));
const isEdit = (tool) => EDIT_TOOLS.has(tool) || isCraftWrite(tool);
const ms = (iso) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : NaN;
};

// Свёртка журнала сессии в одну сводку. Чистая функция над записями.
//
// Ложный отказ — «deny, затем тот же вызов прошёл в течение хода после реплики
// или кнопки»: отказ запоминается по хешу вызова; реплика (новый ход) или ответ
// кнопкой снимают с него замок; тот же хеш, прошедший в ЭТОМ ходе после снятия,
// засчитывается ложным отказом.
export function summarize(records, { sid = '', now = Date.now() } = {}) {
  const s = {
    sid, harness: '', repo: '', started_at: '', ended_at: new Date(now).toISOString(),
    turns: 0,
    tokens: { input: 0, output: 0, cache_read: 0, cache_create: 0 },
    tokens_first_turn: null,
    model_calls: { count: 0, ms: 0, by_mode: {} },
    denies: { total: 0, by_class: {} },
    false_denies: 0,
    plan: { shown: 0, bounced: 0, approved: 0 },
    incidents: { detected: 0, skill_called: 0, share: null },
    stop_blocks: {},
    tool_errors: 0,
    first_edit_ms: null,
    outcome: { craft_writes: 0, pushed: false },
    signals: {
      reinstructions: 0, call_repeats: 0, stage_repeats: 0, turns_without_progress: 0, error_streak_max: 0,
    },
  };
  let streak = 0;
  let started = NaN;
  let firstTurn = null;
  // Ход без прогресса — по ХОДУ, а не по записи Stop: заблокированный конец
  // хода даёт второй Stop с тем же номером, решает последний.
  const noProgressByTurn = new Map();
  const pending = new Map(); // hash → { unlockTurn }
  const pres = new Map();    // id → pre-запись
  const incidentTurns = new Set();
  const skillTurns = new Set();

  const unlock = (turn) => {
    for (const p of pending.values()) p.unlockTurn = turn;
  };

  for (const r of records) {
    const t = ms(r.ts);
    if (!Number.isFinite(started) && Number.isFinite(t)) started = t;
    if (Number.isFinite(r.turn)) s.turns = Math.max(s.turns, r.turn);

    if (r.kind === 'session') {
      // Стартов бывает несколько (компакт, возобновление): начало сессии —
      // самая ранняя запись, поздние старты его не двигают.
      s.harness = r.harness || s.harness;
      s.repo = r.repo || s.repo;
      s.sid = s.sid || r.sid || '';
    } else if (r.kind === 'prompt') {
      unlock(r.turn);
      if (r.incident === true) incidentTurns.add(r.turn);
      if (r.repeat === true || r.reinstruct === true) s.signals.reinstructions += 1;
    } else if (r.kind === 'pre') {
      if (r.id) pres.set(r.id, r);
      if (r.decision === 'deny') {
        s.denies.total += 1;
        const cls = r.class || 'unknown';
        s.denies.by_class[cls] = (s.denies.by_class[cls] || 0) + 1;
        if (r.h) pending.set(r.h, { unlockTurn: null });
        if (r.tool === 'ExitPlanMode') s.plan.bounced += 1;
      } else if (r.decision === 'allow') {
        if (r.tool === 'ExitPlanMode') s.plan.shown += 1;
        if (r.h && pending.has(r.h)) {
          const p = pending.get(r.h);
          if (p.unlockTurn !== null && p.unlockTurn === r.turn) s.false_denies += 1;
          pending.delete(r.h);
        }
      }
      if (r.tool === 'Skill' && /incident/i.test(String(r.skill || ''))) skillTurns.add(r.turn);
      if (r.repeat_call === true) s.signals.call_repeats += 1;
      if (r.stage_repeat === true) s.signals.stage_repeats += 1;
    } else if (r.kind === 'post' || r.kind === 'fail') {
      const failed = r.kind === 'fail' || r.error === true;
      if (failed) s.tool_errors += 1;
      streak = failed ? streak + 1 : 0;
      if (streak > s.signals.error_streak_max) s.signals.error_streak_max = streak;
      if (r.tool === 'AskUserQuestion' && !failed) unlock(r.turn);
      if (r.tool === 'ExitPlanMode' && !failed) s.plan.approved += 1;
      if (!failed && isCraftWrite(r.tool)) s.outcome.craft_writes += 1;
      if (!failed && isEdit(r.tool) && s.first_edit_ms === null
          && Number.isFinite(t) && Number.isFinite(started)) s.first_edit_ms = t - started;
      const pre = r.id ? pres.get(r.id) : null;
      if (!failed && pre && pre.push === true) s.outcome.pushed = true;
    } else if (r.kind === 'stop') {
      if (r.blocked_by) s.stop_blocks[r.blocked_by] = (s.stop_blocks[r.blocked_by] || 0) + 1;
      if (typeof r.no_progress === 'boolean') noProgressByTurn.set(r.turn, r.no_progress);
      const u = r.usage && typeof r.usage === 'object' ? r.usage : {};
      for (const key of Object.keys(s.tokens)) s.tokens[key] += Number(u[key]) || 0;
      // Токены первого хода — сумма ВСЕХ его Stop: заблокированный конец хода
      // даёт второй Stop с тем же номером и своей долей usage.
      if (s.tokens_first_turn === null || firstTurn === r.turn) {
        if (s.tokens_first_turn === null) {
          firstTurn = r.turn;
          s.tokens_first_turn = { input: 0, output: 0, cache_read: 0, cache_create: 0 };
        }
        for (const key of Object.keys(s.tokens_first_turn)) s.tokens_first_turn[key] += Number(u[key]) || 0;
      }
    } else if (r.kind === 'model') {
      s.model_calls.count += 1;
      s.model_calls.ms += Number(r.ms) || 0;
      const mode = r.mode || 'unknown';
      const m = s.model_calls.by_mode[mode] || { count: 0, ms: 0 };
      m.count += 1;
      m.ms += Number(r.ms) || 0;
      s.model_calls.by_mode[mode] = m;
    }
  }

  s.signals.turns_without_progress = [...noProgressByTurn.values()].filter(Boolean).length;
  s.incidents.detected = incidentTurns.size;
  s.incidents.skill_called = [...incidentTurns].filter((turn) => skillTurns.has(turn)).length;
  s.incidents.share = incidentTurns.size ? s.incidents.skill_called / incidentTurns.size : null;
  s.started_at = Number.isFinite(started) ? new Date(started).toISOString() : '';
  return s;
}

// --- сигналы -------------------------------------------------------------------

// Нормализованный хеш реплики: регистр, пробелы и знаки препинания не в счёт.
// По нему узнаётся ПОВТОР той же реплики; сам текст никуда не идёт.
export function promptHash(prompt) {
  const norm = String(prompt || '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!norm) return '';
  return sha256(norm).slice(0, 16);
}

// Переуказание: Влад повторяет уже данное указание. Признаки — маркеры повтора
// в тексте («ещё раз», «я же сказал», «опять», «повторяю»). Совпадение с
// прежней репликой считает вызывающий код по хешу.
const REINSTRUCT = /(^|[^\p{L}])(ещ[её] раз|я же (сказал|говорил|просил|писал)|опять|снова|повторяю|в который раз|сколько раз)([^\p{L}]|$)/iu;
export function looksLikeReinstruction(prompt) {
  return REINSTRUCT.test(String(prompt || ''));
}

// Стадии хода, повтор которых внутри одного хода — сигнал: показ плана и вопрос.
export const STAGE_TOOLS = new Set(['ExitPlanMode', 'AskUserQuestion']);

// Мутирует ли вызов мир: правящие инструменты, запись в Craft, Bash с
// настоящей (не эфемерной) целью записи или гит-операцией над репозиторием,
// чужой MCP-инструмент без глагола чтения в имени. Тот же признак, которым
// план-гейт отличает чтение от записи.
const READING_VERBS = 'get|list|read|search|fetch|show|describe|resolve|status|view|find|count|check';
const GIT_MUTATION = /\bgit\b[^|;&]*\b(push|commit|merge|rebase|reset|checkout|switch|stash|tag|cherry-pick|am|apply|revert|worktree)\b/;
function mcpReads(name) {
  const op = String(name).replace(/^mcp__.*?__/, '');
  return new RegExp(`^(${READING_VERBS})(_|$)`, 'i').test(op)
    || new RegExp(`_(${READING_VERBS})$`, 'i').test(op);
}
export function looksMutating(tool, input = {}) {
  if (isEdit(tool)) return true;
  if (tool === 'Bash') {
    const cmd = String(input.command || '');
    if (!cmd) return false;
    if (GIT_MUTATION.test(cmd)) return true;
    return bashWriteTargets(cmd).some((raw) => {
      const t = cleanTarget(raw);
      return Boolean(t) && !isEphemeral(t) && !gitEphemeral(t);
    });
  }
  if (/^mcp__/.test(tool)) return !mcpReads(tool);
  return false;
}

// Прогресс хода — удавшийся вызов, который мутирует мир, показ плана, вопрос
// Владу или пуш.
export function isProgress(pre, post) {
  if (!pre || !post || post.error === true) return false;
  return pre.mutates === true || isEdit(pre.tool) || STAGE_TOOLS.has(pre.tool) || pre.push === true;
}

// --- ошибка инструмента в ответе -------------------------------------------------

// Тот же признак, по которому буфер наблюдений пишет tool-error: is_error либо
// непустое поле error в ответе инструмента.
export function responseIsError(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) return false;
  if (response.is_error === true || response.isError === true) return true;
  const err = response.error;
  return err !== undefined && err !== null && err !== false && err !== '';
}
