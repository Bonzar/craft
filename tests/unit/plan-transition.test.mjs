import assert from 'node:assert/strict';
import test from 'node:test';
import { planTransition } from '../../core/orchestration/plan-transition.mjs';
import { createPlanRequired, validatePlanRequired } from '../../core/contracts/plan-required.mjs';
import { coordinatePlanTransition } from '../../adapters/codex/plan-mode-backend.mjs';
import fs from 'node:fs';

test('supported adapter creates a native next-turn planning operation', async () => {
  const result = await planTransition('codex', { sessionRef: 'thread-1', intent: 'Refactor the gate' });
  assert.equal(result.status, 'ready');
  assert.equal(result.operation.method, 'turn/start');
  assert.equal(result.operation.params.collaborationMode.mode, 'plan');
  assert.equal(result.operation.params.collaborationMode.settings.reasoning_effort, 'medium');
  assert.equal(result.operation.params.collaborationMode.settings.developer_instructions, null);
  assert.equal(result.operation.params.input[0].text, 'Refactor the gate');
  assert.deepEqual(result.operation.params.input[0].text_elements, []);
});

test('plan_required has a strict versioned schema', () => {
  const decision = createPlanRequired('approval is missing', {
    blockedAction: { route: 'file.mutate', payload: { target: 'a.js' } },
    originalIntent: 'Refactor the gate',
    transitionId: 'transition-1',
  });
  assert.equal(validatePlanRequired(decision), decision);
  assert.throws(() => validatePlanRequired({ ...decision, extra: true }), /invalid schema/);
});

test('coordinator denies, ends and awaits the current turn before starting planning exactly once', async () => {
  const config = JSON.parse(fs.readFileSync(new URL('../../adapters/codex/config.json', import.meta.url), 'utf8'));
  const decision = createPlanRequired('approval is missing', {
    blockedAction: { route: 'file.mutate', payload: { target: 'a.js' } },
    originalIntent: 'Refactor the gate',
    transitionId: `transition-${process.pid}`,
  });
  const calls = [];
  const transport = {
    denyAction: async () => { calls.push('deny'); },
    endTurn: async () => { calls.push('end'); },
    awaitTurnEnded: async () => { calls.push('ack'); return { status: 'ended' }; },
    request: async (operation) => { calls.push(operation.method); return { status: 'started' }; },
  };
  const first = await coordinatePlanTransition(decision, { threadId: 'thread-1' }, config, transport);
  const second = await coordinatePlanTransition(decision, { threadId: 'thread-1' }, config, transport);
  assert.equal(first.status, 'started');
  assert.equal(second.status, 'started');
  assert.deepEqual(calls, ['deny', 'end', 'ack', 'turn/start']);
});

test('coordinator stays blocked without native transport or original intent', async () => {
  const config = JSON.parse(fs.readFileSync(new URL('../../adapters/codex/config.json', import.meta.url), 'utf8'));
  const decision = createPlanRequired('approval is missing', {
    blockedAction: { route: 'unknown', payload: {} },
    originalIntent: '',
    transitionId: 'missing-intent',
  });
  const result = await coordinatePlanTransition(decision, { threadId: 'thread-1' }, config, null);
  assert.equal(result.status, 'blocked');
  assert.equal(result.blocked, true);
});

test('coordinator reports unsupported when the hook connection has no harness transport', async () => {
  const config = JSON.parse(fs.readFileSync(new URL('../../adapters/codex/config.json', import.meta.url), 'utf8'));
  const decision = createPlanRequired('approval is missing', {
    blockedAction: { route: 'file.mutate', payload: { target: 'a.js' } },
    originalIntent: 'Refactor the gate',
    transitionId: 'no-transport',
  });
  const result = await coordinatePlanTransition(decision, { threadId: 'thread-1' }, config, null);
  assert.equal(result.status, 'unsupported');
  assert.equal(result.blocked, true);
});

test('missing native transition support is explicit and fail-closed', async () => {
  const result = await planTransition('claude', { sessionRef: 'thread-1', intent: 'Refactor the gate' });
  assert.equal(result.status, 'unsupported');
  assert.equal(result.blocked, true);
});
