import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parsePreflightVerdict } from '../../core/classifier/verdict.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

test('classifier backend configuration pins Codex to Spark and has no implicit fallback', () => {
  const claude = JSON.parse(fs.readFileSync(path.join(repo, 'adapters/claude/config.json'), 'utf8'));
  const codex = JSON.parse(fs.readFileSync(path.join(repo, 'adapters/codex/config.json'), 'utf8'));
  assert.equal(claude.classifier.model, 'haiku');
  assert.equal(codex.classifier.model, 'gpt-5.3-codex-spark');
  assert.deepEqual(claude.classifier.fallbackModels, []);
  assert.deepEqual(codex.classifier.fallbackModels, []);
});

test('ingest schema validator accepts exact decisions and rejects extra fields', () => {
  const validator = path.join(repo, 'core/classifier/validate-ingest.mjs');
  const good = { add: [{ kind: 'work', goal_new: 'Goal', tasks: [{ title: 'Task', where: ['a.ts'], anchor: '## Task' }] }], close: [], lift: [] };
  const accepted = spawnSync(process.execPath, [validator], { input: JSON.stringify(good), encoding: 'utf8' });
  assert.equal(accepted.status, 0);
  assert.deepEqual(JSON.parse(accepted.stdout), good);
  const bad = spawnSync(process.execPath, [validator], { input: JSON.stringify({ ...good, write: true }), encoding: 'utf8' });
  assert.notEqual(bad.status, 0);
  const ban = { add: [{ kind: 'ban', goal_new: 'Never edit generated files' }], close: [], lift: [] };
  const acceptedBan = spawnSync(process.execPath, [validator], { input: JSON.stringify(ban), encoding: 'utf8' });
  assert.equal(acceptedBan.status, 0);
});

test('cover transport rejects multiline and empty-detail model answers', () => {
  const runner = path.join(repo, 'core/classifier/run.sh');
  const mock = path.join(repo, 'tests/hooks/fixtures/mock-classifier.sh');
  const registry = path.join(repo, 'tests/hooks/fixtures/plan-scope.md');
  const classify = (answer) => spawnSync('bash', [runner, 'cover', registry], {
    cwd: repo,
    encoding: 'utf8',
    env: { ...process.env, PLAN_CLASSIFIER_CMD: mock, MOCK_CLASSIFIER_ANSWER: answer },
  }).stdout.trim();
  assert.equal(classify('explanation\nПОКРЫТА Ц1.1: ok'), 'UNAVAILABLE');
  assert.equal(classify('ПОКРЫТА '), 'UNAVAILABLE');
});

test('preflight verdict has a strict fail-closed schema', () => {
  assert.deepEqual(parsePreflightVerdict('ALLOW_SESSION:typed session action'), {
    kind: 'ALLOW_SESSION', allowing: true, detail: 'typed session action',
  });
  assert.deepEqual(parsePreflightVerdict('ALLOW_EPHEMERAL:temporary target'), {
    kind: 'ALLOW_EPHEMERAL', allowing: true, detail: 'temporary target',
  });
  assert.deepEqual(parsePreflightVerdict('CHECK_REGISTRY:world effect'), {
    kind: 'CHECK_REGISTRY', allowing: false, detail: 'world effect',
  });
  assert.deepEqual(parsePreflightVerdict('DENY:malformed action'), {
    kind: 'DENY', allowing: false, detail: 'malformed action',
  });
  for (const invalid of ['ALLOW_SESSION', 'ALLOW_EPHEMERAL:', 'CHECK_REGISTRY', 'allow session:x', 'DENY:x\nextra', 'UNAVAILABLE:reason']) {
    assert.equal(parsePreflightVerdict(invalid), null, invalid);
  }
});

test('preflight transport rejects malformed model output', () => {
  const runner = path.join(repo, 'core/classifier/run.sh');
  const mock = path.join(repo, 'tests/hooks/fixtures/mock-classifier.sh');
  const classify = (answer) => spawnSync('bash', [runner, 'preflight'], {
    cwd: repo,
    encoding: 'utf8',
    input: JSON.stringify({ intent: { effect: 'session' }, action: { route: 'session.plan', payload: {} } }),
    env: { ...process.env, PLAN_CLASSIFIER_CMD: mock, MOCK_CLASSIFIER_PREFLIGHT: answer },
  }).stdout.trim();
  assert.equal(classify('ALLOW_SESSION:typed session action'), 'ALLOW_SESSION:typed session action');
  assert.equal(classify('explanation\nALLOW_SESSION:typed session action'), 'UNAVAILABLE');
  assert.equal(classify('ALLOW_SESSION'), 'UNAVAILABLE');
});
