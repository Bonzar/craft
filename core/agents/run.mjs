#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { loadAgent, loadConfig } from './lib/registry.mjs';
import { acquire, childPermission } from './lib/limits.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function request() {
  const text = fs.readFileSync(0, 'utf8').trim();
  const value = text ? JSON.parse(text) : {};
  const allowed = new Set(['agentId', 'task', 'context', 'backend', 'depth', 'sessionId', 'cwd']);
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('request must be an object');
  for (const key of Object.keys(value)) if (!allowed.has(key)) throw new Error(`unknown request field: ${key}`);
  return { ...value, agentId: value.agentId || process.argv[2] };
}

function backendName(input) {
  const value = input.backend || process.env.CRAFT_AGENT_BACKEND || process.env.CRAFT_RUNTIME || '';
  if (!/^[a-z0-9][a-z0-9-]*$/.test(value)) throw new Error('runtime backend is required');
  return value;
}

async function backendFor(name) {
  const dir = process.env.CRAFT_ADAPTER_DIR || path.join(repo, 'adapters', name);
  const config = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  const module = await import(pathToFileURL(path.join(dir, 'agent-backend.mjs')).href);
  if (typeof module.runAgent !== 'function') throw new Error(`agent backend has no runAgent: ${name}`);
  return { config, run: module.runAgent };
}

function buildPrompt(agent, input, depth) {
  const context = input.context === undefined ? '(none)' : JSON.stringify(input.context, null, 2);
  return `${agent.prompt}\n\n## Universal agent runtime\nYou are ${agent.id}, depth ${depth}. The adapter exposes the core capability agent.invoke with arguments {agentId, task, context}; use it to invoke any registered child. Never depend on a provider-specific invocation mechanism. The core preserves the caller's permission ceiling, depth and concurrency limits, and returns one typed result.\n\n## Assignment\n${input.task}\n\n## Context\n${context}`;
}

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

async function invoke(input) {
  const config = loadConfig();
  const agent = loadAgent(String(input.agentId || ''));
  const backend = backendName(input);
  const depth = Number(input.depth ?? process.env.CRAFT_AGENT_DEPTH ?? 0);
  if (!Number.isInteger(depth) || depth < 0 || depth > config.limits.maxDepth) throw new Error('agent depth limit reached');
  if (typeof input.task !== 'string' || !input.task.trim()) throw new Error('task is required');
  const bytes = Buffer.byteLength(JSON.stringify(input.context ?? null));
  if (bytes > config.limits.maxContextBytes) throw new Error('agent context is too large');
  const permission = childPermission(process.env.CRAFT_AGENT_PERMISSION, agent.permission);
  const session = input.sessionId || process.env.CRAFT_AGENT_SESSION_ID || process.env.CRAFT_SESSION_ID || 'default';
  const release = acquire(session, config.limits.maxConcurrency);
  const adapter = await backendFor(backend);
  const model = adapter.config.agentModels?.[agent.model_profile];
  if (!model) throw new Error(`backend ${backend} has no model for profile ${agent.model_profile}`);
  const env = { ...process.env, CRAFT_AGENT_BACKEND: backend, CRAFT_AGENT_DEPTH: String(depth + 1), CRAFT_AGENT_PERMISSION: permission, CRAFT_AGENT_SESSION_ID: session, CRAFT_AGENT_PARENT: agent.id };
  try {
    const cwd = input.cwd || process.cwd();
    if (!fs.statSync(cwd).isDirectory()) throw new Error(`cwd is not a directory: ${cwd}`);
    const args = { prompt: buildPrompt(agent, input, depth), model, permission, cwd, timeoutMs: config.limits.timeoutMs, env };
    const result = adapter.run(args);
    return { schemaVersion: 1, status: 'ok', agentId: agent.id, backend, model, depth, result };
  } finally { release(); }
}

try {
  emit(await invoke(request()));
} catch (error) {
  emit({ schemaVersion: 1, status: 'error', agentId: process.argv[2] || 'unknown', backend: process.env.CRAFT_AGENT_BACKEND || 'unknown', model: 'unknown', depth: Number(process.env.CRAFT_AGENT_DEPTH || 0), result: '', error: String(error?.message || error) });
  process.exitCode = 1;
}
