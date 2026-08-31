import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const invoke = path.join(repo, 'core', 'classifier', 'invoke.mjs');

function adapter(root, name, source, config) {
  const dir = path.join(root, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'classifier-backend.mjs'), source);
  if (config) fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify(config));
}

function notices(stderr) {
  return String(stderr || '').split('\n')
    .filter((line) => line.startsWith('CRAFT_CLASSIFIER_NOTICE '))
    .map((line) => JSON.parse(line.slice('CRAFT_CLASSIFIER_NOTICE '.length)));
}

test('classifier candidate list falls back across registered backends and reports the switch', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-fallbacks.'));
  const calls = path.join(root, 'calls.jsonl');
  try {
    adapter(root, 'alpha', `
      import fs from 'node:fs';
      export function runClassifier({ model, env }) {
        fs.appendFileSync(env.CLASSIFIER_TEST_CALLS, JSON.stringify({ backend: 'alpha', model }) + '\\n');
        const error = new Error('primary unavailable');
        error.classifierReason = 'usage_limit';
        error.retryAt = '15:52';
        error.classifierDetail = 'Лимит исчерпан до 15:52';
        throw error;
      }
    `, { classifier: { candidates: [
      { backend: 'alpha', model: 'primary-model' },
      { backend: 'beta', model: 'fallback-model' },
    ] } });
    adapter(root, 'beta', `
      import fs from 'node:fs';
      export function runClassifier({ model, env }) {
        fs.appendFileSync(env.CLASSIFIER_TEST_CALLS, JSON.stringify({ backend: 'beta', model }) + '\\n');
        return { output: 'ALLOW_SESSION:fallback selected' };
      }
    `);

    const result = spawnSync(process.execPath, [invoke], {
      input: 'classify', encoding: 'utf8', env: {
        ...process.env,
        CRAFT_CLASSIFIER_BACKEND: 'alpha',
        CRAFT_ADAPTER_ROOT: root,
        CLASSIFIER_TEST_CALLS: calls,
      },
    });

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'ALLOW_SESSION:fallback selected');
    assert.deepEqual(fs.readFileSync(calls, 'utf8').trim().split('\n').map(JSON.parse), [
      { backend: 'alpha', model: 'primary-model' },
      { backend: 'beta', model: 'fallback-model' },
    ]);
    assert.deepEqual(notices(result.stderr), [
      {
        type: 'classifier_attempt_failed', backend: 'alpha', model: 'primary-model',
        reason: 'usage_limit', retryAt: '15:52', detail: 'Лимит исчерпан до 15:52',
      },
      { type: 'classifier_fallback_selected', backend: 'beta', model: 'fallback-model' },
    ]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('classifier reports every configured candidate before failing closed', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-unavailable.'));
  try {
    const failing = `export function runClassifier() {
      const error = new Error('unavailable');
      error.classifierReason = 'auth';
      throw error;
    }`;
    adapter(root, 'alpha', failing, { classifier: { candidates: [
      { backend: 'alpha', model: 'one' },
      { backend: 'beta', model: 'two' },
    ] } });
    adapter(root, 'beta', failing);

    const result = spawnSync(process.execPath, [invoke], {
      input: 'classify', encoding: 'utf8', env: {
        ...process.env,
        CRAFT_CLASSIFIER_BACKEND: 'alpha',
        CRAFT_ADAPTER_ROOT: root,
      },
    });

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'UNAVAILABLE');
    assert.deepEqual(notices(result.stderr).at(-1), {
      type: 'classifier_unavailable',
      attempts: [
        { backend: 'alpha', model: 'one', reason: 'auth', detail: 'unavailable' },
        { backend: 'beta', model: 'two', reason: 'auth', detail: 'unavailable' },
      ],
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('transient backend failure retries the same candidate before using fallback', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-retry.'));
  const counter = path.join(root, 'counter');
  try {
    adapter(root, 'alpha', `
      import fs from 'node:fs';
      export function runClassifier({ env }) {
        const count = fs.existsSync(env.CLASSIFIER_TEST_COUNTER)
          ? Number(fs.readFileSync(env.CLASSIFIER_TEST_COUNTER, 'utf8')) : 0;
        fs.writeFileSync(env.CLASSIFIER_TEST_COUNTER, String(count + 1));
        if (count === 0) {
          const error = new Error('temporary network failure');
          error.classifierReason = 'network';
          throw error;
        }
        return { output: 'ALLOW_SESSION:retry succeeded' };
      }
    `, { classifier: { candidates: [
      { backend: 'alpha', model: 'one' },
      { backend: 'beta', model: 'two' },
    ] } });
    adapter(root, 'beta', `export function runClassifier() { throw new Error('must not run'); }`);

    const result = spawnSync(process.execPath, [invoke], {
      input: 'classify', encoding: 'utf8', env: {
        ...process.env,
        CRAFT_CLASSIFIER_BACKEND: 'alpha',
        CRAFT_ADAPTER_ROOT: root,
        CLASSIFIER_TEST_COUNTER: counter,
        CRAFT_CLASSIFIER_RETRY_DELAY_MS: '0',
      },
    });

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'ALLOW_SESSION:retry succeeded');
    assert.equal(fs.readFileSync(counter, 'utf8'), '2');
    assert.deepEqual(notices(result.stderr), [{
      type: 'classifier_attempt_failed', backend: 'alpha', model: 'one', reason: 'network',
      detail: 'temporary network failure',
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('non-transient backend failure is not retried', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-no-retry.'));
  const calls = path.join(root, 'calls');
  try {
    adapter(root, 'alpha', `
      import fs from 'node:fs';
      export function runClassifier({ env }) {
        fs.appendFileSync(env.CLASSIFIER_TEST_CALLS, 'x');
        const error = new Error('quota exhausted');
        error.classifierReason = 'usage_limit';
        throw error;
      }
    `, { classifier: { candidates: [
      { backend: 'alpha', model: 'one' },
      { backend: 'beta', model: 'two' },
    ] } });
    adapter(root, 'beta', `export function runClassifier() { return { output: 'ALLOW_SESSION:fallback' }; }`);

    const result = spawnSync(process.execPath, [invoke], {
      input: 'classify', encoding: 'utf8', env: {
        ...process.env,
        CRAFT_CLASSIFIER_BACKEND: 'alpha',
        CRAFT_ADAPTER_ROOT: root,
        CLASSIFIER_TEST_CALLS: calls,
      },
    });

    assert.equal(result.stdout.trim(), 'ALLOW_SESSION:fallback');
    assert.equal(fs.readFileSync(calls, 'utf8'), 'x');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('invalid cover output advances to the next explicit candidate', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-invalid-cover.'));
  try {
    adapter(root, 'alpha', `
      export function runClassifier({ model }) {
        return { output: model === 'one' ? 'explanation instead of verdict' : 'ПОКРЫТА Ц1.1: разрешённая задача' };
      }
    `, { classifier: { candidates: [
      { backend: 'alpha', model: 'one' },
      { backend: 'alpha', model: 'two' },
    ] } });
    const result = spawnSync(process.execPath, [invoke], {
      input: 'classify', encoding: 'utf8', env: {
        ...process.env,
        CRAFT_CLASSIFIER_BACKEND: 'alpha',
        CRAFT_ADAPTER_ROOT: root,
        CRAFT_CLASSIFIER_MODE: 'cover',
      },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'COVERED:Ц1.1: разрешённая задача');
    assert.equal(notices(result.stderr).at(-1).type, 'classifier_fallback_selected');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('malformed candidate configuration fails closed without trying an implicit model', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'classifier-malformed.'));
  try {
    adapter(root, 'alpha', `export function runClassifier() { return { output: 'ALLOW_SESSION:wrong' }; }`, {
      classifier: { candidates: [{ backend: 'alpha', model: 'one', extra: true }] },
    });
    const result = spawnSync(process.execPath, [invoke], {
      input: 'classify', encoding: 'utf8', env: {
        ...process.env,
        CRAFT_CLASSIFIER_BACKEND: 'alpha',
        CRAFT_ADAPTER_ROOT: root,
      },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'UNAVAILABLE');
    assert.equal(notices(result.stderr).at(-1).type, 'classifier_configuration_invalid');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
