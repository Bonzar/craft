import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const runner = path.join(repo, 'core/agents/run.mjs');
const mock = path.join(repo, 'tests/fixtures/mock-claude-agent.sh');

function run(input, env = {}) {
  return spawnSync(process.execPath, [runner], {
    cwd: repo,
    env: { ...process.env, CRAFT_AGENT_BACKEND: 'claude', CRAFT_CLAUDE_CMD: mock, CRAFT_AGENT_SESSION_ID: `test-${process.pid}-${Math.random()}`, ...env },
    input: JSON.stringify(input), encoding: 'utf8',
  });
}

test('core runtime returns one provider-neutral result shape', () => {
  const result = run({ agentId: 'comment-analyzer', task: 'Review this comment.', context: { file: 'a.ts' } });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    schemaVersion: 1, status: 'ok', agentId: 'comment-analyzer', backend: 'claude', model: 'haiku', depth: 0, result: 'mock agent result',
  });
});

test('unknown fields and excessive recursion fail before a backend can run', () => {
  const extra = run({ agentId: 'comment-analyzer', task: 'x', nativeTool: 'spawn_agent' });
  assert.notEqual(extra.status, 0);
  assert.match(JSON.parse(extra.stdout).error, /unknown request field/);
  const deep = run({ agentId: 'comment-analyzer', task: 'x', depth: 4 });
  assert.notEqual(deep.status, 0);
  assert.match(JSON.parse(deep.stdout).error, /depth limit/);
});
