#!/usr/bin/env node
// Stop и SessionEnd: сводка сессии (её пишет universal-metrics в
// `<журнал>.summary.json`)
// уезжает в ветку `metrics` репозитория системы — того чекаута, где лежит сам
// файл хука, тем же origin, что использует синк системы.
//
// Сеть живёт в фоновом работнике: ход уже закончен, ждать доставку некому.
// Сводка сперва ложится в локальную очередь и оттуда уезжает; не уехала —
// доедет следующей выгрузкой. Стоит СРАЗУ ЗА наблюдателем, в начале цепочки:
// блокировка конца хода приходит позже и сводку не отменяет, а забрать её надо до
// того, как решение оборвёт цепочку.
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
// В очередь этот хук ставит НЕ всякую найденную сводку, а только сводку
// настоящей сессии — признак её в поле `harness`, см. предикат ниже. Выгрузка
// же везёт очередь КАК ЕСТЬ: строку, которую положили туда другим путём
// (tools/registry-ingest.mjs), предикат не стережёт.
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
} from './lib/summary-store.js';
// Адаптер выбирает КРАЙ: общая часть хранения инструмента не знает и не ищет.
import * as ADAPTER from './lib/summary-store-git.js';

if (process.env.METRICS_STORE === 'off') process.exit(0);

const selfPath = fileURLToPath(import.meta.url);
// Цель считает одна функция на весь слой (lib/summary-store.js): своя формула
// здесь уже расходилась с той, и переопределение окружения игнорировалось.
// Само переопределение читает край — здесь.
const TARGET = storeTarget(process.env.METRICS_STORE_TARGET);

// waitMs — сколько ждать лок очереди. Своё число ПРИХОДИТ СНАРУЖИ: пять минут
// имеет право ждать только отсоединённый работник, а инлайн-режим идёт в
// процессе хука, и зашитый большой срок оказался бы ожиданием в цепочке хода.
function store(summaryFile, { waitMs, queue }) {
  let summary;
  try {
    summary = JSON.parse(fs.readFileSync(summaryFile, 'utf8'));
  } catch {
    return { status: 'no-summary', delivered: 0 };
  }
  if (!summary || typeof summary !== 'object' || !summary.sid) return { status: 'no-summary', delivered: 0 };
  if (!queue) return { status: 'unsupported', capability: 'summary-store', delivered: 0 };
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

// Харнес сводки. Возвращает null, когда сводка НЕ ПРОЧИТАЛАСЬ: это не «харнеса
// нет», и решать по такому чтению нельзя — нечитаемую сводку называет своим
// исходом `no-summary` уже store(), и перехват здесь отнял бы у неё эту строку.
function summaryHarness(file) {
  let summary;
  try {
    summary = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
  if (!summary || typeof summary !== 'object') return null;
  return String(summary.harness || '');
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
  // Файл очереди работнику передаёт тот, кто его запустил: события у работника
  // нет, а каталог состояния — поле события. Своя формула остаётся запасной для
  // прямого запуска работника руками.
  const queue = process.env.METRICS_STORE_QUEUE || defaultQueue(ADAPTER, TARGET);
  noteOutcome(store(file, { waitMs: WORKER_WAIT_MS, queue }), file);
  process.exit(0);
}

const { readEvent } = await import('./lib/event-claude.js');
const { currentMetricsLog, append } = await import('./lib/metrics.js');
const { projectDispatcherAt } = await import('./lib/registration-claude.js');

const { EVENTS } = await import('./lib/event.js');
const { cwd, event: name, state_dir: stateDirOfEvent } = readEvent();
// Конец хода и конец СЕССИИ: на втором уезжает сводка, досчитанная по строкам
// решений последнего хода, — их наблюдатель переносит как раз на нём.
if (name !== EVENTS.STOP && name !== EVENTS.SESSION_END) process.exit(0);
const log = currentMetricsLog();
if (!log) process.exit(0);

// Тем же правилом контуров, что и метрики: пишет тот, кто вёл полную цепочку.
// Уступки по hookOnce здесь нет намеренно — её ключ для Stop это хеш события со
// сроком в секунды, и второй одинаковый Stop (заблокированный конец хода)
// терял бы сводку.
if ((process.env.CRAFT_HOOK_SCOPE || 'project') === 'universal' && projectDispatcherAt(cwd)) process.exit(0);

const summaryFile = `${log}.summary.json`;
if (!fs.existsSync(summaryFile)) process.exit(0);

// Публикуется только сводка НАСТОЯЩЕЙ сессии, и признак её — имя харнеса.
// У живой сессии оно непусто всегда: край берёт его как `CRAFT_HARNESS ||
// 'claude'` (lib/event-claude.js), то есть пустым не бывает, а в сводку его
// переносит запись `session` (lib/metrics-summary.js). Пусто оно ровно у той
// сводки, которую собрал не харнес, — у ручного прогона и у замера субагента:
// они выключатель не выставляют, и 4 сентября две такие уехали в боевую ветку.
//
// Признак выбран ПО ДАННЫМ ветки `metrics`, а не на вкус. Считано по `sid` —
// это и есть единица публикации, строка на сессию: за всю историю ветки в ней
// тринадцать сессий, у одиннадцати настоящих `harness` — «claude», и пуст он
// ровно у двух синтетических. Сессии, у которой он и пуст, и непуст, нет ни
// одной, то есть признак разводит их без ложных срабатываний.
//
// Соседние признаки не годятся: по `repo` фильтровать нельзя — у настоящей
// сессии вне репозитория или без remote он пуст по устройству
// (lib/repo-git.js), а по числу ходов нельзя — у синтетической строки было
// turns: 1, как у настоящей короткой сессии.
//
// Стоит ДО очереди и работника: поднимать доставку ради сводки, которую всё
// равно не публикуем, незачем. Стережёт он ровно ПОСТАНОВКУ этой сводки:
// очередь выгружается как есть, и что в неё попало помимо этого хука — вопрос
// не сюда.
//
// Пропуск НАЗЫВАЕТСЯ строкой журнала: тихий выход здесь читался бы как
// уехавшая сводка, а это ровно тот дефект, который чинит сам пункт.
const harness = summaryHarness(summaryFile);
if (harness === '') {
  append(log, {
    kind: 'skip', ts: new Date().toISOString(), what: 'summary', reason: 'no-harness',
  });
  process.exit(0);
}

// Файл очереди считает КРАЙ, у которого есть событие: каталог состояния — поле
// ядра, и очередь резолвится по нему, а не по своей копии формулы. Работнику он
// уходит готовым значением — событием тот не располагает.
const QUEUE = process.env.METRICS_STORE_QUEUE || defaultQueue(ADAPTER, TARGET, stateDirOfEvent);

if (process.env.METRICS_STORE_INLINE) {
  const res = store(summaryFile, { waitMs: QUEUE_WAIT_MS, queue: QUEUE });
  noteOutcome(res, summaryFile);
  process.stderr.write(`[summary-store] ${res.status}${res.delivered ? ` ×${res.delivered}` : ''}\n`);
  process.exit(0);
}

const worker = spawn(process.execPath, [selfPath], {
  detached: true,
  stdio: 'ignore',
  env: {
    ...process.env,
    METRICS_STORE_WORKER: '1',
    METRICS_STORE_SUMMARY: summaryFile,
    METRICS_STORE_QUEUE: QUEUE,
    HOOK_ONCE: 'off',
  },
});
worker.unref();
