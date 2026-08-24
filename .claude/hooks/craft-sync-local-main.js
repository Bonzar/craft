#!/usr/bin/env node
// SessionStart hook (ТОЛЬКО ЛОКАЛЬНО): подтягивает локальную ветку main до
// origin/main перемоткой. Приложение режет воркри каждой сессии от локальной
// main, поэтому свежая main означает, что каждая новая сессия стартует на
// свежем коде. SessionStart-хук работает УЖЕ ВНУТРИ отрезанного воркри, так что
// текущую сессию он не перебазирует — он готовит main для следующих.
//
// Признак локальности: CRAFT_LOCAL=1, заданный только в репозиторном `.env`
// (он читается ниже). `.env` в гит не попадает и существует лишь в локальных
// чекаутах; облачные сессии клонируют из гита и получают переменные из настроек
// окружения, поэтому там CRAFT_LOCAL не задан и хук выходит, ничего не тронув.
// Признаком не может быть доступ к connect-API: его задаёт и облако.
//
// `git fetch origin main:main` двигает только САМ реф main и только перемоткой;
// рабочее дерево текущего воркри он не трогает никогда и безвредно отказывает,
// когда перемотка невозможна или main где-то выкачена.
// Старт не клинит никогда: любая неудача — запись в лог и тихий выход.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { loadEnv } from './lib/env.js';

const log = (message) => process.stderr.write(`[sync-local-main] ${message}\n`);

loadEnv();

if (process.env.CRAFT_LOCAL !== '1') {
  log('CRAFT_LOCAL != 1 (not a local session); skipping main sync');
  process.exit(0);
}

const here = path.dirname(fileURLToPath(import.meta.url));
const root = process.env.CLAUDE_PROJECT_DIR || path.resolve(here, '..', '..');

const fetched = spawnSync('git', ['-C', root, 'fetch', '--quiet', 'origin', 'main:main'], {
  stdio: 'ignore',
});
if (fetched.status === 0) {
  const head = (spawnSync('git', ['-C', root, 'rev-parse', '--short', 'main'], { encoding: 'utf8' })
    .stdout || '').trim();
  log(`local main -> origin/main (${head})`);
} else {
  log('main sync skipped (offline / non-ff / main checked out) — non-fatal');
}
