import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolveCodexCommand } from './lib/command.mjs';
import {
  classifierFailure, inferClassifierReason, normalizeClassifierFailure,
} from '../../core/classifier/failure.mjs';

const client = path.join(path.dirname(fileURLToPath(import.meta.url)), 'app-server-client.mjs');

function valid(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === 1
    && typeof value.output === 'string' && value.output.length > 0;
}

function childFailure(result) {
  const prefix = 'CRAFT_CLASSIFIER_BACKEND_ERROR ';
  const line = String(result.stderr || '').split('\n').find((value) => value.startsWith(prefix));
  if (line) {
    try {
      const value = JSON.parse(line.slice(prefix.length));
      return classifierFailure(value.reason, value);
    } catch { /* fall through to the safe process error */ }
  }
  const detail = result.error?.message || result.stderr || 'classifier app-server failed';
  const reason = result.error?.code === 'ETIMEDOUT' ? 'timeout' : inferClassifierReason(detail);
  return classifierFailure(reason, { detail });
}

export function runClassifier({ model, prompt, systemPrompt, schema, tools, timeoutMs, env }) {
  const result = spawnSync(process.execPath, [client], {
    cwd: '/tmp',
    env,
    input: JSON.stringify({
      command: resolveCodexCommand(env), model, prompt, systemPrompt, schema, tools, timeoutMs,
    }),
    encoding: 'utf8',
    timeout: timeoutMs + 1_000,
    maxBuffer: 32 * 1024 * 1024,
  });
  if (result.status !== 0 || result.error) throw childFailure(result);
  try {
    const output = JSON.parse(result.stdout);
    if (!valid(output)) throw classifierFailure('invalid_output', { detail: 'classifier output violates schema' });
    return output;
  } catch (error) {
    const failure = normalizeClassifierFailure(error);
    throw classifierFailure(
      failure.reason === 'unknown' ? 'invalid_output' : failure.reason,
      failure,
    );
  }
}
