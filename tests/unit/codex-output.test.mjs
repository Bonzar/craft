import assert from 'node:assert/strict';
import test from 'node:test';
import { renderCodexOutput } from '../../adapters/codex/hooks/lib/output.js';

test('SessionStart folds plain hook output into one additionalContext response', () => {
  const rendered = renderCodexOutput('SessionStart', ['router', '', 'rules']);
  assert.deepEqual(JSON.parse(rendered), {
    hookSpecificOutput: {
      hookEventName: 'SessionStart',
      additionalContext: 'router\n\nrules',
    },
  });
});

test('PreToolUse preserves a denial and attaches earlier context', () => {
  const denial = JSON.stringify({ type: 'deny', reason: 'blocked' });
  const payload = JSON.parse(renderCodexOutput('PreToolUse', [
    JSON.stringify({ type: 'context', event: 'PreToolUse', content: 'fact' }),
    denial,
  ]));
  assert.equal(payload.hookSpecificOutput.permissionDecision, 'deny');
  assert.equal(payload.hookSpecificOutput.permissionDecisionReason, 'blocked');
  assert.equal(payload.hookSpecificOutput.additionalContext, 'fact');
});

test('PreToolUse renders a classifier warning as a user-visible notification', () => {
  const payload = JSON.parse(renderCodexOutput('PreToolUse', [
    JSON.stringify({ type: 'notice', level: 'warning', message: 'primary unavailable; fallback selected' }),
  ]));
  assert.equal(payload.systemMessage, 'primary unavailable; fallback selected');
  assert.equal(payload.hookSpecificOutput.additionalContext, 'primary unavailable; fallback selected');
});

test('unsupported ask is fail-closed as deny in Codex', () => {
  const payload = JSON.parse(renderCodexOutput('PreToolUse', [
    JSON.stringify({ type: 'ask', reason: 'confirm destructive action' }),
  ]));
  assert.equal(payload.hookSpecificOutput.permissionDecision, 'deny');
  assert.match(payload.hookSpecificOutput.permissionDecisionReason, /fail-closed/);
});

test('plan_required after a failed tool uses valid PostToolUse feedback wire', () => {
  const payload = JSON.parse(renderCodexOutput('PostToolUse', [
    JSON.stringify({ type: 'plan_required', reason: 'planning required' }),
  ]));
  assert.equal(payload.decision, 'block');
  assert.equal(payload.continue, false);
  assert.match(payload.reason, /plan_required/);
  assert.match(payload.reason, /unsupported/);
  assert.equal(payload.hookSpecificOutput, undefined);
});

test('Stop keeps the shared blocking wire format', () => {
  const rendered = renderCodexOutput('Stop', [JSON.stringify({ type: 'block', reason: 'fix it' })]);
  assert.deepEqual(JSON.parse(rendered), { decision: 'block', reason: 'fix it' });
});

test('SubagentStart turns shared context into native additionalContext', () => {
  const rendered = renderCodexOutput('SubagentStart', ['runtime contract']);
  assert.deepEqual(JSON.parse(rendered), {
    hookSpecificOutput: {
      hookEventName: 'SubagentStart',
      additionalContext: 'runtime contract',
    },
  });
});

test('SubagentStop always returns valid Codex JSON when the core is silent', () => {
  assert.deepEqual(JSON.parse(renderCodexOutput('SubagentStop', ['', ''])), {});
});
