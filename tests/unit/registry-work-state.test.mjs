import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import normalizer from '../../adapters/shared/hooks/normalize.cjs';
import { applyWorkStateEvent } from '../../core/hooks/lib/work-state.js';
import { readRegistry, upsertGoal } from '../../core/hooks/lib/registry.js';

const { normalizeHarnessEvent } = normalizer;

function registry() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'registry-work-state.'));
  const file = path.join(dir, 'registry.jsonl');
  upsertGoal(file, {
    title: 'Работа с реестром',
    source: 'plan',
    tasks: [
      { title: 'Закрыть задачу', where: [], body: '' },
      { title: 'Оставить задачу открытой', where: [], body: '' },
    ],
  });
  return { dir, file };
}

test('Claude TaskUpdate closes the addressed registry task after success', () => {
  const { dir, file } = registry();
  try {
    const event = normalizeHarnessEvent({
      hook_event_name: 'PostToolUse',
      tool_name: 'TaskUpdate',
      tool_input: { taskId: 'Ц1.1', status: 'completed' },
    }, 'claude');

    const result = applyWorkStateEvent(file, event);

    assert.deepEqual(result, { changed: ['Ц1.1'], unknown: [] });
    assert.equal(readRegistry(file)[0].tasks[0].state, 'closed');
    assert.equal(readRegistry(file)[0].tasks[1].state, 'open');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Codex update_plan closes a registry task by the exact address in its step', () => {
  const { dir, file } = registry();
  try {
    const event = normalizeHarnessEvent({
      hook_event_name: 'PostToolUse',
      tool_name: 'update_plan',
      tool_input: {
        plan: [
          { step: 'Ц1.1 Закрыть задачу', status: 'completed' },
          { step: 'Ц1.2 Оставить задачу открытой', status: 'pending' },
        ],
      },
    }, 'codex');

    const result = applyWorkStateEvent(file, event);

    assert.deepEqual(result, { changed: ['Ц1.1'], unknown: [] });
    assert.equal(readRegistry(file)[0].tasks[0].state, 'closed');
    assert.equal(readRegistry(file)[0].tasks[1].state, 'open');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('failed native task update does not change registry state', () => {
  const { dir, file } = registry();
  try {
    const event = normalizeHarnessEvent({
      hook_event_name: 'PostToolUse',
      tool_name: 'TaskUpdate',
      tool_input: { taskId: 'Ц1.1', status: 'completed' },
      tool_response: { is_error: true, error: 'failed' },
    }, 'claude');

    const result = applyWorkStateEvent(file, event);

    assert.deepEqual(result, { changed: [], unknown: [] });
    assert.equal(readRegistry(file)[0].tasks[0].state, 'open');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('work state without an exact registry address fails closed', () => {
  const { dir, file } = registry();
  try {
    const event = normalizeHarnessEvent({
      hook_event_name: 'PostToolUse',
      tool_name: 'update_plan',
      tool_input: { plan: [{ step: 'Закрыть задачу', status: 'completed' }] },
    }, 'codex');

    const result = applyWorkStateEvent(file, event);

    assert.deepEqual(result, { changed: [], unknown: [] });
    assert.equal(readRegistry(file)[0].tasks[0].state, 'open');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
