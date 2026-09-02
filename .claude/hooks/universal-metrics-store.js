#!/usr/bin/env node
// Stop: сводка сессии (её пишет universal-metrics в `<журнал>.summary.json`)
// уезжает в ветку `metrics` репозитория системы — того чекаута, где лежит сам
// файл хука, тем же origin, что использует синк системы.
//
// Сеть живёт в фоновом работнике: ход уже закончен, ждать push некому. Сводка
// сперва ложится в локальную очередь и оттуда уезжает; нет сети — доедет на
// следующем Stop. Стоит в ALWAYS: блокировка конца хода сводку не отменяет.
//
// Выключатель METRICS_STORE=off; в кейсах раннера он выставлен всегда — иначе
// прогон тестов пушил бы в настоящую ветку. METRICS_STORE_INLINE=1 — работа в
// том же процессе (git-тест на временных репозиториях).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { defaultQueue, enqueue, flushQueue } from './lib/metrics-store.js';

if (process.env.METRICS_STORE === 'off') process.exit(0);

const selfPath = fileURLToPath(import.meta.url);
let dir = path.dirname(selfPath);
try {
  dir = path.dirname(fs.realpathSync(selfPath));
} catch { /* нечего резолвить — берём каталог как есть */ }
const TARGET = process.env.METRICS_STORE_TARGET || path.resolve(dir, '..', '..');

function store(summaryFile) {
  let summary;
  try {
    summary = JSON.parse(fs.readFileSync(summaryFile, 'utf8'));
  } catch {
    return { status: 'no-summary', delivered: 0 };
  }
  if (!summary || typeof summary !== 'object' || !summary.sid) return { status: 'no-summary', delivered: 0 };
  const queue = process.env.METRICS_STORE_QUEUE || defaultQueue(TARGET);
  enqueue(queue, summary);
  return flushQueue({ target: TARGET, queueFile: queue });
}

// Фоновый работник: без события, сводка — из окружения.
if (process.env.METRICS_STORE_WORKER) {
  store(process.env.METRICS_STORE_SUMMARY || '');
  process.exit(0);
}

const { readEvent } = await import('./lib/event.js');
const { hookOnce } = await import('./lib/once.js');
const { metricsLog, sessionId } = await import('./lib/paths.js');

const { raw, event } = readEvent();
if ((event.hook_event_name || '') !== 'Stop') process.exit(0);
const sid = (typeof event.session_id === 'string' && event.session_id) || sessionId();
if (!sid && !process.env.CRAFT_METRICS_LOG) process.exit(0);
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const summaryFile = `${metricsLog(sid)}.summary.json`;
if (!fs.existsSync(summaryFile)) process.exit(0);

if (process.env.METRICS_STORE_INLINE) {
  const res = store(summaryFile);
  process.stderr.write(`[metrics-store] ${res.status}${res.delivered ? ` ×${res.delivered}` : ''}\n`);
  process.exit(0);
}

const worker = spawn(process.execPath, [selfPath], {
  detached: true,
  stdio: 'ignore',
  env: {
    ...process.env, METRICS_STORE_WORKER: '1', METRICS_STORE_SUMMARY: summaryFile, HOOK_ONCE: 'off',
  },
});
worker.unref();
