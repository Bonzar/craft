import assert from 'node:assert/strict';
import test from 'node:test';
import { hookFailureDecision } from '../../core/hooks/lib/failure-policy.js';

test('a crashed plan-gate denies the action instead of disappearing', () => {
  assert.deepEqual(hookFailureDecision('action.before', 'universal-guard-plan-gate'), {
    type: 'deny',
    reason: 'Заблокировано план-гейтом: внутренняя ошибка проверки.',
  });
});

test('non-safety hooks keep the dispatcher fail-open policy', () => {
  assert.equal(hookFailureDecision('session.start', 'craft-inject-router'), null);
  assert.equal(hookFailureDecision('action.before', 'universal-check-console-log'), null);
});

test('missing and nonzero plan-gate executions use the same deny policy', () => {
  for (const failure of ['missing', 'exception', 'nonzero-exit']) {
    assert.equal(hookFailureDecision('action.before', 'universal-guard-plan-gate', failure)?.type, 'deny');
  }
});
