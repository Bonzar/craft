function parse(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed) return null;
  try { return JSON.parse(trimmed); } catch { return { type: 'context', content: trimmed }; }
}

export function renderClaudeOutput(eventName, outputs) {
  const values = outputs.map(parse).filter(Boolean);
  if (!values.length) return '';
  const contexts = values.filter((value) => value.type === 'context').map((value) => value.content).filter(Boolean);
  const decision = values.find((value) => ['deny', 'ask', 'block', 'plan_required'].includes(value.type));
  if (decision) {
    if (decision.type === 'block') return `${JSON.stringify({ decision: 'block', reason: decision.reason })}\n`;
    const reason = decision.type === 'plan_required'
      ? `${decision.reason}\n\n[plan_required][unsupported] Текущий hook-only transport закрыл вызов fail-closed, но не подключён к нативному coordinator следующего planning-turn. Текстовый промпт переход не имитирует.`
      : decision.reason;
    return `${JSON.stringify({ hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: decision.type === 'ask' ? 'ask' : 'deny',
      permissionDecisionReason: reason,
    } })}\n`;
  }
  if (!contexts.length) return '';
  return `${JSON.stringify({ hookSpecificOutput: { hookEventName: eventName, additionalContext: contexts.join('\n\n') } })}\n`;
}
