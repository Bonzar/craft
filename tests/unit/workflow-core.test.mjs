import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const mock = path.join(repo, 'tests/fixtures/mock-claude-agent.sh');

function run(workflow, input) {
  return spawnSync(process.execPath, [path.join(repo, 'core/workflows', workflow)], {
    cwd: repo,
    env: { ...process.env, CRAFT_AGENT_BACKEND: 'claude', CRAFT_CLAUDE_CMD: mock, CRAFT_AGENT_SESSION_ID: `workflow-${process.pid}-${Math.random()}` },
    input: JSON.stringify(input), encoding: 'utf8', timeout: 30000,
  });
}

test('review workflow uses the core graph and returns a strict fail-soft result', () => {
  const result = run('review-pipeline.mjs', { diff: '--- a/a.ts\n+++ b/a.ts\n-old\n+new', changedFiles: ['a.ts'], cwd: repo });
  assert.equal(result.status, 0, result.stderr);
  const value = JSON.parse(result.stdout);
  assert.equal(value.schemaVersion, 1);
  assert.equal(value.status, 'ok');
  assert.equal(value.verdict, 'APPROVE');
  assert.equal(value.stats.lenses, 4);
  assert.deepEqual(value.failedLenses, []);
});

test('plan critic workflow preserves the canonical fan graph and verdict contract', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-plan-fan.'));
  const plan = path.join(dir, 'plan.md');
  fs.writeFileSync(plan, '# [система · core]\nDo the work.\n');
  const result = run('plan-critic-fan.mjs', { plan, units: ['[система · core]'], cwd: repo });
  assert.equal(result.status, 0, result.stderr);
  const value = JSON.parse(result.stdout);
  assert.equal(value.status, 'ok');
  assert.equal(value.workflowId, 'plan-critic-fan');
  assert.match(value.result, /Вердикт: блокеров нет$/);
  assert.deepEqual(value.failedCritics, []);
});
