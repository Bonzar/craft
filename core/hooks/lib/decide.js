import { createPlanRequired } from '../../contracts/plan-required.mjs';

// Provider-neutral decisions. Adapters translate these values into the native
// hook wire protocol; core never emits harness fields.
function emit(payload, { compact = true, terminal = true } = {}) {
  const text = compact ? JSON.stringify(payload) : JSON.stringify(payload, null, 2);
  process.stdout.write(`${text}\n`);
  // Признак «решение принято» для диспетчера: под ним хуки одного события делят
  // общий вывод, и второе решение подряд легло бы в него следом за первым.
  if (terminal) globalThis.hookDecided = true;
}

// PreToolUse: запрет вызова с причиной, которую прочитает модель.
export function deny(reason, options) {
  emit({ type: 'deny', reason }, options);
  process.exit(0);
}

// PreToolUse: вызов уходит человеку на подтверждение — сильнее запрета, потому
// что требует живого решения, а не повторной попытки агента.
export function ask(reason) {
  emit({ type: 'ask', reason });
  process.exit(0);
}

// Stop: ход не заканчивается, агенту возвращается причина.
export function block(reason) {
  emit({ type: 'block', reason });
  process.exit(0);
}

// Текст, дописываемый в контекст агента. Имя события идёт параметром: один и тот
// же приём работает на старте сессии, на реплике, после вызова и после его
// провала, а харнесс сверяет имя с тем событием, на которое хук подписан.
export function inject(hookEventName, additionalContext, options) {
  emit({ type: 'context', event: hookEventName, content: additionalContext }, options);
  process.exit(0);
}

// User-visible informational event. It does not decide the tool call and must
// never stop the remaining policy hooks in the dispatcher.
export function notify(message, level = 'warning') {
  emit({ type: 'notice', level, message }, { terminal: false });
  process.exit(0);
}

// The original action remains denied. An orchestration-capable adapter may end
// the turn and start a new one in its native planning capability.
export function planRequired(reason, resume) {
  emit(createPlanRequired(reason, resume));
  process.exit(0);
}

// Проход. Гварды на проходе молчат — пустой stdout и есть «разрешено».
export function allow() {
  process.exit(0);
}
