#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import normalizer from '../../shared/hooks/normalize.cjs';
import intentState from '../../shared/hooks/intent-state.cjs';
import { renderClaudeOutput } from './lib/output.js';

const { normalizeHarnessEvent } = normalizer;
const { originalIntent } = intentState;

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const raw = fs.readFileSync(0, 'utf8');
let event = {};
try { event = raw.trim() ? JSON.parse(raw) : {}; } catch { /* core handles malformed input */ }
const clientHome = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const canonical = normalizeHarnessEvent(event, 'claude', originalIntent(event, 'claude'), {
  planRoot: path.join(clientHome, 'plans'),
});
const input = `${JSON.stringify(canonical)}\n`;
const env = {
  ...process.env,
  CRAFT_RUNTIME: 'claude',
  CRAFT_PROJECT_DIR: process.env.CLAUDE_PROJECT_DIR || canonical.event.cwd || process.cwd(),
  CRAFT_SESSION_ID: process.env.CLAUDE_CODE_SESSION_ID || canonical.event.sessionId || '',
  CRAFT_STOP_HOOK_ACTIVE: process.env.CLAUDE_STOP_HOOK_ACTIVE || '',
  CRAFT_CLIENT_HOME: clientHome,
  CRAFT_PERSISTENT_STATE_DIR: path.join(clientHome, 'craft-state'),
  CRAFT_ENV_FILE: path.join(os.homedir(), '.claude', 'craft.env'),
  CRAFT_USER_INSTRUCTION_FILE: path.join(os.homedir(), '.claude', 'CLAUDE.md'),
  CRAFT_BEHAVIOR_SNAPSHOT: path.join(os.homedir(), '.claude', 'craft-live', 'behavior-rules.md'),
  CRAFT_CODE_RULES_SNAPSHOT: path.join(os.homedir(), '.claude', 'craft-live', 'code-rules.md'),
  CRAFT_HOOK_CAPTURE: '1',
};
const scope = process.argv[2] === 'universal' ? 'universal' : 'project';
const result = spawnSync(process.execPath, [path.join(repo, 'core', 'hooks', 'dispatch.js'), scope], {
  input, env, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
});
if (result.stderr) process.stderr.write(result.stderr);
let outputs = [];
try { outputs = JSON.parse(result.stdout || '[]'); } catch { /* fail closed decisions remain unavailable */ }
const rendered = renderClaudeOutput(event.hook_event_name || '', Array.isArray(outputs) ? outputs : []);
if (rendered) process.stdout.write(rendered);
process.exitCode = result.status ?? 0;
