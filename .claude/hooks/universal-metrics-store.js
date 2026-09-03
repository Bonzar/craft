#!/usr/bin/env node
// Stop: сводка сессии (её пишет universal-metrics в `<журнал>.summary.json`)
// уезжает в ветку `metrics` репозитория системы — того чекаута, где лежит сам
// файл хука, тем же origin, что использует синк системы.
//
// Сеть живёт в фоновом работнике: ход уже закончен, ждать push некому. Сводка
// сперва ложится в локальную очередь и оттуда уезжает; нет сети — доедет на
// следующем Stop. Стоит в ALWAYS: блокировка конца хода сводку не отменяет.
//
// В очередь сводка идёт на КАЖДОМ Stop, а выгрузка (сеть, коммит, push) — не
// чаще, чем раз в METRICS_STORE_INTERVAL секунд: иначе ветка получала бы сотню
// коммитов за сессию. Отложенная выгрузка ничего не теряет — очередь durable и
// уезжает пачкой.
//
// Пишет тот же контур, что и метрики: при проектной регистрации в чекауте
// сессии пользовательский молчит. Без этого правила пользовательский процесс
// успевал забрать сводку раньше, чем проектный её перезапишет, и на ветку
// уезжала сводка ПРОШЛОГО хода, а последняя не уезжала никогда.
//
// Выключатель METRICS_STORE=off; в кейсах раннера он выставлен всегда — иначе
// прогон тестов пушил бы в настоящую ветку. METRICS_STORE_INLINE=1 — работа в
// том же процессе (git-тест на временных репозиториях).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { defaultQueue, enqueue, flushQueue, dueForFlush } from './lib/metrics-store.js';

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
  const interval = process.env.METRICS_STORE_INTERVAL ?? '600';
  if (!dueForFlush(queue, interval)) return { status: 'queued', delivered: 0 };
  return flushQueue({ target: TARGET, queueFile: queue });
}

// Фоновый работник: без события, сводка — из окружения.
if (process.env.METRICS_STORE_WORKER) {
  store(process.env.METRICS_STORE_SUMMARY || '');
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
