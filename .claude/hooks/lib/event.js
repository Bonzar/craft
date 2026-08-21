// Событие хука: харнесс подаёт его JSON-ом на stdin.
//
// Fail open на всём неожиданном — как у bash-версий, где `jq -r '… // ""'`
// возвращал пустую строку и на битом входе: сломанный хук не должен клинить
// работу, поэтому пустое или неразборное событие даёт пустые поля, а не падение.
import { readFileSync } from 'node:fs';

export function readEvent() {
  let raw = '';
  try {
    raw = readFileSync(0, 'utf8');
  } catch {
    raw = '';
  }

  let event = {};
  try {
    if (raw.trim() !== '') event = JSON.parse(raw);
  } catch {
    event = {};
  }
  if (event === null || typeof event !== 'object') event = {};

  return {
    // Сырой текст нужен там, где ключ считается по всему событию целиком
    // (уступка второму вызову), — пересборка JSON дала бы другой хеш.
    raw,
    event,
    tool: event.tool_name || '',
    // Имя события приходит не всегда: у файлов с двумя ролями (гвард на показе
    // плана и запись после одобрения) значение по умолчанию задаёт сам хук.
    name: event.hook_event_name || '',
    cwd: event.cwd || '',
    mode: event.permission_mode || '',
    prompt: event.prompt || '',
    transcript: event.transcript_path || '',
    input: event.tool_input && typeof event.tool_input === 'object' ? event.tool_input : {},
    response: event.tool_response,
  };
}
