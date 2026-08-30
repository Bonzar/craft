import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { childPermission } from '../../core/agents/lib/limits.mjs';
import { listAgents } from '../../core/agents/lib/registry.mjs';
import { codexAgentArgs } from '../../adapters/codex/agent-backend.mjs';
import { claudeAgentArgs } from '../../adapters/claude/agent-backend.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

test('canonical registry is provider-neutral and contains every review role', () => {
  const agents = listAgents();
  const ids = agents.map((agent) => agent.id);
  for (const id of ['comment-analyzer', 'plan-critic', 'plan-critic-verdict', 'review-synthesizer', 'typescript-reviewer']) assert.ok(ids.includes(id));
  for (const agent of agents) {
    assert.doesNotMatch(agent.description, /\b(Task|Agent|Workflow|spawn_agent|create_thread|send_message_to_thread)\b/);
    assert.match(agent.permission, /^(read-only|workspace-write)$/);
  }
});

test('provider model profiles select Haiku and Spark without leaking into core definitions', () => {
  const claude = JSON.parse(fs.readFileSync(path.join(repo, 'adapters/claude/config.json'), 'utf8'));
  const codex = JSON.parse(fs.readFileSync(path.join(repo, 'adapters/codex/config.json'), 'utf8'));
  assert.equal(claude.agentModels.fast, 'haiku');
  assert.equal(codex.agentModels.fast, 'gpt-5.3-codex-spark');
  assert.equal(codex.agentModels.balanced, 'gpt-5.6-terra');
  assert.equal(codex.agentModels.deep, 'gpt-5.6-sol');
  for (const agent of listAgents()) {
    const source = fs.readFileSync(agent.file, 'utf8');
    assert.doesNotMatch(source, /^model:/m);
    assert.doesNotMatch(source, /^tools:/m);
  }
});

test('a read-only parent can only narrow a child permission', () => {
  assert.equal(childPermission('read-only', 'workspace-write'), 'read-only');
  assert.equal(childPermission('workspace-write', 'read-only'), 'read-only');
  assert.equal(childPermission('workspace-write', 'workspace-write'), 'workspace-write');
  assert.equal(childPermission(undefined, 'workspace-write'), 'read-only');
  assert.equal(childPermission('unexpected', 'workspace-write'), 'read-only');
});

test('backend exposes universal child invocation without granting write shell to read-only agents', () => {
  const claude = claudeAgentArgs({ model: 'fast', permission: 'read-only', mcpConfig: '/tmp/agents.json' });
  const allowed = claude[claude.indexOf('--allowedTools') + 1];
  assert.doesNotMatch(allowed, /(?:^|,)Bash(?:,|$)/);
  assert.match(allowed, /mcp__craft_agent__invoke/);
  assert.ok(claude.includes('--mcp-config'));

  const codex = codexAgentArgs({ model: 'fast', permission: 'read-only', output: '/tmp/out' });
  assert.ok(codex.includes('read-only'));
  assert.ok(codex.some((arg) => arg.includes('mcp_servers.craft_agent.command')));
  assert.ok(codex.some((arg) => arg.includes('mcp_servers.craft_agent.args')));
});

test('adapter generator emits thin native files from the same registry', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'craft-agent-adapters.'));
  const claude = path.join(dir, 'claude');
  const codex = path.join(dir, 'codex');
  execFileSync(process.execPath, [path.join(repo, 'adapters/generate-agents.mjs'), '--claude-dir', claude, '--codex-dir', codex, '--reference-root', repo]);
  const claudeText = fs.readFileSync(path.join(claude, 'comment-analyzer.md'), 'utf8');
  const codexText = fs.readFileSync(path.join(codex, 'comment-analyzer.toml'), 'utf8');
  assert.match(claudeText, /core\/agents\/definitions\/comment-analyzer\.md/);
  assert.match(codexText, /model = "gpt-5\.3-codex-spark"/);
  assert.match(codexText, /sandbox_mode = "read-only"/);
  assert.doesNotMatch(claudeText, /# Comment Analyzer/);
  assert.match(claudeText, /mcp__craft_agent__invoke/);
  assert.doesNotMatch(claudeText.split('---')[1], /\bBash\b/);
  for (const text of [claudeText, codexText]) {
    assert.match(text, /Read the canonical role definition/);
    assert.match(text, /core\/agents\/definitions\/comment-analyzer\.md/);
    assert.doesNotMatch(text, /core\/agents\/run\.mjs/);
    assert.doesNotMatch(text, /Do not perform the business role directly/);
    assert.match(text, /agent\.invoke/);
  }
});
