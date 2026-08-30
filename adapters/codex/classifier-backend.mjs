import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolveCodexCommand } from './lib/command.mjs';

const client = path.join(path.dirname(fileURLToPath(import.meta.url)), 'app-server-client.mjs');

function valid(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === 1
    && typeof value.output === 'string' && value.output.length > 0;
}

export function runClassifier({ model, prompt, systemPrompt, schema, timeoutMs, env }) {
  const result = spawnSync(process.execPath, [client], {
    cwd: '/tmp',
    env,
    input: JSON.stringify({
      command: resolveCodexCommand(env), model, prompt, systemPrompt, schema, timeoutMs,
    }),
    encoding: 'utf8',
    timeout: timeoutMs + 1_000,
    maxBuffer: 32 * 1024 * 1024,
  });
  if (result.status !== 0 || result.error) throw new Error('Codex classifier app-server failed');
  const output = JSON.parse(result.stdout);
  if (!valid(output)) throw new Error('Codex classifier output violates schema');
  return output;
}
