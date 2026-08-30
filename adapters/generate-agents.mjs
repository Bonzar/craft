#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { listAgents } from '../core/agents/lib/registry.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function option(name, fallback) {
  const at = process.argv.indexOf(name);
  return at >= 0 && process.argv[at + 1] ? path.resolve(process.argv[at + 1]) : fallback;
}

function toml(value) { return JSON.stringify(String(value)); }

function write(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${text.replace(/\n+$/, '')}\n`);
}

function config(name) {
  return JSON.parse(fs.readFileSync(path.join(repo, 'adapters', name, 'config.json'), 'utf8'));
}

function nativeInstructions(harness, agent, definition) {
  return `You are the native ${harness} adapter for canonical agent ${agent.id}. Read the canonical role definition at ${definition} completely before acting, then perform that role directly in this already-running subagent. The definition is authoritative; do not copy or reinterpret its business logic here. Do not launch another client CLI and do not redispatch yourself through an external model process. Preserve the permission already assigned by the harness. If the role needs a registered child, call the adapter-provided agent.invoke capability with its canonical identifier; the shared core enforces registry, permission, depth, concurrency, context and result contracts. Return the completed role result to the caller.`;
}

const claudeDir = option('--claude-dir', path.join(repo, '.claude', 'agents'));
const codexDir = option('--codex-dir', path.join(repo, '.codex', 'agents'));
const referenceRoot = option('--reference-root', repo);
const claudeConfig = config('claude');
const codexConfig = config('codex');

for (const agent of listAgents()) {
  const definition = path.join(referenceRoot, 'core', 'agents', 'definitions', `${agent.id}.md`);
  const claudeTools = agent.permission === 'read-only'
    ? 'Read, Grep, Glob, mcp__craft_agent__invoke'
    : 'Read, Write, Edit, Bash, Grep, Glob, mcp__craft_agent__invoke';
  write(path.join(claudeDir, `${agent.id}.md`), `---
name: ${agent.id}
description: ${agent.description}
tools: ${claudeTools}
model: ${claudeConfig.agentModels[agent.model_profile]}
---

${nativeInstructions('Claude', agent, definition)}`);

  const sandbox = agent.permission === 'read-only' ? 'read-only' : 'workspace-write';
  write(path.join(codexDir, `${agent.id}.toml`), `name = ${toml(agent.id)}
description = ${toml(agent.description)}
model = ${toml(codexConfig.agentModels[agent.model_profile])}
model_reasoning_effort = "high"
sandbox_mode = ${toml(sandbox)}
developer_instructions = ${toml(nativeInstructions('Codex', agent, definition))}`);
}
