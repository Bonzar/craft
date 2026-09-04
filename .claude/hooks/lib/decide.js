// Решения хука: форма ответа, которую читает харнесс. Компактный JSON одной
// строкой плюс перевод строки; форма и порядок ключей — как у jq.

// Развёрнутая форма (отступ в два пробела) осталась второй: харнессу разницы
// нет, но кейсы сверяют вывод побайтно.
function emit(payload, { compact = true } = {}) {
  const text = compact ? JSON.stringify(payload) : JSON.stringify(payload, null, 2);
  process.stdout.write(`${text}\n`);
  // Признак «решение принято» для диспетчера: под ним хуки одного события делят
  // общий вывод, и второе решение подряд легло бы в него следом за первым.
  globalThis.hookDecided = true;
  // Само решение — в общее состояние события: хук метрик стоит после решения и
  // читает его отсюда, а не из stdout. Имя решившего хука ставит диспетчер.
  globalThis.hookDecision = describe(payload);
}

// Вид решения по форме ответа: deny/ask (PreToolUse), block (Stop), inject
// (дописанный контекст). Текст причины остаётся в памяти процесса — метрики
// вычисляют по нему класс и в журнал не пишут.
function describe(payload) {
  const specific = payload && payload.hookSpecificOutput;
  let kind = '';
  let reason = '';
  if (specific && specific.permissionDecision) {
    kind = specific.permissionDecision;
    reason = specific.permissionDecisionReason || '';
  } else if (payload && payload.decision === 'block') {
    kind = 'block';
    reason = payload.reason || '';
  } else if (specific && specific.additionalContext !== undefined) {
    kind = 'inject';
  }
  return { hook: globalThis.hookCurrent || '', kind, reason };
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
