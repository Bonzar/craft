function parse(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed) return null;
  try { return JSON.parse(trimmed); } catch { return { type: 'context', content: trimmed }; }
}

export function renderClaudeOutput(eventName, outputs) {
  const values = outputs.map(parse).filter(Boolean);
  if (!values.length) return '';
  const contexts = values.filter((value) => value.type === 'context').map((value) => value.content).filter(Boolean);
  const notices = values.filter((value) => value.type === 'notice' && typeof value.message === 'string').map((value) => value.message).filter(Boolean);
  const decision = values.find((value) => ['deny', 'ask', 'block', 'plan_required'].includes(value.type));
  if (decision) {
    if (decision.type === 'block') return `${JSON.stringify({ decision: 'block', reason: decision.reason, ...(notices.length ? { systemMessage: notices.join('\n\n') } : {}) })}\n`;
    const reason = decision.type === 'plan_required'
      ? `${decision.reason}\n\n[plan_required][unsupported] Текущий hook-only transport закрыл вызов fail-closed, но не подключён к нативному coordinator следующего planning-turn. Текстовый промпт переход не имитирует.`
      : decision.reason;
    return `${JSON.stringify({ ...(notices.length ? { systemMessage: notices.join('\n\n') } : {}), hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: decision.type === 'ask' ? 'ask' : 'deny',
      permissionDecisionReason: reason,
    } })}\n`;
  }
  if (!contexts.length && !notices.length) return '';
  const content = [...contexts, ...notices].join('\n\n');
  return `${JSON.stringify({ ...(notices.length ? { systemMessage: notices.join('\n\n') } : {}), hookSpecificOutput: { hookEventName: eventName, additionalContext: content } })}\n`;
}
