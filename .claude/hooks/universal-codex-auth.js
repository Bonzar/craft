#!/usr/bin/env node
// SessionStart hook: раскладывает вход в codex из настроек окружения и добирает
// сам клиент. Контейнер эфемерный, а браузерного входа в нём нет, поэтому без
// этого каждая сессия начиналась бы с «Not logged in» — и в автономных рутинах
// codex был бы недоступен вовсе: ввести код там некому.
//
// По согласию: работает, только когда задана CODEX_AUTH_JSON — так же, как
// craft-build-sync работает по CRAFT_SYNC_BUILD. Переменной нет — тихий выход.
//
// Fail quiet везде: старт сессии этот хук не ломает ни при каких входных
// данных. Не вышло — codex просто останется неавторизованным, и это видно
// первым же его вызовом.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { readEvent } from './lib/event-claude.js';
import { hookOnce } from './lib/once.js';
import { loadEnv } from './lib/env.js';
// Адаптеры рабочей копии и харнеса выбирает КРАЙ, а не общая часть.
import { commonDir } from './lib/repo-git.js';
import { harnessEnvPaths } from './lib/env-claude.js';
import { hasCommand } from './lib/system.js';
import { decideCodexAuth } from './lib/codex-auth.js';

const log = (m) => process.stderr.write(`[codex-auth] ${m}\n`);

const { raw, core } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);

loadEnv({ commonDir, ...harnessEnvPaths() });

const iz = process.env.CODEX_AUTH_JSON;
if (!iz) process.exit(0);

// Дом клиента: CODEX_HOME, иначе ~/.codex. Ровно так его берёт и сам codex,
// и на этом же держится проверяемость — кейс указывает свой каталог и не
// трогает рабочий вход машины.
const home = process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
const file = path.join(home, 'auth.json');

let svoy = null;
try {
  svoy = fs.readFileSync(file, 'utf8');
} catch { /* своего входа нет — это штатный случай, решение примет функция */ }

const { write, why } = decideCodexAuth(iz, svoy);
if (write) {
  try {
    fs.mkdirSync(home, { recursive: true, mode: 0o700 });
    // Права задаются вторым шагом: mkdir с mode подчиняется umask, а у файла с
    // refresh-токеном права — не косметика.
    fs.chmodSync(home, 0o700);
    fs.writeFileSync(file, iz, { mode: 0o600 });
    fs.chmodSync(file, 0o600);
    log(`вход разложен в ${file}: ${why}`);
  } catch (err) {
    log(`вход разложить не удалось: ${err && err.message}`);
  }
} else {
  log(`вход не тронут: ${why}`);
}

// Клиент проверяется ВСЕГДА, независимо от того, тронули мы вход или нет.
// Иначе достаточно одной неудачной установки: первый старт кладёт вход и
// спотыкается на npm, а каждый следующий видит «свой вход не старее», выходит
// раньше — и клиента не будет уже никогда.
//
// Ставим, только когда команды не нашлось, то есть один раз на контейнер.
if (hasCommand('codex')) {
  log('клиент codex на месте');
  process.exit(0);
}
if (!hasCommand('npm')) {
  log('npm не найден — клиент не поставить, вход останется лежать');
  process.exit(0);
}
const res = spawnSync('npm', ['i', '-g', '@openai/codex'], { stdio: 'ignore', timeout: 600000 });
log(res.status === 0 ? 'клиент codex поставлен' : 'клиент codex поставить не удалось');
