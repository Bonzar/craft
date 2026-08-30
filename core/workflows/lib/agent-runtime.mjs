import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadConfig } from '../../agents/lib/registry.mjs';

const runner = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'agents', 'run.mjs');

export function invokeAgent(request, options = {}) {
  const env = {
    ...process.env,
    ...(options.env || {}),
    ...(request.backend ? { CRAFT_AGENT_BACKEND: request.backend } : {}),
  };
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [runner, request.agentId], {
      cwd: request.cwd || process.cwd(), env, stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => {
      let value;
      try { value = JSON.parse(stdout.trim()); } catch { reject(new Error(`invalid agent result: ${stdout || stderr}`)); return; }
      const exact = new Set(['schemaVersion', 'status', 'agentId', 'backend', 'model', 'depth', 'result', 'error']);
      if (!value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).some((key) => !exact.has(key))
        || value.schemaVersion !== 1 || !['ok', 'error'].includes(value.status)
        || typeof value.agentId !== 'string' || typeof value.backend !== 'string'
        || typeof value.model !== 'string' || !Number.isInteger(value.depth)
        || typeof value.result !== 'string'
        || (value.status === 'error' && typeof value.error !== 'string')) {
        reject(new Error('agent result does not match the core schema'));
        return;
      }
      if (code !== 0 || value.status !== 'ok') reject(new Error(value.error || stderr || `agent exited ${code}`));
      else resolve(value);
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export async function mapAgents(items, worker) {
  const limit = loadConfig().limits.maxConcurrency;
  const results = new Array(items.length);
  let next = 0;
  async function lane() {
    while (next < items.length) {
      const index = next;
      next += 1;
      try { results[index] = { ok: true, value: await worker(items[index], index) }; }
      catch (error) { results[index] = { ok: false, error: String(error?.message || error) }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => lane()));
  return results;
}
