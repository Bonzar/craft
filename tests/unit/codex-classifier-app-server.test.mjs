import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { runClassifier } from '../../adapters/codex/classifier-backend.mjs';

const outputSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['output'],
  properties: { output: { type: 'string', minLength: 1 } },
};

function fakeCodex(dir) {
  const command = path.join(dir, 'fake-codex.mjs');
  fs.writeFileSync(command, `#!/usr/bin/env node
import fs from 'node:fs';
import readline from 'node:readline';

fs.writeFileSync(process.env.FAKE_CODEX_ARGS, JSON.stringify(process.argv.slice(2)));
if (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(['--disable', 'hooks', 'app-server', '--stdio'])) process.exit(41);

const send = (value) => process.stdout.write(JSON.stringify(value) + '\\n');
const input = readline.createInterface({ input: process.stdin });
input.on('line', (line) => {
  const message = JSON.parse(line);
  fs.appendFileSync(process.env.FAKE_CODEX_MESSAGES, JSON.stringify(message) + '\\n');
  if (message.method === 'initialize') {
    send({ id: message.id, result: { userAgent: 'fake-codex' } });
  } else if (message.method === 'thread/start') {
    send({ id: message.id, result: { thread: { id: 'thread-test' } } });
  } else if (message.method === 'turn/start') {
    send({ id: message.id, result: { turn: { id: 'turn-test', status: 'inProgress', items: [], error: null } } });
    if (process.env.FAKE_CODEX_TERMINAL_ERROR === 'usage') {
      send({ method: 'error', params: {
        error: { message: \"You've hit your usage limit. Switch models, or try again at 3:52 PM.\" },
        willRetry: false, threadId: 'thread-test', turnId: 'turn-test',
      } });
      return;
    }
    send({ method: 'error', params: {
      error: { message: 'transient fake transport error' },
      willRetry: true, threadId: 'thread-test', turnId: 'turn-test',
    } });
    const text = process.env.FAKE_CODEX_MALFORMED === '1'
      ? JSON.stringify({ output: 'ALLOW_SESSION:typed session action', extra: true })
      : JSON.stringify({ output: 'ALLOW_SESSION:typed session action' });
    const foreign = process.env.FAKE_CODEX_FOREIGN_ONLY === '1';
    const commentary = process.env.FAKE_CODEX_COMMENTARY_ONLY === '1';
    send({ method: 'item/completed', params: {
      item: { type: 'agentMessage', id: 'message-test', text, phase: commentary ? 'commentary' : null },
      threadId: foreign ? 'thread-foreign' : 'thread-test',
      turnId: foreign ? 'turn-foreign' : 'turn-test',
      completedAtMs: 1,
    } });
    send({ method: 'turn/completed', params: {
      threadId: 'thread-test',
      turn: { id: 'turn-test', status: 'completed', items: [], error: null },
    } });
  }
});
`);
  fs.chmodSync(command, 0o755);
  return command;
}

function invoke(dir, extraEnv = {}, tools = '') {
  const schemaFile = path.join(dir, 'backend-output.schema.json');
  fs.writeFileSync(schemaFile, JSON.stringify(outputSchema));
  const env = {
    ...process.env,
    CRAFT_CODEX_CMD: fakeCodex(dir),
    FAKE_CODEX_ARGS: path.join(dir, 'args.json'),
    FAKE_CODEX_MESSAGES: path.join(dir, 'messages.jsonl'),
    ...extraEnv,
  };
  return {
    env,
    value: runClassifier({
      model: 'gpt-test-classifier',
      prompt: 'Classify this action.',
      systemPrompt: '',
      schema: outputSchema,
      schemaFile,
      timeoutMs: 5_000,
      tools,
      env,
    }),
  };
}

test('Codex classifier uses one app-server JSONL lifecycle with structured output', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-app-server.'));
  try {
    const { env, value } = invoke(dir);
    assert.deepEqual(value, { output: 'ALLOW_SESSION:typed session action' });
    assert.deepEqual(JSON.parse(fs.readFileSync(env.FAKE_CODEX_ARGS, 'utf8')), [
      '--disable', 'hooks', 'app-server', '--stdio',
    ]);

    const messages = fs.readFileSync(env.FAKE_CODEX_MESSAGES, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(messages.map(({ method }) => method), [
      'initialize', 'initialized', 'thread/start', 'turn/start',
    ]);
    assert.equal(messages[2].params.model, 'gpt-test-classifier');
    assert.equal(messages[2].params.approvalPolicy, 'never');
    assert.equal(messages[2].params.sandbox, 'read-only');
    assert.equal(messages[2].params.ephemeral, true);
    assert.deepEqual(messages[3].params.outputSchema, outputSchema);
    assert.deepEqual(messages[3].params.input, [{
      type: 'text', text: 'Classify this action.', text_elements: [],
    }]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Codex cover classifier receives read-only capability guidance', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-app-server-read-tools.'));
  try {
    const { env } = invoke(dir, {}, 'file.read,text.search,file.list');
    const messages = fs.readFileSync(env.FAKE_CODEX_MESSAGES, 'utf8').trim().split('\n').map(JSON.parse);
    assert.match(messages[2].params.developerInstructions, /read-only/i);
    assert.doesNotMatch(messages[2].params.developerInstructions, /Do not call tools/);
    assert.equal(messages[2].params.sandbox, 'read-only');
    assert.equal(messages[2].params.approvalPolicy, 'never');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Codex classifier rejects an agent message that violates the output schema', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-app-server-invalid.'));
  try {
    assert.throws(() => invoke(dir, { FAKE_CODEX_MALFORMED: '1' }), /classifier|schema|output/i);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Codex classifier never accepts an agent message from another thread or turn', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-app-server-foreign.'));
  try {
    assert.throws(() => invoke(dir, { FAKE_CODEX_FOREIGN_ONLY: '1' }), /classifier|message|turn/i);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Codex classifier never treats commentary as its final decision', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-app-server-commentary.'));
  try {
    assert.throws(() => invoke(dir, { FAKE_CODEX_COMMENTARY_ONLY: '1' }), /classifier|message|turn/i);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Codex classifier preserves a sanitized typed usage-limit failure', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-app-server-usage.'));
  try {
    assert.throws(
      () => invoke(dir, { FAKE_CODEX_TERMINAL_ERROR: 'usage' }),
      (error) => {
        assert.equal(error.classifierReason, 'usage_limit');
        assert.equal(error.retryAt, '3:52 PM');
        assert.match(error.classifierDetail, /usage limit/i);
        return true;
      },
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
