// Метрики слоя: журнал событий сессии, состояние между событиями и признаки,
// которые из событий вычисляются.
//
// Журнал — JSONL в файле сессии (metricsLog из paths.js): по строке на событие.
// Содержимого правок, команд и промптов в журнале нет — только имена, классы
// исходов, длительности и числа. Текст отказа сюда не пишется: из него
// вычисляется КЛАСС причины (reasonClass), и только он попадает в строку.
//
// Что здесь есть:
// — append/currentMetricsLog/childEnv — куда и чем писать;
// — updateState — правка `<журнал>.state.json` под локом (номер хода, начало
//   хода, вызовы в полёте, смещение прочитанного транскрипта);
// — reasonClass/verdictClass — классы отказа гейта и ответа модели;
// — callHash — хеш вызова, по которому узнаётся «тот же вызов» после отказа;
// — promptHash/looksLikeReinstruction — признаки реплики;
// — turnUsage — токены хода из транскрипта;
// — isProgress — был ли в ходе прогресс, по признакам вызова;
// — readJournal/refreshSummary/writeSummary — сводка сессии и её копия.
//
// Чего здесь НЕТ: имён инструментов и команд. Признаки вызова ставит обёртка
// (universal-metrics.js) через write-targets.js, метку репозитория даёт
// repo-git.js, саму свёртку журнала — metrics-summary.js.
//
// Все функции fail quiet: сломанные метрики не должны трогать ход. Не удалось
// взять лок — правка не делается, и это видно строкой kind: 'skip' в журнале.
import fs from 'node:fs';
import path from 'node:path';
import { metricsLog, sessionId } from './paths.js';
import { withLock, atomicWrite } from './lock.js';
import { sha256 } from './hash.js';
import { eachJsonl } from './jsonl.js';
import { summarize } from './metrics-summary.js';

export function append(file, record) {
  if (!file) return;
  try {
    fs.appendFileSync(file, `${JSON.stringify(record)}\n`);
  } catch { /* журнал не пополнился — метрика потеряна, ход цел */ }
}

// Сессия текущего процесса — ЕДИНСТВЕННАЯ формула на слой: из прочитанного
// события (его кладёт readEvent), иначе из окружения.
export function currentSessionId() {
  const ev = globalThis.hookEvent;
  return (ev && typeof ev.session_id === 'string' && ev.session_id) || sessionId();
}

// Журнал текущего процесса. Переопределение разбирает metricsLog; ни сессии, ни
// переопределения — пустая строка: относить записи не к чему.
export function currentMetricsLog() {
  const sid = currentSessionId();
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
  // Сводка пересобирается ТУТ ЖЕ, если она уже сложена: вызов модели приходит и
  // после последнего Stop хода, и иначе не попал бы ни в одну сводку — ни в эту
  // (её уже написали), ни в следующую (ход мог быть последним). Сводки ещё нет
  // — пересобирать нечего, её сложит ближайший Stop.
  //
  // Доставкой пересобранная сводка НЕ занимается: журнал про хранение не знает,
  // а вернуть её в очередь — дело того края, который знает, что он последний
  // (tools/registry-ingest.mjs). Вызов модели из цепочки хода в очередь не
  // ходит вовсе: за ним придёт Stop этого же хода.
  if (!fs.existsSync(`${log}.summary.json`)) return;
  refreshSummary(log, { sid: currentSessionId() });
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

// Прочитанный хвост транскрипта: полные строки с байтового смещения и новое
// смещение за последней ПОЛНОЙ строкой. Хвост без перевода строки ещё
// дописывается и будет прочитан в следующий раз.
function tailFrom(file, from) {
  let start = from;
  let fd;
  try {
    const size = fs.statSync(file).size;
    // Файл КОРОЧЕ прежнего смещения — это другой транскрипт (сессия начата
    // заново, файл подменён): читаем с начала, иначе смещение никогда уже не
    // сойдётся и токены до конца сессии останутся нулевыми.
    if (size < start) start = 0;
    if (size <= start) return { text: '', offset: start };
    fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    const text = buf.toString('utf8');
    const lastNl = text.lastIndexOf('\n');
    if (lastNl < 0) return { text: '', offset: start };
    const complete = text.slice(0, lastNl + 1);
    return { text: complete, offset: start + Buffer.byteLength(complete, 'utf8') };
  } catch {
    return { text: '', offset: from };
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

// Сумма usage по записям assistant. Один ответ модели лежит в транскрипте
// несколькими записями с одним message.id (по записи на блок содержимого) и
// одним и тем же usage — считается один раз, по последней записи.
function sumUsage(text) {
  const byId = new Map();
  let anon = 0;
  eachJsonl(text, (entry) => {
    if (entry.type !== 'assistant') return;
    const message = entry.message;
    if (!message || !message.usage || typeof message.usage !== 'object') return;
    const id = typeof message.id === 'string' && message.id ? message.id : `anon-${anon += 1}`;
    byId.set(id, message.usage);
  });
  const usage = {
    input: 0, output: 0, cache_read: 0, cache_create: 0, messages: 0,
  };
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
  for (const u of byId.values()) {
    usage.input += num(u.input_tokens);
    usage.output += num(u.output_tokens);
    usage.cache_read += num(u.cache_read_input_tokens);
    usage.cache_create += num(u.cache_creation_input_tokens);
    usage.messages += 1;
  }
  return usage;
}

// Токены хода: сумма usage записей assistant с байтового смещения и смещение
// за последней полной строкой — его хранит состояние и передаёт следующий раз.
export function turnUsage(transcript, from = 0) {
  if (!transcript) return { usage: sumUsage(''), offset: from };
  const { text, offset } = tailFrom(transcript, from);
  return { usage: sumUsage(text), offset };
}

// --- сводка сессии -------------------------------------------------------------

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

// Значение в устойчивом виде: ключи сортируются на КАЖДОМ уровне. Порядок полей
// смысла не несёт, а один и тот же вызов обязан давать один хеш — на нём стоит
// узнавание повтора после отказа. Порядок элементов массива, наоборот, значим.
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
const REINSTRUCT = /(^|[^\p{L}])(я (же |уже )?(просил|говорил|сказал|писал)а?|повторяю|сколько раз (можно|повторять|говорить)|русским языком)([^\p{L}]|$)/iu;
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
