#!/usr/bin/env node
// Stop-хук (БЛОКИРУЮЩИЙ): гейт качества JS/TS-правок сессии. Портирован из ECC
// (stop-format-typecheck.js): агент не заканчивает ход с неотформатированным
// или не проходящим типы кодом — нарушения возвращаются ему на починку.
//
// Self-gating — работает только когда:
//   - в проекте (CRAFT_PROJECT_DIR или рабочий каталог) есть tsconfig.json
//     или package.json;
//   - за сессию правились .ts/.tsx/.js/.jsx (по транскрипту, как в
//     universal-check-console-log).
// Прогоняет по изменённым файлам: prettier --check (если prettier доступен
// через npx --no-install), затем tsc --noEmit (если есть tsconfig и tsc
// доступен). Всё с таймаутом 120 с; нет тулзы или таймаут — fail open, не блок.
// Ошибки tsc фильтруются до правленных файлов: чужие (доправочные) ошибки
// проекта ход не блокируют.
//
// Есть нарушения → блок с их перечнем: агент чинит и завершает снова.
// Анти-зацикливание: повторный Stop (env CRAFT_STOP_HOOK_ACTIVE=true или
// stop_hook_active во входе) → тихий выход.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { readEvent } from './lib/event.js';
import { block } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { sourceFiles } from './lib/transcript.js';
import { sessionEditedFiles } from './lib/session-files.js';

// Анти-зацикливание: этот Stop уже вызван из-под стоп-хука → пропуск.
if (process.env.CRAFT_STOP_HOOK_ACTIVE === 'true') process.exit(0);

const { raw, event, transcript } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (event.stopActive === true) process.exit(0);

const proj = process.env.CRAFT_PROJECT_DIR || process.env.PWD || process.cwd();
if (!fs.existsSync(path.join(proj, 'tsconfig.json'))
    && !fs.existsSync(path.join(proj, 'package.json'))) process.exit(0);

const files = sourceFiles(sessionEditedFiles(event.sessionEditedFiles));
if (files.length === 0) process.exit(0);

// Запуск в каталоге проекта с общим бюджетом 120 с. Таймаут и отсутствие тулзы
// неотличимы намеренно: и то, и другое — fail open, гейт не блокирует.
function run(cmd, args) {
  const res = spawnSync(cmd, args, {
    cwd: proj, encoding: 'utf8', timeout: 120000, maxBuffer: 16 * 1024 * 1024,
  });
  const timedOut = Boolean(res.error) || res.signal !== null;
  return { ok: !timedOut && res.status === 0, timedOut, out: `${res.stdout || ''}${res.stderr || ''}` };
}

function available(...args) {
  return run('npx', args).ok;
}

const head = (text, n) => text.split('\n').slice(0, n).join('\n').replace(/\n+$/, '');

let problems = '';

// --- prettier --check по правленным файлам -----------------------------------
if (available('--no-install', 'prettier', '--version')) {
  const res = run('npx', ['--no-install', 'prettier', '--check', ...files]);
  if (!res.ok && !res.timedOut) {
    problems += `prettier --check (почини: npx prettier --write <файлы>):\n${head(res.out, 30)}\n`;
  }
}

// --- tsc --noEmit (только при tsconfig; ошибки — лишь по правленным файлам) --
if (fs.existsSync(path.join(proj, 'tsconfig.json')) && available('--no-install', 'tsc', '--version')) {
  const res = run('npx', ['--no-install', 'tsc', '--noEmit', '--pretty', 'false']);
  if (!res.ok && !res.timedOut) {
    const rel = files.map((f) => (f.startsWith(`${proj}/`) ? f.slice(proj.length + 1) : f));
    const filtered = head(res.out.split('\n').filter((l) => rel.some((r) => l.includes(r))).join('\n'), 30);
    if (filtered) {
      problems += `tsc --noEmit (ошибки типов в правленных файлах):\n${filtered}\n`;
    }
  }
}

if (problems) {
  block(`[stop-hook] Stop-гейт качества: в правленных за сессию файлах есть нарушения — почини их и заверши ход снова.\n${problems}`);
}
