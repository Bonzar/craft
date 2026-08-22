// Решения хука. Форма ответа задана харнессом и повторяет то, что печатали
// bash-версии через `jq -cn`: компактный JSON одной строкой плюс перевод строки.
//
// Совпадение до байта здесь не косметика: пока идёт переезд, дифференциальная
// сверка гоняет обе версии хука на одном входе и валит кейс на любом различии
// вывода. Порядок ключей в объектах ниже — тот же, в котором их собирал jq.

// Форм у ответа две, потому что bash-версии печатали двумя командами: компактной
// (`jq -cn`) и развёрнутой (`jq -n`, отступ в два пробела). Харнессу разницы нет,
// он читает JSON, но дифференциальная сверка на переезде сравнивает вывод
// побайтно — значит форму держим ту же, что была у переносимого хука.
function emit(payload, { compact = true } = {}) {
  const text = compact ? JSON.stringify(payload) : JSON.stringify(payload, null, 2);
  process.stdout.write(`${text}\n`);
}

// PreToolUse: запрет вызова с причиной, которую прочитает модель.
export function deny(reason, options) {
  emit({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }, options);
  process.exit(0);
}

// PreToolUse: вызов уходит человеку на подтверждение — сильнее запрета, потому
// что требует живого решения, а не повторной попытки агента.
export function ask(reason) {
  emit({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'ask',
      permissionDecisionReason: reason,
    },
  });
  process.exit(0);
}

// Stop: ход не заканчивается, агенту возвращается причина.
export function block(reason) {
  emit({ decision: 'block', reason });
  process.exit(0);
}

// Текст, дописываемый в контекст агента. Имя события идёт параметром: один и тот
// же приём работает на старте сессии, на реплике, после вызова и после его
// провала, а харнесс сверяет имя с тем событием, на которое хук подписан.
export function inject(hookEventName, additionalContext, options) {
  emit({ hookSpecificOutput: { hookEventName, additionalContext } }, options);
  process.exit(0);
}

// Проход. Гварды на проходе молчат — пустой stdout и есть «разрешено».
export function allow() {
  process.exit(0);
}
