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
import { withLock, atomicWrite } from './lock.js';

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
// Возвращает то, что вернуло действие.
export function updateState(log, run) {
  return withLock(stateFile(log), () => {
    const state = loadState(log);
    const out = run(state);
    atomicWrite(stateFile(log), JSON.stringify(state));
    return out;
  });
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
  const hadScheme = /^[a-z+]+:\/\//i.test(s);
  s = s.replace(/^[a-z+]+:\/\//i, '');       // схема
  s = s.replace(/^[^@/]+@/, '');            // учётка перед хостом
  // scp-форма host:owner/repo — только там, где схемы НЕ было: с ней двоеточие
  // отделяет порт, и «github.com:443/a/b» превращалось в «github.com/443/a/b»,
  // то есть выдуманный владелец у каждого self-hosted remote на своём порту.
  if (!hadScheme) s = s.replace(/^([^:/]+):(?!\/)/, '$1/');
  else s = s.replace(/^([^:/]+):\d+\//, '$1/'); // порт из адреса выбрасывается
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

// Размер транскрипта на сейчас; нет файла — ноль.
export function transcriptSize(file) {
  if (!file) return 0;
  try {
    return fs.statSync(file).size;
  } catch {
    return 0;
  }
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
