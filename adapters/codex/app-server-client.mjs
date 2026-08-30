#!/usr/bin/env node
import { spawn } from 'node:child_process';
import readline from 'node:readline';

const request = JSON.parse(await new Promise((resolve, reject) => {
  let text = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { text += chunk; });
  process.stdin.on('end', () => resolve(text));
  process.stdin.on('error', reject);
}));

const timeoutMs = Number(request.timeoutMs);
if (!request.command || !request.model || !request.prompt || !request.schema
  || !Number.isFinite(timeoutMs) || timeoutMs <= 0) {
  throw new Error('invalid classifier app-server request');
}

const child = spawn(request.command, ['--disable', 'hooks', 'app-server', '--stdio'], {
  cwd: '/tmp',
  env: process.env,
  stdio: ['pipe', 'pipe', 'pipe'],
});

let settled = false;
let finalMessage = null;
let threadId = null;
let turnId = null;
let stderr = '';

const timer = setTimeout(() => fail('classifier app-server timed out'), timeoutMs);
child.stderr.setEncoding('utf8');
child.stderr.on('data', (chunk) => {
  if (stderr.length < 8_192) stderr += chunk.slice(0, 8_192 - stderr.length);
});

function stop() {
  clearTimeout(timer);
  child.stdin.end();
  child.kill('SIGTERM');
}

function fail(message) {
  if (settled) return;
  settled = true;
  stop();
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
}

function send(message) {
  if (!settled) child.stdin.write(`${JSON.stringify(message)}\n`);
}

function exactOutput(text) {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)
    || Object.keys(parsed).length !== 1
    || typeof parsed.output !== 'string' || parsed.output.length === 0) {
    throw new Error('classifier output violates schema');
  }
  return parsed;
}

function complete() {
  try {
    if (finalMessage === null) throw new Error('classifier returned no final agent message');
    const output = exactOutput(finalMessage);
    settled = true;
    stop();
    process.stdout.write(`${JSON.stringify(output)}\n`);
  } catch (error) {
    fail(error.message);
  }
}

function handle(message) {
  if (!message || typeof message !== 'object') return fail('invalid app-server message');
  if (message.error) return fail('classifier app-server RPC failed');

  if (message.id === 1 && !message.method) {
    send({ method: 'initialized' });
    send({ id: 2, method: 'thread/start', params: {
      model: request.model,
      cwd: '/tmp',
      approvalPolicy: 'never',
      sandbox: 'read-only',
      developerInstructions: [
        request.systemPrompt || '',
        'Return exactly one JSON object satisfying the supplied output schema. Put only the requested classifier decision in the output string. Do not call tools.',
      ].filter(Boolean).join('\n\n'),
      ephemeral: true,
      dynamicTools: [],
    } });
    return;
  }

  if (message.id === 2 && !message.method) {
    threadId = message.result?.thread?.id;
    if (typeof threadId !== 'string' || !threadId) return fail('classifier thread did not start');
    send({ id: 3, method: 'turn/start', params: {
      threadId,
      input: [{ type: 'text', text: request.prompt, text_elements: [] }],
      model: request.model,
      outputSchema: request.schema,
    } });
    return;
  }

  if (message.id === 3 && !message.method) {
    turnId = message.result?.turn?.id;
    if (typeof turnId !== 'string' || !turnId) fail('classifier turn did not start');
    return;
  }

  if (message.method === 'item/completed' && message.params?.item?.type === 'agentMessage') {
    if (message.params.threadId !== threadId || message.params.turnId !== turnId) return;
    if (![null, 'final_answer'].includes(message.params.item.phase)) return;
    if (typeof message.params.item.text !== 'string') return fail('invalid classifier agent message');
    finalMessage = message.params.item.text;
    return;
  }

  if (message.method === 'turn/completed') {
    if (message.params?.threadId !== threadId || message.params?.turn?.id !== turnId) return;
    if (message.params?.turn?.status !== 'completed') return fail('classifier turn failed');
    complete();
    return;
  }

  if (message.id !== undefined && message.method) fail('classifier requested unsupported interaction');
  if (message.method === 'error') fail('classifier app-server reported an error');
}

const lines = readline.createInterface({ input: child.stdout });
lines.on('line', (line) => {
  if (settled || !line.trim()) return;
  try { handle(JSON.parse(line)); } catch { fail('invalid classifier app-server JSON'); }
});

child.on('error', () => fail('classifier app-server could not start'));
child.on('exit', (code) => {
  if (!settled) fail(code === 0 ? 'classifier app-server exited early' : 'classifier app-server failed');
});

send({
  id: 1,
  method: 'initialize',
  params: {
    clientInfo: { name: 'craft-classifier', title: 'Craft classifier', version: '1' },
    capabilities: { experimentalApi: true, requestAttestation: false },
  },
});
