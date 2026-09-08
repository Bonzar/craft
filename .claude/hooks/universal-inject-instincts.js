#!/usr/bin/env node
// SessionStart hook (устанавливается в пользовательский слой): живой инжект
// топ-инстинктов из Craft-дока «🤖 Инстинкты агента» в код-сессии. Инжектится
// жёсткий бюджет — не больше MAX блоков, отфильтрованных по скоупу текущего
// проекта ([<проект>] или [global] в начале блока): инжект не растёт с ростом
// стора.
//
// Пустая или недоступная страница «Инстинкты» → молчание (это гипотезы, не
// правила; без них сессия полноценна). В craft-репо не работает.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { loadEnv } from './lib/env.js';
// Адаптеры рабочей копии и харнеса выбирает КРАЙ, а не общая часть.
import { commonDir } from './lib/repo-git.js';
import { harnessEnvPaths } from './lib/env-claude.js';
import { fetchText } from './lib/net.js';
// Запасной канал сети выбирает КРАЙ, а не общая часть.
import { viaExternal } from './lib/fetch-curl.js';
import { hasCommand } from './lib/system.js';

// Признак craft-репо — его собственный инжектор роутера, любой из двух версий:
// пока слой переезжает, рядом лежат обе, и признак не должен зависеть от того,
// какая осталась.
function isCraftRepo(dir) {
  if (!dir) return false;
  return ['craft-inject-router.js', 'craft-inject-router.sh']
    .some((name) => fs.existsSync(path.join(dir, '.claude', 'hooks', name)));
}
if (isCraftRepo(process.env.CLAUDE_PROJECT_DIR)) process.exit(0);

loadEnv({ commonDir, ...harnessEnvPaths() });

const pageId = process.env.CRAFT_INSTINCTS_PAGE_ID || 'b2b08ac0-b42e-382c-b1ee-8de5fb339fc6';
const max = Number(process.env.CRAFT_MAX_INSTINCTS || 6);

const base = (process.env.CRAFT_API_BASE || '').replace(/\/$/, '');
if (!base) process.exit(0);

const md = await fetchText(`${base}/blocks?id=${pageId}&maxDepth=1`, { viaExternal });
if (!md) process.exit(0);

// Скоуп текущего проекта: слаг git-remote → arc-проект → global.
function scopeOf() {
  const cwd = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const remote = spawnSync('git', ['-C', cwd, 'remote', 'get-url', 'origin'], { encoding: 'utf8' });
  if (remote.status === 0) {
    const url = (remote.stdout || '').trim().replace(/\.git$/, '');
    if (url) return path.basename(url);
  }
  if (hasCommand('arc') && spawnSync('arc', ['info'], { stdio: 'ignore' }).status === 0) {
    return 'arcadia-crm';
  }
  return 'global';
}
const scope = scopeOf();

// Строки-инстинкты: «[скоуп] при … → …». Берём свой скоуп + global, топ сверху
// (консолидация держит сильнейшие выше).
const wanted = new RegExp(`^\\[(${scope}|global)\\]`);
const picked = md.split('\n')
  .filter((line) => line.startsWith('[') && wanted.test(line))
  .slice(0, max);
if (picked.length === 0) process.exit(0);

process.stdout.write(`=== Инстинкты агента (наблюдения, НЕ канон; скоуп: ${scope}) ===\n${picked.join('\n')}\n=== Это гипотезы из прошлых сессий: применяй с проверкой; противоречат правилам или фактам — правила главнее ===\n`);
