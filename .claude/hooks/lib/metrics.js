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
import { metricsLog, sessionId } from './paths.js';
import { withLock, atomicWrite } from './lock.js';
import { sha256 } from './hash.js';

export function append(file, record) {
  if (!file) return;
  try {
    fs.appendFileSync(file, `${JSON.stringify(record)}\n`);
  } catch { /* журнал не пополнился — метрика потеряна, ход цел */ }
}

// Журнал текущего процесса — ЕДИНСТВЕННАЯ формула резолва на весь слой: сессия
// берётся из прочитанного события (его кладёт readEvent), иначе из окружения;
// переопределение разбирает metricsLog. Ни сессии, ни переопределения — пустая
// строка: относить записи не к чему.
export function currentMetricsLog() {
  const ev = globalThis.hookEvent;
  const sid = (ev && typeof ev.session_id === 'string' && ev.session_id) || sessionId();
  if (!sid && !process.env.CRAFT_METRICS_LOG) return '';
  return metricsLog(sid);
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
  // Сводка пересобирается ТУТ ЖЕ, если она уже сложена: фоновый приём кончается
  // позже последнего Stop хода, и его вызов модели иначе не попал бы ни в одну
  // сводку — ни в эту (её уже написали), ни в следующую (ход мог быть
  // последним). Сводки ещё нет — пересобирать нечего, её сложит ближайший Stop.
  if (fs.existsSync(`${log}.summary.json`)) refreshSummary(log, { sid: sessionId() });
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
    let text;
    try {
      text = fs.readFileSync(settings, 'utf8');
    } catch { probe = path.dirname(probe); continue; }
    return registersDispatcher(text);
  }
  return false;
}

// Проектный диспетчер опознаётся по РЕГИСТРАЦИИ, а не по слову в файле: имя
// dispatch.js не наше, и в чужом проекте оно встречается своим скриптом. По
// подстроке такой проект считался бы ведущим полную цепочку, и метрики его
// сессий не писал бы никто.
function registersDispatcher(text) {
  let hooks;
  try {
    hooks = JSON.parse(text).hooks;
  } catch { return false; }
  if (!hooks || typeof hooks !== 'object') return false;
  for (const groups of Object.values(hooks)) {
    if (!Array.isArray(groups)) continue;
    for (const group of groups) {
      const list = group && Array.isArray(group.hooks) ? group.hooks : [];
      for (const hook of list) {
        const cmd = hook && typeof hook.command === 'string' ? hook.command : '';
        if (cmd.split(/\s+/).some((w) => w.replace(/^["']|["']$/g, '').endsWith('/.claude/hooks/dispatch.js'))) {
          return true;
        }
      }
    }
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

// «Прочитал — поправил — записал» под локом и одной атомарной записью.
//
// Без лока параллельные вызовы инструментов (харнесс шлёт их пачкой) читают
// одно состояние и затирают правки друг друга: из восьми вызовов в полёте
// выживает три, и у остальных потом нет длительности. Без атомарной записи
// сосед успевает прочитать пустой файл посреди записи — и это хуже потери
// записи: обнуляются номер хода и смещение транскрипта, то есть следующий Stop
// пересчитывает весь транскрипт заново.
//
// Ждать лок долго НЕЛЬЗЯ: хук стоит в цепочке хода, и метрика не стоит того,
// чтобы её ждал Влад. Не дождались — состояние не трогаем, а в журнал уходит
// строка kind: 'skip': пропуск обязан быть виден в сводке, иначе он выглядит
// как ход, которого не было.
//
// Возвращает то, что вернуло действие, либо undefined, когда лок не достался.
export const STATE_WAIT_MS = 300;

export function updateState(log, run) {
  const { locked, value } = withLock(stateFile(log), () => {
    const state = loadState(log);
    const out = run(state);
    atomicWrite(stateFile(log), JSON.stringify(state));
    return out;
  }, { waitMs: STATE_WAIT_MS });
  if (!locked) {
    append(log, { kind: 'skip', ts: new Date().toISOString(), what: 'state', wait_ms: STATE_WAIT_MS });
    return undefined;
  }
  return value;
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
  let start = from;
  try {
    const size = fs.statSync(transcript).size;
    // Файл КОРОЧЕ прежнего смещения — это другой транскрипт (сессия начата
    // заново, файл подменён): читаем с начала, иначе смещение никогда уже не
    // сойдётся и токены до конца сессии останутся нулевыми.
    if (size < start) start = 0;
    if (size <= start) return { usage: empty, offset: start };
    fd = fs.openSync(transcript, 'r');
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    text = buf.toString('utf8');
  } catch {
    return { usage: empty, offset: from };
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
  const lastNl = text.lastIndexOf('\n');
  if (lastNl < 0) return { usage: empty, offset: start };
  const complete = text.slice(0, lastNl + 1);
  const offset = start + Buffer.byteLength(complete, 'utf8');

  const byId = new Map();
  let anon = 0;
  eachJsonl(complete, (entry) => {
    if (entry.type !== 'assistant') return;
    const message = entry.message;
    if (!message || !message.usage || typeof message.usage !== 'object') return;
    const id = typeof message.id === 'string' && message.id ? message.id : `anon-${anon += 1}`;
    byId.set(id, message.usage);
  });
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

// Разбор JSONL: по объекту на строку, битая строка пропускается. Общий на весь
// модуль — его же правилам подчиняются и записи журнала, и записи транскрипта.
export function eachJsonl(text, fn) {
  for (const line of String(text).split('\n')) {
    if (!line.trim()) continue;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    if (entry && typeof entry === 'object') fn(entry, line);
  }
}

// Записи журнала без строк сводки.
//
// Возвращает null, когда журнал НЕ ПРОЧИТАЛСЯ, и [] — когда его просто нет.
// Разница существенная: на пустом списке сводка выходит нулевой, а ею
// перезаписывается копия, которую увозит хранение, — то есть разовый сбой
// чтения стирал бы живую сессию.
export function readJournal(log) {
  let text = '';
  try {
    text = fs.readFileSync(log, 'utf8');
  } catch (err) {
    return err && err.code === 'ENOENT' ? [] : null;
  }
  const out = [];
  eachJsonl(text, (rec, line) => {
    // Строки сводки отсеиваются ДО разбора там, где это видно по началу строки:
    // их в журнале накапливается по одной на Stop, и разбирать их заново на
    // каждой сводке — самая дорогая часть чтения.
    if (line.startsWith('{"kind":"summary"')) return;
    if (rec.kind !== 'summary') out.push(rec);
  });
  return out;
}

// Хеш вызова: инструмент и СМЫСЛОВАЯ часть входа. Служебные поля (описание
// команды, таймаут, фоновый режим) в хеш не идут — модель переписывает их при
// повторе, и «тот же вызов» переставал узнаваться, то есть ложный отказ гейта
// не засчитывался. Ключи сортируются: порядок полей во входе не обещан.
const VOLATILE_INPUT = new Set(['description', 'timeout', 'run_in_background', 'shell_id']);

export function callHash(tool, input) {
  const src = input && typeof input === 'object' ? input : {};
  const semantic = Object.keys(src)
    .filter((k) => !VOLATILE_INPUT.has(k))
    .sort()
    .map((k) => `${k}=${canonical(src[k])}`)
    .join('\n');
  return sha256(`${tool}\n${semantic}`).slice(0, 16);
}

// Значение в устойчивом виде: ключи сортируются на КАЖДОМ уровне, а не только
// на верхнем. Порядок полей смысла не несёт, и на вложенных объектах (правки
// MultiEdit, ячейки NotebookEdit) один и тот же вызов давал разные хеши — то
// есть повтор после отказа не узнавался и ложный отказ не засчитывался.
// Порядок элементов массива, наоборот, значим и сохраняется.
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function writeSummary(log, summary) {
  try {
    fs.writeFileSync(`${log}.summary.json`, `${JSON.stringify(summary)}\n`);
  } catch { /* копия сводки не легла — в журнале она есть */ }
}

// Пересборка сводки по журналу — под локом сводки, чтобы читающий журнал и
// пишущий копию не разъезжались с параллельным вызовом.
//
// Зовётся с конца хода (record: true — тогда сводка ещё и ложится строкой в
// журнал) и из ФОНОВОГО вызова модели. Второе обязательно: приём реестра уходит
// отдельным процессом, его запись «model» приходит уже после того, как сводка
// сложена, и без пересборки хранение увозило бы сессию с недосчитанными
// вызовами модели и их временем.
//
// Журнал НЕ ПРОЧИТАЛСЯ (это не то же, что «пуст») — сводку не трогаем: нулевая
// затёрла бы хорошую.
export function refreshSummary(log, { sid = '', now = Date.now(), record = false } = {}) {
  if (!log) return null;
  const { locked, value } = withLock(`${log}.summary.json`, () => {
    const records = readJournal(log);
    if (!records) return null;
    const ts = new Date(now).toISOString();
    const summary = summarize(records, { sid, now });
    if (record) append(log, { kind: 'summary', ts, ...summary });
    writeSummary(log, { ts, ...summary });
    return summary;
  }, { waitMs: STATE_WAIT_MS });
  if (!locked) {
    append(log, { kind: 'skip', ts: new Date(now).toISOString(), what: 'summary', wait_ms: STATE_WAIT_MS });
    return null;
  }
  return value;
}

// Скиллы разбора инцидента: код-сессия и сессия над базой Craft. Список точный,
// потому что признак «скилл вызван» — это доля разборов, а не похожие имена.
const INCIDENT_SKILLS = new Set(['code-incident', 'craft-incident']);

const ms = Date.parse;

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
      // Повтор реплики и словесный маркер переуказания — РАЗНЫЕ признаки: первый
      // ловит дословно ту же реплику, второй — обращение «я же просил». В одном
      // счётчике они складывались, и по сводке нельзя было сказать, чего именно
      // было больше.
      prompt_repeats: 0, reinstructions: 0,
      call_repeats: 0, stage_repeats: 0, turns_without_progress: 0, error_streak_max: 0,
    },
  };
  let streak = 0;
  // Ход без прогресса считается по ХОДУ, а не по записи Stop: у одного хода
  // записей Stop бывает несколько, и решает последняя.
  const noProgressByTurn = new Map();
  // Ходы — по числу РАЗЛИЧНЫХ номеров, а не по максимуму: максимум считал ходы,
  // которых в журнале нет (сессия, возобновлённая с чужим номером), и не считал
  // пропуски. Нулевой ход ходом не является: реплики ещё не было, это Stop
  // служебного вызова до начала разговора.
  const turnsSeen = new Set();
  let started = NaN;
  let firstTurn = null;
  const pending = new Map(); // hash → { unlockTurn }
  const pres = new Map();    // id → pre-запись
  const incidentTurns = new Set();
  const skillTurns = new Set();

  // Реплика или ответ кнопкой снимают замок с ОТЛОЖЕННЫХ отказов, и снимают его
  // КАЖДЫЙ РАЗ заново: между отказом и повтором Влад успевает и ответить кнопкой,
  // и написать реплику, а замок, снятый однажды и потом выброшенный, терял
  // ровно тот случай, ради которого признак заведён.
  //
  // Из ожидания отказ уходит по СВОЕМУ возрасту, а не по возрасту снятия:
  // «сразу после реплики» — это ход отказа или следующий за ним, дальше это уже
  // новая работа, и правильный отказ, снятый через пять ходов новым планом,
  // ложным не считается.
  const DENY_WINDOW_TURNS = 1;
  const unlock = (turn) => {
    for (const [hash, p] of pending) {
      if (turn - p.denyTurn > DENY_WINDOW_TURNS) pending.delete(hash);
      else p.unlockTurn = turn;
    }
  };

  for (const r of records) {
    const t = ms(r.ts);
    if (!Number.isFinite(started) && Number.isFinite(t)) started = t;
    if (Number.isFinite(r.turn) && r.turn > 0) turnsSeen.add(r.turn);

    if (r.kind === 'session') {
      // Стартов бывает несколько (компакт, возобновление): начало сессии —
      // самая ранняя запись, поздние старты его не двигают.
      s.harness = r.harness || s.harness;
      s.repo = r.repo || s.repo;
      s.sid = s.sid || r.sid || '';
    } else if (r.kind === 'prompt') {
      unlock(r.turn);
      if (r.incident === true) incidentTurns.add(r.turn);
      if (r.repeat === true) s.signals.prompt_repeats += 1;
      if (r.reinstruct === true) s.signals.reinstructions += 1;
      // Серия ошибок — про то, как агент бьётся ВНУТРИ хода: реплика Влада её
      // разрывает, иначе ошибки по обе стороны его вмешательства сложились бы
      // в одну серию, которой не было.
      streak = 0;
    } else if (r.kind === 'pre') {
      if (r.id) pres.set(r.id, r);
      if (r.decision === 'deny') {
        s.denies.total += 1;
        const cls = r.class || 'unknown';
        s.denies.by_class[cls] = (s.denies.by_class[cls] || 0) + 1;
        if (r.h) pending.set(r.h, { unlockTurn: null, denyTurn: r.turn });
        if (r.plan === true) s.plan.bounced += 1;
      } else if (r.decision === 'allow') {
        if (r.plan === true) s.plan.shown += 1;
        if (r.h && pending.has(r.h)) {
          const p = pending.get(r.h);
          if (p.unlockTurn !== null && p.unlockTurn === r.turn) s.false_denies += 1;
          pending.delete(r.h);
        }
      }
      if (r.repeat_call === true) s.signals.call_repeats += 1;
      if (r.stage_repeat === true) s.signals.stage_repeats += 1;
    } else if (r.kind === 'post' || r.kind === 'fail') {
      const pre = r.id ? pres.get(r.id) : null;
      const failed = r.kind === 'fail' || r.error === true;
      if (failed) s.tool_errors += 1;
      streak = failed ? streak + 1 : 0;
      if (streak > s.signals.error_streak_max) s.signals.error_streak_max = streak;
      if (!failed && pre && pre.question === true) unlock(r.turn);
      // Скилл разбора засчитывается только УСПЕШНЫМ вызовом и по точному имени:
      // отказанный вызов разбора не делает, а подстрока incident ловила и
      // соседние скиллы, и сводка говорила, что разбор был, когда его не было.
      if (!failed && pre && INCIDENT_SKILLS.has(String(pre.skill || ''))) skillTurns.add(r.turn);
      if (!failed && pre && pre.plan === true) s.plan.approved += 1;
      if (!failed && pre && pre.craft_write === true) s.outcome.craft_writes += 1;
      if (!failed && pre && pre.edit === true && s.first_edit_ms === null
          && Number.isFinite(t) && Number.isFinite(started)) s.first_edit_ms = t - started;
      if (!failed && pre && pre.push === true) s.outcome.pushed = true;
    } else if (r.kind === 'stop') {
      if (r.blocked_by) s.stop_blocks[r.blocked_by] = (s.stop_blocks[r.blocked_by] || 0) + 1;
      // Нулевой ход в счёт не идёт: до первой реплики Влада хода не было, а
      // Stop служебного вызова давал «ход без прогресса», которого не случалось.
      if (typeof r.no_progress === 'boolean' && r.turn > 0) noProgressByTurn.set(r.turn, r.no_progress);
      const u = r.usage && typeof r.usage === 'object' ? r.usage : {};
      for (const key of Object.keys(s.tokens)) s.tokens[key] += Number(u[key]) || 0;
      // Токены первого хода — сумма ВСЕХ его Stop: заблокированный конец хода
      // даёт второй Stop с тем же номером и своей долей usage.
      if (s.tokens_first_turn === null) {
        firstTurn = r.turn;
        s.tokens_first_turn = { input: 0, output: 0, cache_read: 0, cache_create: 0 };
      }
      if (firstTurn === r.turn) {
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

  s.turns = turnsSeen.size;
  s.signals.turns_without_progress = [...noProgressByTurn.values()].filter(Boolean).length;
  s.incidents.detected = incidentTurns.size;
  s.incidents.skill_called = [...incidentTurns].filter((turn) => skillTurns.has(turn)).length;
  s.incidents.share = incidentTurns.size ? s.incidents.skill_called / incidentTurns.size : null;
  s.started_at = Number.isFinite(started) ? new Date(started).toISOString() : '';
  return s;
}

// Размер транскрипта на сейчас; нет файла — ноль.
export function transcriptSize(file) {
  if (!file) return 0;
  try {
    return fs.statSync(file).size;
  } catch {
    return 0;
  }
}

// --- сигналы -------------------------------------------------------------------

// Ниже порога реплика в узнавание повторов не идёт: «ок», «да», «продолжай» —
// обычные подтверждения, и второе такое же за сессию не означает, что Влад
// повторяет указание. Пустой хеш выключает и признак повтора, и запоминание.
export const REPEAT_MIN_CHARS = 12;

// Нормализованный хеш реплики: регистр, пробелы и знаки препинания не в счёт.
// По нему узнаётся ПОВТОР той же реплики; сам текст никуда не идёт.
export function promptHash(prompt) {
  const norm = String(prompt || '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (norm.length < REPEAT_MIN_CHARS) return '';
  return sha256(norm).slice(0, 16);
}

// Переуказание: Влад повторяет уже данное указание. Это ЭВРИСТИКА по словарю —
// она предупреждает, а не доказывает, и словарь растёт по живым сводкам.
//
// В словаре только формулы, ОБРАЩЁННЫЕ К АГЕНТУ: «я же просил», «повторяю».
// Одиночные усилители («опять», «снова», «ещё раз», «в который раз») из него
// убраны — они куда чаще про мир, чем про указание: «запусти тесты ещё раз» и
// «в который раз упал CI» переуказаниями не являются, а признак на них
// срабатывал.
const REINSTRUCT = /(^|[^\p{L}])(я же (сказал|говорил|просил|писал|просила)|я (просил|говорил|сказал) (же |уже )|повторяю|сколько раз (можно|повторять|говорить)|русским языком)([^\p{L}]|$)/iu;
// Вежливая формула целиком, вместе с прилипшим к ней усилителем («ещё раз
// спасибо», «снова здравствуйте»): усилитель тут часть оборота, а не указания.
const COURTESY = /(?:(ещ[её] раз|снова|опять|вновь)\s+)?(спасибо\p{L}*|благодар\p{L}*|здравствуй\p{L}*|привет\p{L}*|добр(ый|ое)\s+(день|вечер|утро))(?:\s+(ещ[её] раз|снова|вновь))?/giu;

export function looksLikeReinstruction(prompt) {
  // Вежливость вычёркивается, а не гасит реплику целиком: «спасибо, но я же
  // просил не трогать README» — это поправка с вежливым зачином, и признак
  // считается по остатку. Чисто вежливая реплика остатка не оставляет.
  const text = String(prompt || '').replace(COURTESY, ' ');
  return REINSTRUCT.test(text);
}

// Прогресс хода — удавшийся вызов, который менял мир, показывал план, спрашивал
// Влада или отправлял сделанное. Признаки ставит обёртка на PreToolUse: на
// PostToolUse входа вызова уже нет.
export function isProgress(pre, post) {
  if (!pre || !post || post.error === true) return false;
  return pre.mutates === true || pre.stage === true || pre.push === true;
}
