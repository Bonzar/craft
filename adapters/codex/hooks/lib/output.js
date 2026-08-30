// Codex requires one valid response per hook handler. The shared dispatcher
// runs several policy hooks serially, so their individual stdout fragments are
// folded here into one event response without changing Claude's wire format.

function parse(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed) return null;
  try {
    return { json: JSON.parse(trimmed), text: '' };
  } catch {
    return { json: null, text: trimmed };
  }
}

function contextOf(payload) {
  if (payload && payload.type === 'context') return typeof payload.content === 'string' ? payload.content : '';
  const value = payload && payload.hookSpecificOutput && payload.hookSpecificOutput.additionalContext;
  return typeof value === 'string' ? value : '';
}

export function renderCodexOutput(eventName, outputs) {
  const parsed = outputs.map(parse).filter(Boolean);
  if (!parsed.length) return ['SubagentStart', 'SubagentStop'].includes(eventName) ? '{}\n' : '';

  const contexts = [];
  const messages = [];
  let decision = null;
  let shouldContinue;
  let stopReason = '';

  for (const item of parsed) {
    if (item.text) {
      if (['SessionStart', 'UserPromptSubmit', 'SubagentStart'].includes(eventName)) contexts.push(item.text);
      else messages.push(item.text);
      continue;
    }
    const payload = item.json;
    const context = contextOf(payload);
    if (context) contexts.push(context);
    if (payload.type === 'deny' || payload.type === 'ask' || payload.type === 'plan_required') {
      const reason = payload.type === 'plan_required'
        ? `${payload.reason}\n\n[plan_required][unsupported] Текущий hook-only transport закрыл вызов fail-closed, но не умеет завершить ход и отправить turn/start. Нужен app-server orchestration transport; текстовый промпт переход не имитирует.`
        : payload.type === 'ask'
          ? `${payload.reason}\n\nЭтот Codex hook transport не поддерживает ручное ask-решение; вызов закрыт fail-closed.`
          : payload.reason;
      if (eventName === 'PreToolUse') {
        decision = { hookSpecificOutput: {
          hookEventName: 'PreToolUse',
          permissionDecision: 'deny',
          permissionDecisionReason: reason,
        } };
      } else {
        // PostToolUse cannot undo the finished call, but this is the valid
        // Codex feedback shape. The orchestration coordinator remains
        // responsible for ending the turn and starting the planning turn.
        decision = { decision: 'block', reason, continue: false, stopReason: reason };
      }
      continue;
    }
    if (payload.type === 'block') {
      decision = { decision: 'block', reason: payload.reason };
      continue;
    }
    if (typeof payload.systemMessage === 'string' && payload.systemMessage) messages.push(payload.systemMessage);
    if (payload.decision || (payload.hookSpecificOutput && payload.hookSpecificOutput.permissionDecision)) {
      decision = payload;
    }
    if (payload.continue === false) shouldContinue = false;
    if (typeof payload.stopReason === 'string') stopReason = payload.stopReason;
  }

  // A blocking decision is already a complete event-specific payload. Preserve
  // it and attach any earlier informational context where the event accepts it.
  if (decision) {
    const out = { ...decision };
    if (messages.length && !out.systemMessage) out.systemMessage = messages.join('\n\n');
    if (contexts.length && ['PreToolUse', 'PostToolUse'].includes(eventName)) {
      out.hookSpecificOutput = {
        ...(out.hookSpecificOutput || {}),
        hookEventName: eventName,
        additionalContext: contexts.join('\n\n'),
      };
    }
    return `${JSON.stringify(out)}\n`;
  }

  const out = {};
  if (shouldContinue === false) out.continue = false;
  if (stopReason) out.stopReason = stopReason;
  if (messages.length) out.systemMessage = messages.join('\n\n');
  if (contexts.length) {
    out.hookSpecificOutput = {
      hookEventName: eventName,
      additionalContext: contexts.join('\n\n'),
    };
  }
  return Object.keys(out).length ? `${JSON.stringify(out)}\n` : '';
}
