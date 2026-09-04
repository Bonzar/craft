// Каноническое событие хука: та форма, в которой его видит ОБЩАЯ часть слоя.
// Про харнес здесь не знают ничего — ни его переменных, ни его полей, ни имён
// его событий. Сырое событие приводит к этой форме ОБЁРТКА (для Claude Code —
// lib/event-claude.js), она же несёт таблицу отображения имён событий.
//
// ЯДРО — то, что даёт ЛЮБАЯ обёртка:
//   harness     — чем породило событие;
//   session_id  — сессия; пусто, если харнес её не даёт;
//   call_id     — идентификатор вызова инструмента; пусто вне вызова;
//   event       — имя события ИЗ ЭТОГО файла (EVENTS), а не из харнеса;
//   tool        — имя вызванного инструмента; пусто вне вызова;
//   input       — вход вызова простым объектом;
//   cwd         — рабочий каталог сессии;
//   state_dir   — каталог, в котором хуки держат состояние и разговаривают друг
//                 с другом (журнал решений, метки, локи).
//
// ФАКТЫ — то, чего обёртка может не дать:
//   journal — путь к журналу событий сессии;
//   tokens  — токены хода данными: {input, output, cache_read, cache_create, messages}.
//
// Хук объявляет нужные ему факты списком и спрашивает missingFact. Факта нет —
// ответ `unsupported` С ИМЕНЕМ ФАКТА, а не молчание и не обходной путь
// (решение 8).
export const EVENTS = Object.freeze({
  SESSION_START: 'session-start',
  PROMPT: 'prompt',
  PRE_TOOL: 'pre-tool',
  POST_TOOL: 'post-tool',
  POST_TOOL_FAILURE: 'post-tool-failure',
  STOP: 'stop',
  SUBAGENT_STOP: 'subagent-stop',
  PRE_COMPACT: 'pre-compact',
  SESSION_END: 'session-end',
  NOTIFICATION: 'notification',
});

const EVENT_NAMES = new Set(Object.values(EVENTS));

export const CORE_FIELDS = Object.freeze([
  'harness', 'session_id', 'call_id', 'event', 'tool', 'input', 'cwd', 'state_dir',
]);
export const FACTS = Object.freeze(['journal', 'tokens']);

const str = (v) => (typeof v === 'string' ? v : '');
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

// Ядро из того, что собрала обёртка. НЕИЗВЕСТНОЕ имя события даёт пустое имя, а
// не догадку: хук, подписанный на своё событие, тогда просто не сработает, тогда
// как догадка запустила бы его не на том событии.
export function coreEvent(parts = {}) {
  const name = str(parts.event);
  return {
    harness: str(parts.harness),
    session_id: str(parts.session_id),
    call_id: str(parts.call_id),
    event: EVENT_NAMES.has(name) ? name : '',
    tool: str(parts.tool),
    input: obj(parts.input),
    cwd: str(parts.cwd),
    state_dir: str(parts.state_dir),
  };
}

// Факт ДАН, когда ключ есть и не пуст. Ноль токенов — факт; отсутствие ключа —
// нет. Поэтому смотрится наличие значения, а не его истинность.
export function hasFact(event, fact) {
  if (!event || typeof event !== 'object') return false;
  const value = event[fact];
  if (value === undefined || value === null) return false;
  return typeof value !== 'string' || value !== '';
}

// Первое из объявленных, чего нет. Пустая строка — все факты на месте.
export function missingFact(event, requires = []) {
  for (const fact of requires) {
    if (!hasFact(event, fact)) return fact;
  }
  return '';
}

// Непокрытое называется явно и одинаково: имя возможности или факта, которого не
// хватило. Это не ошибка и не молчание, а ОТВЕТ (решение 8).
export function unsupported(capability) {
  return { status: 'unsupported', capability: String(capability || '') };
}
