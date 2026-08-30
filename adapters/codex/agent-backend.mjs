import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolveCodexCommand } from './lib/command.mjs';

const agentServer = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'core', 'agents', 'mcp-server.mjs');

export function codexAgentArgs({ model, permission, output }) {
  const sandbox = permission === 'read-only' ? 'read-only' : 'workspace-write';
  return [
    'exec', '--model', model, '--sandbox', sandbox, '--ephemeral', '--output-last-message', output,
    '-c', `mcp_servers.craft_agent.command=${JSON.stringify(process.execPath)}`,
    '-c', `mcp_servers.craft_agent.args=[${JSON.stringify(agentServer)}]`,
    '-c', 'mcp_servers.craft_agent.tool_timeout_sec=3600',
    '-',
  ];
}

export function runAgent({ prompt, model, permission, cwd, timeoutMs, env }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-agent-codex.'));
  const output = path.join(dir, 'last-message.txt');
  const args = codexAgentArgs({ model, permission, output });
  try {
    const result = spawnSync(resolveCodexCommand(env), args, {
      cwd, env, input: prompt, encoding: 'utf8', timeout: timeoutMs, maxBuffer: 32 * 1024 * 1024,
    });
    if (result.status !== 0 || result.error) throw new Error(result.stderr || result.error?.message || 'Codex backend failed');
    return fs.readFileSync(output, 'utf8').trim();
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}
