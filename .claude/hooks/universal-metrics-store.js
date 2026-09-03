#!/usr/bin/env node
// Stop: сводка сессии (её пишет universal-metrics в `<журнал>.summary.json`)
// уезжает в ветку `metrics` репозитория системы — того чекаута, где лежит сам
// файл хука, тем же origin, что использует синк системы.
//
// Сеть живёт в фоновом работнике: ход уже закончен, ждать доставку некому.
// Сводка сперва ложится в локальную очередь и оттуда уезжает; не уехала —
// доедет следующей выгрузкой. Стоит в ALWAYS: блокировка конца хода сводку не
// отменяет.
//
// Выгрузка идёт на КАЖДОМ Stop: хук её не ждёт, а отложенная сводка — это
// сводка, которой может не стать вовсе, если чекаут одноразовый.
//
// Исход выгрузки виден в журнале метрик строкой kind: 'store': работник
// отсоединён, его stdout и stderr никто не читает, и без этой строки провал
// доставки в бою неотличим от того, что доставки не было.
//
// Пишет тот же контур, что и метрики: при проектной регистрации в чекауте
// сессии пользовательский молчит. Без этого правила пользовательский процесс
// успевал забрать сводку раньше, чем проектный её перезапишет, и на ветку
// уезжала сводка ПРОШЛОГО хода, а последняя не уезжала никогда.
//
// Выключатель METRICS_STORE=off; в кейсах раннера он выставлен всегда — иначе
// прогон тестов пушил бы в настоящую ветку. METRICS_STORE_INLINE=1 — работа в
// том же процессе (тест хранения на временных репозиториях).
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
  const queued = enqueue(queue, summary);
  const res = flushQueue({ target: TARGET, queueFile: queue });
  return queued ? res : { ...res, queued: false };
}

// Исход доставки — строкой в журнал той сессии, чью сводку везли. Журнал берётся
// из окружения: у работника события нет.
function noteOutcome(res) {
  const log = process.env.CRAFT_METRICS_LOG || '';
  if (!log) return;
  const line = {
    kind: 'store', ts: new Date().toISOString(), status: res.status, delivered: res.delivered || 0,
  };
  if (res.queued === false) line.queued = false;
  if (res.capability) line.capability = res.capability;
  try {
    fs.appendFileSync(log, `${JSON.stringify(line)}\n`);
  } catch { /* журнала нет — исход виден только в stderr инлайн-режима */ }
}

// Фоновый работник: без события, сводка — из окружения.
if (process.env.METRICS_STORE_WORKER) {
  noteOutcome(store(process.env.METRICS_STORE_SUMMARY || ''));
  process.exit(0);
}

const { readEvent } = await import('./lib/event.js');
const { currentMetricsLog, projectDispatcherAt } = await import('./lib/metrics.js');

const { event, cwd } = readEvent();
if ((event.hook_event_name || '') !== 'Stop') process.exit(0);
const log = currentMetricsLog();
if (!log) process.exit(0);

// Тем же правилом контуров, что и метрики: пишет тот, кто вёл полную цепочку.
// Уступки по hookOnce здесь нет намеренно — её ключ для Stop это хеш события со
// сроком в секунды, и второй одинаковый Stop (заблокированный конец хода)
// терял бы сводку.
if ((globalThis.hookScope || 'project') === 'universal' && projectDispatcherAt(cwd)) process.exit(0);

const summaryFile = `${log}.summary.json`;
if (!fs.existsSync(summaryFile)) process.exit(0);

if (process.env.METRICS_STORE_INLINE) {
  const res = store(summaryFile);
  noteOutcome(res);
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
