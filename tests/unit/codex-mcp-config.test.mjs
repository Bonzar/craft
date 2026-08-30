import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { codexAgentMcpConfigured, setManagedAgentMcpTimeout } from '../../adapters/codex/mcp-config.mjs';

const server = '/repo/core/agents/mcp-server.mjs';
const configured = (overrides = {}) => ({
  name: 'craft_agent', enabled: true,
  transport: {
    type: 'stdio', command: 'node', args: [server],
    env: { CRAFT_AGENT_BACKEND: 'codex', CRAFT_AGENT_PERMISSION: 'read-only' },
  },
  tool_timeout_sec: 3600,
  ...overrides,
});

test('managed MCP check validates backend, permission and timeout, not only path', () => {
  assert.equal(codexAgentMcpConfigured(configured(), server), true);
  assert.equal(codexAgentMcpConfigured(configured({
    transport: { type: 'stdio', command: 'node', args: [server], env: { CRAFT_AGENT_BACKEND: 'codex' } },
  }), server), false);
  assert.equal(codexAgentMcpConfigured(configured({
    transport: { type: 'stdio', command: 'node', args: [server], env: { CRAFT_AGENT_BACKEND: 'codex', CRAFT_AGENT_PERMISSION: 'workspace-write' } },
  }), server), false);
  assert.equal(codexAgentMcpConfigured(configured({ tool_timeout_sec: null }), server), false);
});

test('timeout writer changes only the managed MCP section and is idempotent', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-mcp-config.'));
  const file = path.join(dir, 'config.toml');
  try {
    fs.writeFileSync(file, '[model]\nname = "keep"\n\n[mcp_servers.craft_agent]\ncommand = "node"\n\n[mcp_servers.craft_agent.env]\nCRAFT_AGENT_PERMISSION = "read-only"\n');
    assert.equal(setManagedAgentMcpTimeout(file), true);
    const once = fs.readFileSync(file, 'utf8');
    assert.match(once, /\[model\]\nname = "keep"/);
    assert.match(once, /\[mcp_servers\.craft_agent\]\ncommand = "node"\ntool_timeout_sec = 3600/);
    assert.equal(setManagedAgentMcpTimeout(file), false);
    assert.equal(fs.readFileSync(file, 'utf8'), once);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
