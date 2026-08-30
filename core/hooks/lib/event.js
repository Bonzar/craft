// Canonical event envelope: an adapter translates its harness protocol before
// core sees the event. Native event fields and tool names are forbidden here.
//
// Fail open на всём неожиданном — как у bash-версий, где `jq -r '… // ""'`
// возвращал пустую строку и на битом входе: сломанный хук не должен клинить
// работу, поэтому пустое или неразборное событие даёт пустые поля, а не падение.
import { readFileSync } from 'node:fs';

// Прочитанный текст запоминается: под диспетчером одно событие читают несколько
// хуков подряд, а поток входа отдаёт его лишь однажды — второй хук получил бы
// пустоту и молча ничего не сделал.
let cached = null;

export function readEvent() {
  if (cached === null) {
    try {
      cached = readFileSync(0, 'utf8');
    } catch {
      cached = '';
    }
  }
  const raw = cached;

  let event = {};
  try {
    if (raw.trim() !== '') event = JSON.parse(raw);
  } catch {
    event = {};
  }
  if (event === null || typeof event !== 'object') event = {};

  const meta = event.event && typeof event.event === 'object' ? event.event : {};
  const action = event.action && typeof event.action === 'object'
    ? event.action
    : { route: 'unknown', payload: { raw: {} } };
  const input = action.payload && typeof action.payload === 'object' ? action.payload : {};

  return {
    // Сырой текст нужен там, где ключ считается по всему событию целиком
    // (уступка второму вызову), — пересборка JSON дала бы другой хеш.
    raw,
    event: meta,
    action,
    route: action.route || 'unknown',
    // Имя события приходит не всегда: у файлов с двумя ролями (гвард на показе
    // плана и запись после одобрения) значение по умолчанию задаёт сам хук.
    name: meta.name || '',
    cwd: meta.cwd || '',
    mode: meta.mode || '',
    prompt: meta.prompt || '',
    transcript: meta.transcript || '',
    assistantTurnText: meta.assistantTurnText || '',
    sessionEditedFiles: Array.isArray(meta.sessionEditedFiles) ? meta.sessionEditedFiles : [],
    input,
    outcome: event.outcome && typeof event.outcome === 'object'
      ? event.outcome
      : { status: 'unknown', error: '', result: event.response },
    response: event.outcome && typeof event.outcome === 'object' ? event.outcome.result : event.response,
  };
}
