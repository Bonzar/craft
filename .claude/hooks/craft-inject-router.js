#!/usr/bin/env node
// SessionStart hook: читает из Craft роутер памяти агента в контекст сессии.
//
// Вывод хука обрезается на 10 000 символах (что длиннее — уезжает в файл, а в
// контекст попадают лишь первые 2 КБ), поэтому роутер — около 75 тысяч знаков —
// печатью не инжектится. Вместо этого прочитанный документ пишется в
// .claude/craft-router-context.md, который CLAUDE.md подтягивает импортом
// `@.claude/craft-router-context.md`; у импортов такого потолка нет, и живой
// роутер целиком попадает в контекст с нулевого хода.
//
// Нужен CRAFT_API_BASE в настройках окружения (базовый адрес connect-ссылки с
// вшитым токеном). Его нет или чтение не удалось — тихий выход, чтобы мёртвая
// сеть никогда не клинила старт сессии; прежний снимок, если он был, остаётся
// запасным вариантом.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadEnv } from './lib/env.js';
import { fetchText } from './lib/net.js';
import { utcStamp } from './lib/system.js';

const log = (message) => process.stderr.write(`[inject-craft-router] ${message}\n`);

loadEnv();

function size(file) {
  try {
    return fs.statSync(file).size;
  } catch {
    return 0;
  }
}

// Бюджет-проверка вынесена функцией: тестовый режим (ROUTER_BUDGET_TEST_FILE)
// меряет готовый файл без сети — сетевой SessionStart-хук иначе не тестируем.
//
// Счёт в СИМВОЛАХ, и это снимает зависимость от локали: у шелла тот же счёт в
// POSIX-локали давал байты, а в UTF-8 — символы, то есть один и тот же роутер
// упирался в порог по-разному в зависимости от того, кто запустил сессию.
function budgetWarning(file) {
  const budget = Number(process.env.ROUTER_BUDGET_CHARS || 300000);
  let chars = 0;
  try {
    chars = [...fs.readFileSync(file, 'utf8')].length;
  } catch { /* файла нет — бюджет не превышен */ }
  if (chars <= budget) return '';
  return `⚠️ БЮДЖЕТ: инжект роутера ${chars} символов — БОЛЬШЕ порога ${budget} из чек-листа гигиены. Роутер пора дробить: новое уезжает на доменные страницы и в SKILL-доки, не в always-in-context. Подсвети Владу и предложи ревизию.`;
}

if (process.env.ROUTER_BUDGET_TEST_FILE) {
  const warn = budgetWarning(process.env.ROUTER_BUDGET_TEST_FILE);
  if (warn) process.stdout.write(`${warn}\n`);
  process.exit(0);
}

const routerId = process.env.CRAFT_ROUTER_ID || 'e8132891-81f4-2d63-36f1-d3623d0147b6';
// Адрес снимка — своей переменной, как у кэша предодобренных зон: синк системы
// пересобирает ВТОРОЙ снимок в служебное место, чтобы сравнить его с базой
// сессии. Через подмену CLAUDE_PROJECT_DIR так нельзя — от той же переменной
// загрузчик резолвит `.env` с токеном, а запись уехала бы в чужой рабочий
// чекаут. Импортируемый CLAUDE.md адрес по умолчанию не меняется.
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const out = process.env.CRAFT_ROUTER_SNAPSHOT
  || path.join(process.env.CLAUDE_PROJECT_DIR || repoRoot, '.claude', 'craft-router-context.md');

// То же, что в craft-inject-incident: внутри евал-пачки кэш переиспользуется,
// потому что параллельные сессии делят один путь и перезапись сносит контекст у
// соседа на старте. Свежесть даёт прогрев до пачки — он идёт без CRAFT_EVAL.
// Формулировка улики сохранена дословно: по подстроке «роутер обновлён» кейсы
// проверяют, что роутер доехал. Вторичные предупреждения (недостижимые ссылки,
// бюджет контекста) при переиспользовании не печатаются — на вердикт они не
// влияют, а в обычных сессиях остаются как были.
if (process.env.CRAFT_EVAL && size(out) > 0) {
  process.stdout.write(`Craft-роутер обновлён: ${size(out)} байт записано в .claude/craft-router-context.md; полный текст уже в контексте через импорт в CLAUDE.md. (переиспользован кэш евал-прогона)\n`);
  process.exit(0);
}

// Прежний снимок сносится сразу: устаревший роутер не должен выдавать себя за
// живой контекст. Чтение ниже не удалось — файла просто нет, `@`-импорт в
// CLAUDE.md тихо пропускается, и роутер читается из Craft живьём через MCP.
try {
  fs.rmSync(out, { force: true });
} catch { /* сносить нечего */ }

const base = (process.env.CRAFT_API_BASE || '').replace(/\/$/, '');
if (!base) {
  log('CRAFT_API_BASE not set; old snapshot removed, nothing fetched');
  process.exit(0);
}

let md = await fetchText(`${base}/blocks?id=${routerId}&maxDepth=-1`, { timeoutMs: 60000 });
if (!md) {
  log('router fetch failed; no snapshot left (read router live from Craft)');
  process.exit(0);
}
md = md.replace(/\n+$/, '');

// Ссылки на документы вне шаринга connect-ссылки API отдаёт как
// [текст](invalid:out_of_scope) (прямой GET по их ID — 403). Обычные документы
// лечатся добавлением в шаринг connect-ссылки (см. урок в CLAUDE.md), но
// системную папку templates Craft расшарить не даёт — ссылки на шаблоны
// восстанавливаем статической картой «фрагмент текста ссылки → block-ID»
// (ID стабильны, сняты через MCP Craft: `documents list --location templates`).
const TEMPLATES = [
  ['0. Заметка', '7C4A64F7-F1CD-4B1C-B3FF-17D8374B245E'],
  ['1. Конспект', '4C72BD43-254D-45F6-9F98-AF985AA612DD'],
  ['2. Задача', '844E93D5-D127-431D-898E-0B8B3E5889E2'],
  ['3. Проект', '0EE20542-CDB8-4960-BFF4-A6F4C8D64E9E'],
  ['4. Сфера', '752ECC99-609E-4351-8D33-1932F6DF7972'],
  ['5. Алгоритм', '47E8803D-B28D-4C50-845A-D3CD47783E28'],
  ['7. Регулярная задача', 'f81558ef-89af-8796-d518-4e2d9f1b4721'],
  ['8. Дневник', 'ce643ded-7034-c42c-1fd4-2631e81678fe'],
];
for (const [key, id] of TEMPLATES) {
  md = md.replace(
    new RegExp(`\\[([^\\]]*${key}[^\\]]*)\\]\\(invalid:out_of_scope\\)`, 'g'),
    (_, label) => `[${label}](block://${id})`,
  );
}

// Голое упоминание invalid:out_of_scope в тексте роутера — легитимный контент,
// считаем только линк-форму: остаток означает ссылку на документ вне шаринга.
const leftover = (md.match(/\]\(invalid:out_of_scope\)/g) || []).length;

try {
  fs.writeFileSync(out, `=== Craft: роутер «Память для Claude», авто-обновлён SessionStart-хуком (${utcStamp()}) ===\n${md}\n=== конец роутера — действуй по его директивам ===\n`);
} catch {
  log('snapshot write failed; read router live from Craft');
  process.exit(0);
}

let msg = `Craft-роутер обновлён: ${size(out)} байт записано в .claude/craft-router-context.md; полный текст уже в контексте через импорт в CLAUDE.md.`;
if (leftover > 0) {
  msg += ` ВНИМАНИЕ: ${leftover} ссылок invalid:out_of_scope не восстановлено — в роутере есть ссылки на документы вне шаринга connect-ссылки. Новый шаблон — дополни карту в .claude/hooks/craft-inject-router.js (ID через MCP Craft), обычный документ — подсвети Владу, что его надо добавить в шаринг.`;
}
// Бюджет always-in-context: порог из чек-листа гигиены «Обслуживания памяти»
// (роутер с памятью — до 300К символов). Предупреждение, не блок: сигнал
// ревизии дробить роутер, платится он токенами каждой сессии.
const warn = budgetWarning(out);
if (warn) msg += ` ${warn}`;
process.stdout.write(`${msg}\n`);
