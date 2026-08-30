import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import normalizer from '../../adapters/shared/hooks/normalize.cjs';

const { normalizeHarnessEvent } = normalizer;

test('native names are normalized before reaching core', () => {
  const event = normalizeHarnessEvent({
    hook_event_name: 'PreToolUse',
    tool_name: 'apply_patch',
    tool_input: { command: '*** Begin Patch\n*** Update File: a.js\n*** End Patch' },
  }, 'test');
  assert.equal(event.action.route, 'file.patch');
  assert.deepEqual(event.action.payload.changes, [
    { kind: 'update', file: 'a.js', oldText: '', newText: '' },
  ]);
  assert.equal('patch' in event.action.payload, false);
  assert.equal(event.event.name, 'action.before');
});

test('native invocation identity survives as provider-neutral event metadata', () => {
  const first = normalizeHarnessEvent({
    hook_event_name: 'PreToolUse',
    tool_use_id: 'call-1',
    tool_name: 'Edit',
    tool_input: { file_path: 'a.js', old_string: 'a', new_string: 'b' },
  }, 'test');
  const retry = normalizeHarnessEvent({
    hook_event_name: 'PreToolUse',
    tool_use_id: 'call-2',
    tool_name: 'Edit',
    tool_input: { file_path: 'a.js', old_string: 'a', new_string: 'b' },
  }, 'test');
  assert.equal(first.event.invocationId, 'call-1');
  assert.equal(retry.event.invocationId, 'call-2');
  assert.notDeepEqual(first, retry);
});

test('adapter labels primary and child plan artifacts before core sees them', () => {
  const options = { planRoot: '/native/client/plans' };
  const primary = normalizeHarnessEvent({
    hook_event_name: 'PostToolUse',
    tool_name: 'Write',
    tool_input: { file_path: '/native/client/plans/main.md', content: '# plan' },
  }, 'claude', '', options);
  const child = normalizeHarnessEvent({
    hook_event_name: 'PostToolUse',
    tool_name: 'Write',
    tool_input: { file_path: '/native/client/plans/main-agent-1.md', content: '# draft' },
  }, 'claude', '', options);
  assert.deepEqual(primary.action.payload.planArtifact, { kind: 'plan', role: 'primary', path: '/native/client/plans/main.md' });
  assert.deepEqual(child.action.payload.planArtifact, { kind: 'plan', role: 'child', path: '/native/client/plans/main-agent-1.md' });

  const repositoryPlan = normalizeHarnessEvent({
    hook_event_name: 'PostToolUse', tool_name: 'Write',
    tool_input: { file_path: '/repo/docs/plans/release.md', content: '# docs' },
  }, 'claude', '', options);
  const unsupportedRuntime = normalizeHarnessEvent({
    hook_event_name: 'PostToolUse', tool_name: 'Write',
    tool_input: { file_path: '/native/client/plans/main.md', content: '# plan' },
  }, 'codex', '', options);
  assert.equal(repositoryPlan.action.payload.planArtifact, undefined);
  assert.equal(unsupportedRuntime.action.payload.planArtifact, undefined);
});

test('adapter extracts the whole visible turn and edited files from native transcript', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-transcript-test.'));
  const transcript = path.join(dir, 'session.jsonl');
  fs.writeFileSync(transcript, [
    { type: 'user', message: { content: [{ type: 'text', text: 'start' }] } },
    { type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'first' }, { type: 'tool_use', name: 'Edit', input: { file_path: '/tmp/a.js' } }] } },
    { type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'last' }] } },
  ].map((entry) => JSON.stringify(entry)).join('\n'));
  try {
    const event = normalizeHarnessEvent({ hook_event_name: 'Stop', transcript_path: transcript }, 'test');
    assert.equal(event.event.assistantTurnText, 'first\nlast');
    assert.deepEqual(event.event.sessionEditedFiles, ['/tmp/a.js']);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('unknown tools stay unknown and therefore cannot acquire read permission', () => {
  const event = normalizeHarnessEvent({
    hook_event_name: 'PreToolUse',
    tool_name: 'future_mutator',
    tool_input: { value: 1 },
  }, 'test');
  assert.equal(event.action.route, 'unknown');
});

test('read-looking compound operations do not bypass exact capability mapping', () => {
  for (const tool_name of ['mcp__x__get_or_create', 'mcp__x__read_then_update', 'mcp__x__list_and_delete']) {
    const event = normalizeHarnessEvent({ hook_event_name: 'PreToolUse', tool_name, tool_input: {} }, 'test');
    assert.equal(event.action.route, 'unknown', tool_name);
  }
});

test('real Craft app read tool is recognized by its exact operation suffix', () => {
  const event = normalizeHarnessEvent({
    hook_event_name: 'PreToolUse',
    tool_name: 'mcp__codex_apps__craft_mcp_craft_read',
    tool_input: { command: 'blocks get ABC --depth -1' },
  }, 'test');
  assert.equal(event.action.route, 'read');
});

test('real Craft app write tool is recognized by its exact operation suffix', () => {
  const event = normalizeHarnessEvent({
    hook_event_name: 'PreToolUse',
    tool_name: 'mcp__codex_apps__craft_mcp_craft_write',
    tool_input: { command: 'blocks update --id ABC --json {}' },
  }, 'test');
  assert.equal(event.action.route, 'data.mutate');
});

test('native session tools map to granular provider-neutral effects', () => {
  const cases = new Map([
    ['TaskCreate', 'session.work'],
    ['update_plan', 'session.plan'],
    ['ScheduleWakeup', 'session.schedule'],
    ['send_message', 'agent.control'],
    ['SendUserFile', 'session.delivery'],
    ['request_permissions', 'session.permission'],
  ]);
  for (const [tool_name, route] of cases) {
    const event = normalizeHarnessEvent({ hook_event_name: 'PreToolUse', tool_name, tool_input: {} }, 'test');
    assert.equal(event.action.route, route, tool_name);
  }
});

test('failed Codex PostToolUse is normalized to the universal failure event', () => {
  const event = normalizeHarnessEvent({
    hook_event_name: 'PostToolUse',
    tool_name: 'ExitPlanMode',
    tool_input: {},
    tool_response: { is_error: true, error: 'not in plan mode' },
  }, 'test');
  assert.equal(event.event.name, 'action.failure');
  assert.equal(event.outcome.status, 'error');
});
