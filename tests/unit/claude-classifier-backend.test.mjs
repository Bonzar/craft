import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { runClassifier } from '../../adapters/claude/classifier-backend.mjs';

const schema = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  type: 'object', additionalProperties: false, required: ['output'],
  properties: { output: { type: 'string', minLength: 1 } },
};

function fakeClaude(dir) {
  const command = path.join(dir, 'fake-claude.mjs');
  fs.writeFileSync(command, `#!/usr/bin/env node
import fs from 'node:fs';
const args = process.argv.slice(2);
fs.writeFileSync(process.env.FAKE_CLAUDE_ARGS, JSON.stringify(args));
fs.writeFileSync(process.env.FAKE_CLAUDE_ENV, JSON.stringify({
  session: process.env.CLAUDE_CODE_SESSION_ID || null,
}));
if (args.includes('--bare')) {
  process.stdout.write(JSON.stringify({ subtype: 'success', result: 'Not logged in · Please run /login' }));
  process.exit(47);
}
const settingsSource = args[args.indexOf('--setting-sources') + 1];
if (settingsSource !== 'project') {
  process.stderr.write('user hook blocked StructuredOutput');
  process.exit(42);
}
const turns = Number(args[args.indexOf('--max-turns') + 1]);
if (turns < 2) {
  process.stderr.write('error_max_turns');
  process.exit(43);
}
const nativeSchema = JSON.parse(args[args.indexOf('--json-schema') + 1]);
if ('$schema' in nativeSchema) {
  process.stderr.write('unsupported schema dialect');
  process.exit(45);
}
if (process.env.FAKE_CLAUDE_FAILURE === 'network') {
  process.stderr.write('ERR_NETWORK_IO_SUSPENDED');
  process.exit(44);
}
if (process.env.FAKE_CLAUDE_FAILURE === 'stdout') {
  process.stdout.write(JSON.stringify({ subtype: 'error_max_turns', result: 'Structured output was not produced' }));
  process.exit(46);
}
process.stdout.write(JSON.stringify({ structured_output: { output: 'ALLOW_SESSION:isolated' } }));
`);
  fs.chmodSync(command, 0o755);
  return command;
}

function invoke(dir, extraEnv = {}) {
  const env = {
    ...process.env,
    CRAFT_CLAUDE_CMD: fakeClaude(dir),
    FAKE_CLAUDE_ARGS: path.join(dir, 'args.json'),
    FAKE_CLAUDE_ENV: path.join(dir, 'env.json'),
    CLAUDE_CODE_SESSION_ID: 'parent-session',
    ...extraEnv,
  };
  return { env, value: runClassifier({
    model: 'test-model', prompt: 'classify', systemPrompt: 'system', schema,
    turns: 1, tools: '', timeoutMs: 5_000, env,
  }) };
}

test('Claude classifier isolates user hooks without dropping account auth', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-classifier.'));
  try {
    const { env, value } = invoke(dir);
    assert.deepEqual(value, { output: 'ALLOW_SESSION:isolated' });
    const args = JSON.parse(fs.readFileSync(env.FAKE_CLAUDE_ARGS, 'utf8'));
    assert.ok(!args.includes('--bare'));
    assert.equal(args[args.indexOf('--setting-sources') + 1], 'project');
    assert.ok(Number(args[args.indexOf('--max-turns') + 1]) >= 2);
    assert.equal(JSON.parse(fs.readFileSync(env.FAKE_CLAUDE_ENV, 'utf8')).session, null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Claude classifier maps transport failure to a typed safe reason', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-classifier-failure.'));
  try {
    assert.throws(
      () => invoke(dir, { FAKE_CLAUDE_FAILURE: 'network' }),
      (error) => {
        assert.equal(error.classifierReason, 'network');
        assert.match(error.classifierDetail, /network/i);
        return true;
      },
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Claude classifier preserves a useful failure returned only in JSON stdout', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-classifier-stdout-failure.'));
  try {
    assert.throws(
      () => invoke(dir, { FAKE_CLAUDE_FAILURE: 'stdout' }),
      (error) => {
        assert.equal(error.classifierReason, 'invalid_output');
        assert.match(error.classifierDetail, /error_max_turns/);
        assert.match(error.classifierDetail, /Structured output/);
        return true;
      },
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
