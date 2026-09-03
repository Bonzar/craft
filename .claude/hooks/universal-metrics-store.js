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
// том же процессе (тест хранения на временных репозиториях); лок очереди она
// ждёт коротким сроком хода, а не сроком работника.
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import {
  defaultQueue, enqueue, flushQueue, storeTarget, WORKER_WAIT_MS, QUEUE_WAIT_MS,
} from './lib/metrics-store.js';
// Адаптер выбирает КРАЙ: общая часть хранения инструмента не знает и не ищет.
import * as ADAPTER from './lib/metrics-store-git.js';

if (process.env.METRICS_STORE === 'off') process.exit(0);

const selfPath = fileURLToPath(import.meta.url);
// Цель считает одна функция на весь слой (lib/metrics-store.js): своя формула
// здесь уже расходилась с той, и переопределение окружения игнорировалось.
const TARGET = storeTarget();

// waitMs — сколько ждать лок очереди. Своё число ПРИХОДИТ СНАРУЖИ: пять минут
// имеет право ждать только отсоединённый работник, а инлайн-режим идёт в
// процессе хука, и зашитый большой срок оказался бы ожиданием в цепочке хода.
function store(summaryFile, { waitMs }) {
  let summary;
  try {
    summary = JSON.parse(fs.readFileSync(summaryFile, 'utf8'));
  } catch {
    return { status: 'no-summary', delivered: 0 };
  }
  if (!summary || typeof summary !== 'object' || !summary.sid) return { status: 'no-summary', delivered: 0 };
  const queue = process.env.METRICS_STORE_QUEUE || defaultQueue(TARGET, ADAPTER);
  if (!queue) return { status: 'unsupported', capability: 'metrics-store', delivered: 0 };
  // Работник ОТСОЕДИНЁН, его никто не ждёт — лок он ждёт долго. Короткий срок
  // у него означал бы потерянную сводку: предыдущая выгрузка держит лок всё
  // время сети, а следующего Stop у сессии может не быть.
  let queued = enqueue(queue, summary, { waitMs });
  const res = flushQueue({
    target: TARGET, queueFile: queue, adapter: ADAPTER, waitMs,
  });
  // Не встали в очередь до выгрузки — пробуем ещё раз: лок теперь свободен.
  if (!queued) queued = enqueue(queue, summary, { waitMs });
  return queued ? res : { ...res, queued: false };
}

// Исход доставки — строкой в журнал той сессии, чью сводку везли. Журнал
// выводится из пути к её сводке: у работника нет ни события, ни переменной
// сессии — та, что была у хука, ему не передаётся.
function logOf(summaryFile) {
  return summaryFile.endsWith('.summary.json') ? summaryFile.slice(0, -'.summary.json'.length) : '';
}

function noteOutcome(res, summaryFile) {
  const log = logOf(summaryFile);
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
  const file = process.env.METRICS_STORE_SUMMARY || '';
  noteOutcome(store(file, { waitMs: WORKER_WAIT_MS }), file);
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
  const res = store(summaryFile, { waitMs: QUEUE_WAIT_MS });
  noteOutcome(res, summaryFile);
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
