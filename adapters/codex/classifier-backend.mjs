import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { resolveCodexCommand } from './lib/command.mjs';

export function runClassifier({ model, prompt, schemaFile, timeoutMs, env }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-classifier.'));
  const output = path.join(dir, 'result.json');
  const wrapped = `${prompt}\n\nReturn a JSON object with exactly one string field named output. Put the requested decision or ingest JSON text inside that string.`;
  try {
    const args = ['exec', '--model', model, '--sandbox', 'read-only', '--ephemeral', '--output-schema', schemaFile, '--output-last-message', output, '-'];
    const result = spawnSync(resolveCodexCommand(env), args, {
      cwd: '/tmp', env, input: wrapped, encoding: 'utf8', timeout: timeoutMs, maxBuffer: 32 * 1024 * 1024,
    });
    if (result.status !== 0 || result.error) throw new Error(result.stderr || result.error?.message || 'Codex classifier failed');
    return JSON.parse(fs.readFileSync(output, 'utf8'));
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}
