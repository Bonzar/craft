#!/usr/bin/env node
// SessionStart hook (устанавливается в пользовательский слой): живой инжект
// ЯДРА «Правил кода» из Craft в код-сессии вне craft-репо. Канон — Craft;
// никакого коммитнутого кэша.
//
// Инжектится ТОЛЬКО корень дока (maxDepth=1): ядро + диспетчер доменных
// страниц. Полные доменные страницы (TypeScript, React, …) агент читает
// целиком по триггеру домена — так велит сам диспетчер. Сжимать их в инжект
// нельзя (урок в «Обслуживании памяти»).
//
// В craft-репо ядро по умолчанию не инжектится: craft-сессии код не пишут. Но
// сессия, запущенная В craft-репо со ВТОРЫМ рабочим корнем снаружи (craft-local
// первой директорией ради воркри, маунт кода второй), — как раз код-сессия, и
// без этого исключения она оставалась без правил кода вовсе: ни лимитов, ни
// адресации файла ссылкой. Постоянная запись в permissions под признак не
// годится — она стоит у всех сессий подряд и ничего не различает; корень,
// заданный при ЗАПУСКЕ сессии, различает.
//
// Fail quiet: нет доступа или сети → короткая директива-фолбек.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { loadEnv } from './lib/env.js';
// Адаптеры рабочей копии и харнеса выбирает КРАЙ, а не общая часть.
import { commonDir } from './lib/repo-git.js';
import { harnessEnvPaths } from './lib/env-claude.js';
import { fetchText } from './lib/net.js';
// Запасной канал сети выбирает КРАЙ, а не общая часть.
import { viaExternal } from './lib/fetch-curl.js';
import { utcStamp } from './lib/system.js';

// Корни, заданные при запуске сессии: аргументы --add-dir у процесса-предка.
// Путь с пробелом здесь не разберётся и просто не будет учтён — тогда сессия
// останется без ядра, как и раньше, а не получит мусор.
function extraDirs() {
  if ('CODE_RULES_EXTRA_DIRS' in process.env) {
    return (process.env.CODE_RULES_EXTRA_DIRS || '').split(/\s+/).filter(Boolean);
  }
  let pid = process.pid;
  for (let step = 0; step < 6; step += 1) {
    const parent = (spawnSync('ps', ['-o', 'ppid=', '-p', String(pid)], { encoding: 'utf8' })
      .stdout || '').trim();
    if (!parent || parent === '0') break;
    const args = (spawnSync('ps', ['-o', 'args=', '-p', parent], { encoding: 'utf8' })
      .stdout || '').trim();
    if (args.includes('--add-dir')) {
      const words = args.split(' ');
      return words.map((w, i) => (w === '--add-dir' ? words[i + 1] : '')).filter(Boolean);
    }
    pid = Number(parent);
  }
  return [];
}

// Сравнение путей — по нормализованному виду: «..» и хвостовая косая в сыром
// аргументе не дают префиксу совпасть, и директория ВНУТРИ репо сошла бы за
// второй корень.
function absPath(dir) {
  try {
    if (fs.statSync(dir).isDirectory()) return path.resolve(dir);
  } catch { /* каталога нет — сравниваем как дано */ }
  return dir;
}

const projectDir = process.env.CLAUDE_PROJECT_DIR || '';
const isCraftRepo = projectDir && ['craft-inject-router.js', 'craft-inject-router.sh']
  .some((name) => fs.existsSync(path.join(projectDir, '.claude', 'hooks', name)));
if (isCraftRepo) {
  const project = absPath(projectDir);
  const secondRoot = extraDirs()
    .map(absPath)
    .some((d) => d !== project && !d.startsWith(`${project}/`));
  if (!secondRoot) process.exit(0);
}

const codeRulesId = process.env.CRAFT_CODE_RULES_ID || 'd3f184fb-2c70-6058-0797-d9851f4b16a7';
const claudeMd = process.env.CRAFT_USER_CLAUDE_MD || path.join(os.homedir(), '.claude', 'CLAUDE.md');
const snapshot = process.env.CRAFT_CODE_RULES_SNAPSHOT
  || path.join(os.homedir(), '.claude', 'craft-live', 'code-rules.md');
// Инвариант Craft: страница свыше 50 прямых блоков читается с пагинацией, и
// правило из хвоста молча не доезжает. Порог держим тут, счёт — по json-ответу.
const blockLimit = Number(process.env.CRAFT_BLOCK_LIMIT || 50);
// Печать тела остаётся аварийным путём, и её потолок прежний: вывод капится.
const BUDGET = 9500;

let blockWarn = '';

// Канал жив, только пока импорт снимка стоит в личном CLAUDE.md: без него файл
// никто не прочитает, и тело обязано идти печатью.
function channelReady() {
  try {
    return fs.readFileSync(claudeMd, 'utf8').includes(snapshot);
  } catch {
    return false;
  }
}

function fallback(why) {
  process.stdout.write(`⚠️ Ядро «Правил кода» не загружено из Craft (${why}). Перед правками кода прочитай его живьём: Craft MCP blocks get ${codeRulesId} --depth 1 (ядро + диспетчер доменных страниц; страницу своего домена читай целиком). Пока не прочитал, держи минимум: файл в чате и плане адресуется АБСОЛЮТНЫМ путём — относительный резолвится не от того места, где его читают, и не откроется.\n`);
  process.exit(0);
}

// deliver(текст) — доставка тела правил. Канал установлен (импорт снимка стоит в
// личном CLAUDE.md) — тело уходит в файл, в выводе остаётся строка-отчёт; иначе
// печатаем по-старому, с прежним потолком вывода. Перезапись атомарная: снимок
// общий для всех сессий, и соседняя читает либо прежнюю версию, либо новую, но
// не половину и не пустоту. Снимок не сносится ни на одном пути — вчерашний
// текст честнее пустоты, а его возраст виден по метке времени внутри.
function deliver(text, stamp) {
  if (process.env.CODE_RULES_TEST_SNAPSHOT || channelReady()) {
    try {
      fs.mkdirSync(path.dirname(snapshot), { recursive: true });
    } catch { /* каталог уже есть или не создаётся — решит запись ниже */ }
    try {
      fs.writeFileSync(`${snapshot}.tmp`, `${text}\n`);
      fs.renameSync(`${snapshot}.tmp`, snapshot);
      process.stdout.write(`Ядро «Правил кода» обновлено из Craft (${fs.statSync(snapshot).size} байт, ${stamp}) — полный текст в контексте через импорт снимка, обрезки нет.${blockWarn}\n`);
      return;
    } catch {
      try {
        fs.rmSync(`${snapshot}.tmp`, { force: true });
      } catch { /* временного файла и не было */ }
    }
  }
  // Потолок вывода считается в БАЙТАХ, как считал его шелл в этой локали.
  let body = Buffer.from(`${text}\n`, 'utf8');
  if (body.length - 1 > BUDGET) {
    body = Buffer.concat([
      body.subarray(0, BUDGET),
      Buffer.from(`\n…[обрезано бюджетом — канал импорта не установлен, поставь его прогоном install.sh; дочитай ядро живьём: blocks get ${codeRulesId} --depth 1]\n`, 'utf8'),
    ]);
  }
  process.stdout.write(body);
  process.stdout.write(`${blockWarn}\n`);
}

// Тестовый шов стоит ДО загрузки `.env`: прогон кейсов идёт без сети и без живого
// Craft, а проверяемое — само условие «инжектить или молчать» и текст фолбека, не
// тело правил. Задать пустой CRAFT_API_BASE снаружи для этого нельзя: `.env`
// перекрывает переданное окружение.
if (process.env.CODE_RULES_TEST_MD) {
  let md;
  try {
    md = fs.readFileSync(process.env.CODE_RULES_TEST_MD, 'utf8').replace(/\n+$/, '');
  } catch {
    fallback('тестовый шов без источника');
  }
  // Порог инварианта проверяется и в шве: живой счёт идёт по json из сети,
  // которой в прогоне кейсов нет, поэтому число подаётся напрямую. Сам порог и
  // текст сигнала — те же, что на живом пути ниже.
  const probe = process.env.CODE_RULES_TEST_BLOCKS || '';
  if (/^[0-9]+$/.test(probe) && Number(probe) > blockLimit) {
    blockWarn = ` ⚠️ Ядро переросло инвариант: ${probe} прямых блоков при потолке ${blockLimit} — страница читается с пагинацией, и правила из хвоста молча не доезжают. Дробление — по чек-листу гигиены.`;
  }
  deliver(`=== Craft: «⚙️ Правила кода» — ядро, тестовый инжект ===\n${md}`, 'тестовый инжект');
  process.exit(0);
}

loadEnv({ commonDir, ...harnessEnvPaths() });

const base = (process.env.CRAFT_API_BASE || '').replace(/\/$/, '');
if (!base) fallback('CRAFT_API_BASE не задан');

let md = await fetchText(`${base}/blocks?id=${codeRulesId}&maxDepth=1`, { viaExternal });
if (!md) fallback('сеть/API недоступны');
md = md.replace(/\n+$/, '');

// Счёт прямых блоков — по json того же запроса: в markdown границ блока не видно,
// абзац, пункт списка и callout там неразличимы. Отдельного сетевого вызова не
// добавляется — тот же адрес, другой формат ответа; сбой счёта молчит, это
// сигнал гигиены, а не условие доставки.
const json = await fetchText(`${base}/blocks?id=${codeRulesId}&maxDepth=1`, { accept: 'application/json', viaExternal });
if (json) {
  try {
    const parsed = JSON.parse(json);
    const direct = Array.isArray(parsed.content) ? parsed.content.length : null;
    if (direct !== null && direct > blockLimit) {
      blockWarn = ` ⚠️ Ядро переросло инвариант: ${direct} прямых блоков при потолке ${blockLimit} — страница читается с пагинацией, и правила из хвоста молча не доезжают. Дробление — по чек-листу гигиены.`;
    }
  } catch { /* счёт не разобрался — это сигнал гигиены, не условие доставки */ }
}

deliver(`=== Craft: «⚙️ Правила кода» — ядро, живой инжект (${utcStamp()}) ===\n${md}\n=== конец ядра. Работаешь с доменом — прочитай его страницу ЦЕЛИКОМ (Craft MCP, blocks get по ссылке из диспетчера, --depth -1) до первых правок кода ===`, utcStamp());
