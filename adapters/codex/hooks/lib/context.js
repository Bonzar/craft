// Live Craft context shared by agent adapters. Fetching and caching belong to
// The provider adapter chooses how the shared material reaches a session.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');

function read(file) {
  try { return fs.readFileSync(file, 'utf8').trim(); } catch { return ''; }
}

function ensureDir(dir) {
  try { fs.mkdirSync(dir, { recursive: true }); return true; } catch { return false; }
}

function run(script, extraEnv) {
  return spawnSync(process.execPath, [script], {
    cwd: repoRoot,
    env: { ...process.env, CRAFT_PROJECT_DIR: repoRoot, ...extraEnv },
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    timeout: 110_000,
  });
}

function refreshRouter(snapshot) {
  const tmp = `${snapshot}.refresh.${process.pid}`;
  const result = run(path.join(repoRoot, 'core', 'hooks', 'craft-inject-router.js'), {
    CRAFT_ROUTER_SNAPSHOT: tmp,
  });
  if (result.stderr) process.stderr.write(result.stderr);
  const fresh = read(tmp);
  if (!fresh) {
    try { fs.rmSync(tmp, { force: true }); } catch { /* nothing to remove */ }
    return false;
  }
  try {
    fs.renameSync(tmp, snapshot);
    return true;
  } catch {
    try { fs.rmSync(tmp, { force: true }); } catch { /* nothing to remove */ }
    return false;
  }
}

function refreshCodeRules(snapshot) {
  const result = run(path.join(repoRoot, 'core', 'hooks', 'universal-inject-code-rules.js'), {
    CRAFT_CODE_RULES_SNAPSHOT: snapshot,
    CRAFT_USER_INSTRUCTION_FILE: path.join(repoRoot, 'AGENTS.md'),
    CODE_RULES_FORCE: '1',
    CODE_RULES_FORCE_SNAPSHOT: '1',
  });
  if (result.stderr) process.stderr.write(result.stderr);
  return result.stdout || '';
}

export function buildCraftContext(event = {}, env = process.env) {
  const codexHome = env.CODEX_HOME || path.join(os.homedir(), '.codex');
  const liveDir = env.CRAFT_CODEX_LIVE_DIR || path.join(codexHome, 'craft-live');
  const explicitRouter = Object.prototype.hasOwnProperty.call(env, 'CRAFT_CODEX_ROUTER_SNAPSHOT');
  const routerSnapshot = env.CRAFT_CODEX_ROUTER_SNAPSHOT || path.join(liveDir, 'router.md');
  const codeSnapshot = env.CRAFT_CODEX_CODE_SNAPSHOT || path.join(liveDir, 'code-rules.md');
  const skipRefresh = env.CRAFT_CODEX_SKIP_REFRESH === '1' || event.source === 'compact';

  ensureDir(liveDir);
  let codeOutput = '';
  if (!skipRefresh) {
    refreshRouter(routerSnapshot);
    codeOutput = refreshCodeRules(codeSnapshot);
  }

  const router = read(routerSnapshot)
    || (explicitRouter ? '' : read(path.join(repoRoot, '.craft', 'router-context.md')));
  const codeRules = read(codeSnapshot)
    || (/^=== Craft: «⚙️ Правила кода»/m.test(codeOutput) ? codeOutput.trim() : '');
  const parts = [router, codeRules].filter(Boolean);
  if (!parts.length) {
    parts.push('⚠️ Craft router was not loaded. Before Craft work, read the router live with blocks get --depth -1. Before code changes, load the Craft code-rules dispatcher.');
  }
  return parts.join('\n\n');
}
