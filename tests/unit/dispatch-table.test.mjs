import assert from 'node:assert/strict';
import test from 'node:test';
import { hooksFor } from '../../core/hooks/dispatch-table.js';

test('Codex SessionStart loads live context before shared capabilities', () => {
  const hooks = hooksFor('session.start', 'none', 'client');
  assert.equal(hooks[0], 'client-inject-context');
  assert.ok(hooks.includes('universal-env-capabilities'));
  assert.ok(hooks.includes('universal-session-anchor'));
});

test('a canonical file patch crosses hygiene, config and the single plan-gate', () => {
  const hooks = hooksFor('action.before', 'file.patch', 'client');
  for (const expected of [
    'craft-guard-plan-hygiene',
    'universal-config-protection',
    'universal-guard-plan-gate',
  ]) assert.ok(hooks.includes(expected), `${expected} is missing`);
  assert.equal(hooks.includes('universal-session-anchor'), false);
});

test('Codex subagent lifecycle is connected to shared context and critic state', () => {
  assert.deepEqual(hooksFor('agent.start', 'agent.invoke', 'client'), ['client-inject-context']);
  assert.deepEqual(hooksFor('agent.stop', 'agent.invoke', 'client'), ['universal-mark-plan-critic']);
});
