#!/usr/bin/env node
// SessionStart hook: собирает предодобренную ЗОНУ план-гейта — все block-ID,
// живущие внутри страниц прямого редактирования из gate-exempt-pages.txt.
// План-гейт (universal-guard-plan-gate) пропускает craft_write без одобренного
// плана, когда ВСЕ block-ID команды лежат в этом наборе.
//
// Почему ключ — цели записи, а не формулировка: у этого исключения ОБРАТНАЯ
// асимметрия по сравнению с детектором инцидентов — ложно открыть гейт настоящей
// записи в проект или сферу ДОРОГО, а лишний план на операции со списком ДЁШЕВО.
// Фразы («купил X») промахиваются — Влад покупает кольца и билеты, не только
// продукты, — так что безопасный ключ только один: сама цель записи.
//
// Механика чтения повторяет инжектор роутера (connect-API, доступ из окружения
// или `.env`). Тихий отказ в ДЕШЁВУЮ сторону: нет доступа или конфига, чтение не
// удалось → файла зоны нет → гейт просто продолжает требовать планы. Блок,
// созданный посреди сессии, в снимок не попадает — редкий и дешёвый промах.
//
// Место кэша одно и каноническое: чекаут, в котором лежит РЕАЛЬНЫЙ файл хука.
// Гейт читает кэш по той же формуле, поэтому все сессии — облачная, локальный
// воркри, arc-маунт, задача по расписанию — сходятся на одном месте.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadEnv } from './lib/env.js';
import { fetchText } from './lib/net.js';
import { exemptScopeFile } from './lib/paths.js';

const log = (message) => process.stderr.write(`[universal-cache-gate-exempt-scope] ${message}\n`);

const selfPath = fileURLToPath(import.meta.url);
let dir = path.dirname(selfPath);
try {
  dir = path.dirname(fs.realpathSync(selfPath));
} catch { /* нечего резолвить — берём каталог как есть */ }

loadEnv();

const config = process.env.CRAFT_GATE_EXEMPT_PAGES || path.join(dir, 'gate-exempt-pages.txt');
const out = exemptScopeFile();

// Прежний снимок сносится первым: устаревшая зона не должна выдавать себя за
// свежую. Сборка ниже не удалась — файла нет, и гейт гейтит всё.
try {
  fs.rmSync(out, { force: true });
} catch { /* сносить нечего */ }

let lines;
try {
  lines = fs.readFileSync(config, 'utf8').split('\n');
} catch {
  log(`config ${config} missing; scope not built (gate applies as usual)`);
  process.exit(0);
}

const base = (process.env.CRAFT_API_BASE || '').replace(/\/$/, '');
if (!base) {
  log('CRAFT_API_BASE not set; scope not built (gate applies as usual)');
  process.exit(0);
}

const UUID = /[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}/g;
const ids = [];
let pages = 0;
let fetched = 0;
for (const line of lines) {
  // Комментарий отрезается по решётке, пробелы внутри снимаются целиком.
  const page = line.split('#')[0].replace(/\s/g, '');
  if (!page) continue;
  pages += 1;
  // Формат ответа не задаётся намеренно: набор block-ID полон только в
  // машинном представлении, а Accept: markdown отдал бы текст без адресов.
  const body = await fetchText(`${base}/blocks?id=${page}&maxDepth=-1`, {
    accept: '*/*', timeoutMs: 60000,
  });
  if (!body) {
    log(`fetch failed for ${page}; skipped`);
    continue;
  }
  fetched += 1;
  ids.push(...(body.match(UUID) || []));
}

if (ids.length === 0) {
  log(`no block IDs collected from ${pages} page(s); scope not built (gate applies as usual)`);
  process.exit(0);
}

// Верхний регистр и без повторов — гейт поднимает регистр ID команды перед сверкой.
const unique = [...new Set(ids.map((id) => id.replace(/[a-f]/g, (c) => c.toUpperCase())))].sort();
try {
  fs.writeFileSync(out, `${unique.join('\n')}\n`);
} catch {
  log('scope write failed; gate applies as usual');
  process.exit(0);
}

process.stdout.write(`Предодобренный scope план-гейта: ${unique.length} block-ID из ${fetched}/${pages} страниц (.craft/gate-exempt-scope.txt) — запись целиком внутри них идёт без плана.\n`);
