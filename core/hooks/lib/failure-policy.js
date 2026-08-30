// Most hooks are advisory or additive and keep the dispatcher fail-open. The
// universal plan-gate is the exception: losing it on an action event would turn
// an internal error into permission, so the dispatcher emits a deny decision.
export function hookFailureDecision(eventName, hookName) {
  if (eventName === 'action.before' && hookName === 'universal-guard-plan-gate') {
    return {
      type: 'deny',
      reason: 'Заблокировано план-гейтом: внутренняя ошибка проверки.',
    };
  }
  return null;
}
