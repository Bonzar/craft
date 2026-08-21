// Решения хука. Форма ответа задана харнессом и повторяет то, что печатали
// bash-версии через `jq -cn`: компактный JSON одной строкой плюс перевод строки.
//
// Совпадение до байта здесь не косметика: пока идёт переезд, дифференциальная
// сверка гоняет обе версии хука на одном входе и валит кейс на любом различии
// вывода. Порядок ключей в объектах ниже — тот же, в котором их собирал jq.

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

// PreToolUse: запрет вызова с причиной, которую прочитает модель.
export function deny(reason) {
  emit({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  });
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

// Проход. Гварды на проходе молчат — пустой stdout и есть «разрешено».
export function allow() {
  process.exit(0);
}
