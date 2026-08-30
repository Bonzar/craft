#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { renderCodexOutput } from './lib/output.js';
import normalizer from '../../shared/hooks/normalize.cjs';
import intentState from '../../shared/hooks/intent-state.cjs';

const { normalizeHarnessEvent } = normalizer;
const { originalIntent } = intentState;

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const raw = fs.readFileSync(0, 'utf8');
let event = {};
try { event = raw.trim() ? JSON.parse(raw) : {}; } catch { /* core handles malformed input */ }
const canonical = normalizeHarnessEvent(event, 'codex', originalIntent(event, 'codex'));
const clientHome = process.env.CODEX_HOME || path.join(process.env.HOME || '', '.codex');
const name = event.hook_event_name || '';
const input = `${JSON.stringify(canonical)}\n`;
const env = {
  ...process.env,
  CRAFT_RUNTIME: 'codex',
  CRAFT_PROJECT_DIR: canonical.event.cwd || process.cwd(),
  CRAFT_SESSION_ID: canonical.event.sessionId || '',
  CRAFT_CLIENT_HOME: clientHome,
  CRAFT_PERSISTENT_STATE_DIR: path.join(clientHome, 'craft-state'),
  CRAFT_HOOK_CAPTURE: '1',
  CRAFT_HOOK_EXTENSION_DIR: path.join(repo, 'adapters', 'codex', 'hooks'),
  CRAFT_EVENT_JSON: JSON.stringify(canonical),
};
const result = spawnSync(process.execPath, [path.join(repo, 'core', 'hooks', 'dispatch.js'), 'client'], {
  input, env, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
});
if (result.stderr) process.stderr.write(result.stderr);
let outputs = [];
try { outputs = JSON.parse(result.stdout || '[]'); } catch { /* fail open */ }
const rendered = renderCodexOutput(name, Array.isArray(outputs) ? outputs : []);
if (rendered) process.stdout.write(rendered);
process.exitCode = result.status ?? 0;
