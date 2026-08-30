import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const agentServer = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'core', 'agents', 'mcp-server.mjs');

export function claudeAgentArgs({ model, permission, mcpConfig }) {
  const tools = permission === 'read-only'
    ? 'Read,Grep,Glob,mcp__craft_agent__invoke'
    : 'Read,Write,Edit,Bash,Grep,Glob,mcp__craft_agent__invoke';
  return [
    '-p', '--model', model, '--output-format', 'json', '--allowedTools', tools,
    '--mcp-config', mcpConfig, '--no-session-persistence',
  ];
}

export function runAgent({ prompt, model, permission, cwd, timeoutMs, env }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-agent-claude.'));
  const mcpConfig = path.join(dir, 'mcp.json');
  fs.writeFileSync(mcpConfig, JSON.stringify({
    mcpServers: { craft_agent: { type: 'stdio', command: process.execPath, args: [agentServer] } },
  }));
  try {
    const args = claudeAgentArgs({ model, permission, mcpConfig });
    const result = spawnSync(env.CRAFT_CLAUDE_CMD || 'claude', args, {
      cwd, env, input: prompt, encoding: 'utf8', timeout: timeoutMs, maxBuffer: 32 * 1024 * 1024,
    });
    if (result.status !== 0 || result.error) throw new Error(result.stderr || result.error?.message || 'Claude backend failed');
    try {
      const parsed = JSON.parse(result.stdout);
      return String(parsed.result ?? parsed.structured_output ?? '');
    } catch { return String(result.stdout || '').trim(); }
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}
