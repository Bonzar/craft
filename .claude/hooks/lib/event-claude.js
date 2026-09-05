// Обёртка события Claude Code: сырое событие с stdin → каноническое ядро
// (lib/event.js). ЗДЕСЬ и только здесь живут имена полей этого харнеса, имена
// его событий и его переменные окружения; общая часть слоя их не видит.
//
// Отдаёт ядро (harness, session_id, call_id, event, tool, input, cwd, state_dir)
// и сверх него — то, чем сегодня пользуются САМИ ОБЁРТКИ (хуки universal-* и
// craft-*): сырой текст события, разобранный объект, имя события харнеса, ответ
// инструмента, реплику, путь транскрипта, режим разрешений. Хуки — это тоже
// сторона харнеса, им знать его форму законно; в `lib/` эти поля не уходят.
//
// Fail open на всём неожиданном: пустое или неразборное событие даёт пустые
// поля, а не падение. Сломанный хук не должен клинить работу.
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { coreEvent, EVENTS } from './event.js';
import { stateDir, journalLog } from './paths.js';

// Таблица отображения имён событий харнеса в свои. Единственное место, где имена
// событий Claude Code вообще упоминаются.
const EVENT_BY_HARNESS = new Map([
  ['SessionStart', EVENTS.SESSION_START],
  ['UserPromptSubmit', EVENTS.PROMPT],
  ['PreToolUse', EVENTS.PRE_TOOL],
  ['PostToolUse', EVENTS.POST_TOOL],
  ['PostToolUseFailure', EVENTS.POST_TOOL_FAILURE],
  ['Stop', EVENTS.STOP],
  ['SubagentStop', EVENTS.SUBAGENT_STOP],
  ['PreCompact', EVENTS.PRE_COMPACT],
  ['SessionEnd', EVENTS.SESSION_END],
  ['Notification', EVENTS.NOTIFICATION],
]);

// Обратная таблица: своё имя события → имя харнеса. Нужна там, где харнес сверяет
// имя в ОТВЕТЕ хука с тем событием, на которое хук подписан (см. decide-claude).
const HARNESS_BY_EVENT = new Map([...EVENT_BY_HARNESS].map(([k, v]) => [v, k]));

export function harnessEventName(event) {
  return HARNESS_BY_EVENT.get(event) || '';
}

// Номер ПОЯВЛЕНИЯ события. Один процесс обслуживает ровно одно появление: под
// диспетчером — всю его цепочку, поштучно — один хук. Поэтому номер годится в
// ключ канала между хуками цепочки, и им же строки текущего появления отделяются
// от строк прошлых ходов: у событий без идентификатора вызова (реплика, конец
// хода) больше отличить их нечем.
const OCCURRENCE = randomUUID();

// Прочитанный текст запоминается: под диспетчером одно событие читают несколько
// хуков подряд, а поток входа отдаёт его лишь однажды — второй хук получил бы
// пустоту и молча ничего не сделал.
let cached = null;

function readRaw() {
  if (cached === null) {
    try {
      cached = readFileSync(0, 'utf8');
    } catch {
      cached = '';
    }
  }
  return cached;
}

export function readEvent() {
  const raw = readRaw();

  let event = {};
  try {
    if (raw.trim() !== '') event = JSON.parse(raw);
  } catch {
    event = {};
  }
  if (event === null || typeof event !== 'object') event = {};

  const harnessName = typeof event.hook_event_name === 'string' ? event.hook_event_name : '';
  const core = coreEvent({
    harness: process.env.CRAFT_HARNESS || 'claude',
    session_id: event.session_id || process.env.CLAUDE_CODE_SESSION_ID || '',
    call_id: typeof event.tool_use_id === 'string' ? event.tool_use_id : '',
    event: EVENT_BY_HARNESS.get(harnessName) || '',
    tool: event.tool_name || '',
    input: event.tool_input,
    cwd: event.cwd || '',
    state_dir: stateDir(),
  });

  // Сессия — под своим именем в окружение: пути состояния резолвятся по ней
  // (paths.sessionId), а имя переменной ЭТОГО харнеса общая часть знать не должна.
  // Здесь — единственный переход от события к тому, чем пользуется весь слой.
  if (core.session_id) process.env.CRAFT_SESSION_ID = core.session_id;

  return {
    ...core,
    // ФАКТ `journal` — путь к журналу событий сессии. Считается ПОСЛЕ того, как
    // сессия легла в окружение строкой выше: путь резолвится по ней, и до неё
    // он был бы пустым у всякого события. Сессии нет — пути нет, и факта нет:
    // хук, объявивший его, ответит `unsupported` с именем, а не тихо пройдёт.
    journal: journalLog(),
    // Ядро отдельным ключом: его передают туда, где нужно именно оно (уступка
    // второму вызову, журнал решений), — плоская россыпь там была бы россыпью
    // лишних полей.
    core,
    // Номер появления события: им канал между хуками отделяет строки ЭТОГО
    // появления от строк прошлых ходов.
    occurrence: OCCURRENCE,
    // Ниже — сторона харнеса. Её читают только обёртки.
    raw,
    harness_event: harnessName,
    response: event.tool_response,
    prompt: event.prompt || '',
    transcript: event.transcript_path || '',
    mode: event.permission_mode || '',
    source: typeof event.source === 'string' ? event.source : '',
    // Конец хода, вызванный самим стоп-хуком: на нём стоп-хуки молчат, иначе
    // получается цикл. Признак этого харнеса, поэтому имя поля живёт здесь.
    stop_active: event.stop_hook_active === true,
  };
}

// Тело ошибки из ответа. ФОРМА ОТВЕТА — форма харнеса, и разбирать её обязана
// обёртка: хук, читающий `is_error`/`content` своими руками, привязан к Claude
// ровно так же, как если бы это лежало в общей части, — только гвард имён туда не
// смотрит, и привязка становится невидимой.
//
// Форма разбирается НЕЗАВИСИМО от предиката ниже, и это существенно. Предикат
// отвечает на вопрос «есть ли в ответе ПРИЗНАК ошибки» и по построению ложен для
// строки, массива блоков и объекта без своих полей. Но провал бывает объявлен
// САМИМ СОБЫТИЕМ, и тогда тело лежит в ответе любой из этих форм: сцепив текст с
// предикатом, мы выбрасывали бы его у самой частой формы провала и клали в журнал
// «тела нет» при теле в руках.
//
// Пустая строка значит «тела нет»; «была ли ошибка» отвечает предикат, а не длина
// этого текста.
export function errorText(response) {
  if (response === undefined || response === null || response === false) return '';
  if (typeof response === 'string') return response;
  // Ответ блоками: у инструмента их бывает несколько, текст лежит в каждом.
  if (Array.isArray(response)) {
    return response
      .map((block) => (block && typeof block === 'object' ? errorText(block.text ?? block.content) : ''))
      .filter(Boolean)
      .join('\n');
  }
  if (typeof response !== 'object') return String(response);
  const body = response.content ?? response.error ?? '';
  if (body === undefined || body === null || body === false || body === '') return '';
  return typeof body === 'string' ? body : errorText(body) || JSON.stringify(body);
}

// Ошибка инструмента в ответе: is_error либо непустое поле error. Форма ответа —
// тоже форма харнеса, поэтому предикат живёт здесь, а общая часть получает от
// обёртки готовое булево.
export function responseIsError(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) return false;
  if (response.is_error === true || response.isError === true) return true;
  const err = response.error;
  return err !== undefined && err !== null && err !== false && err !== '';
}
