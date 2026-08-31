import assert from 'node:assert/strict';
import test from 'node:test';
import { renderClaudeOutput } from '../../adapters/claude/hooks/lib/output.js';

test('PreToolUse renders a classifier warning as a user-visible notification', () => {
  const payload = JSON.parse(renderClaudeOutput('PreToolUse', [
    JSON.stringify({ type: 'notice', level: 'warning', message: 'primary unavailable; fallback selected' }),
  ]));
  assert.equal(payload.systemMessage, 'primary unavailable; fallback selected');
  assert.equal(payload.hookSpecificOutput.additionalContext, 'primary unavailable; fallback selected');
});
