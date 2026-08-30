#!/usr/bin/env node
// SessionStart hook: собирает craft-sync в PATH контейнера. Контейнер
// эфемерный, но репозиторий клонируется заново каждую сессию, поэтому сборка на
// старте держит бинарник доступным, не коммитя платформенный бинарный блоб.
// Любая неудача — тихий выход, чтобы сломанная сборка не клинила старт сессии.
//
// По согласию: собирает, только когда в настройках окружения задан
// CRAFT_SYNC_BUILD=1 — большинство сессий craft-sync не запускают, платить
// сборкой на каждом старте незачем. Ручная сборка в любой сессии:
//   node core/hooks/craft-build-sync.js --force
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { loadEnv } from './lib/env.js';
import { hasCommand } from './lib/system.js';

const log = (message) => process.stderr.write(`[build-craft-sync] ${message}\n`);

loadEnv();

if (process.argv[2] !== '--force' && process.env.CRAFT_SYNC_BUILD !== '1') {
  log('CRAFT_SYNC_BUILD != 1; skipping build (run with --force to build now)');
  process.exit(0);
}

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = process.env.CRAFT_PROJECT_DIR || path.resolve(here, '..', '..');
const src = path.join(repo, 'craft-sync');

if (!hasCommand('go')) {
  log('go not found; skipping');
  process.exit(0);
}
if (!fs.existsSync(path.join(src, 'main.go'))) {
  log(`source not found at ${src}; skipping`);
  process.exit(0);
}

const out = process.env.CRAFT_SYNC_BIN || path.join(os.homedir(), '.local', 'bin', 'craft-sync');
try {
  fs.mkdirSync(path.dirname(out), { recursive: true });
} catch { /* каталог уже есть или не создаётся — упадёт сама сборка */ }

const res = spawnSync('go', ['build', '-ldflags=-s -w', '-o', out, '.'], {
  cwd: src,
  env: { ...process.env, CGO_ENABLED: '0' },
  stdio: ['ignore', 'inherit', 'inherit'],
});
log(res.status === 0 ? `built ${out}` : 'build failed (non-fatal)');
