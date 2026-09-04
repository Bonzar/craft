// Хранение сводок сессий: очередь и раскладка по дням. Про инструмент, которым
// сводки уезжают, этот файл не знает ничего и сам его не выбирает: АДАПТЕР
// приходит параметром от края (available, fetchBase, readDay, publish), а сюда —
// только данные. Адаптера нет — исход `unsupported`, а не
// молчание и не ошибка.
//
// Файл на стороне хранилища — `summaries/<дата UTC>.jsonl`, по строке на
// сессию; день берётся по НАЧАЛУ сессии, чтобы сессия, перешагнувшая полночь,
// не оставила две строки в двух файлах и не посчиталась дважды.
//
// Очередь лежит в КАТАЛОГЕ СОСТОЯНИЯ слоя, а не в каталоге инструмента доставки:
// сводки сессии — состояние слоя, и в чужой рабочей копии им делать нечего.
// Сводка сначала ложится в локальный файл очереди и уезжает оттуда.
// Не уехала — очередь цела и доедет следующей выгрузкой. Очередь держит по
// ОДНОЙ строке на сессию (сводка каждого Stop заменяет прежнюю) и обрезана
// сверху: иначе на репозитории, куда push запрещён навсегда, она росла бы
// строкой на каждый ход до конца жизни чекаута.
//
// И постановка в очередь, и выгрузка идут под ОДНИМ локом (lib/lock.js): пока
// выгрузка держит лок, дописать в очередь некому, поэтому после удачной
// доставки очередь снимается целиком, без сверки со снимком. Лок не достался —
// об этом говорится исходом, а не тишиной.
//
// Постановка в очередь: enqueueSummary(...) → {ok, reason}, enqueue(...) → true/false.
//
// Исходы flushQueue: stored — доставлено, очередь снята; nothing — очередь
// пуста; offline — хранилище недоступно, очередь цела; push-failed — отказ на
// записи; error — сборка не удалась; unsupported — адаптера для этой цели нет;
// locked — лок занят дольше срока, выгрузка не начиналась.
import fs from 'node:fs';
import path from 'node:path';
import { withLock, atomicWrite } from './lock.js';
import { eachJsonl } from './jsonl.js';
import { repoRootOf, stateDir } from './paths.js';
import { append } from './metrics.js';

// Потолок очереди в строках. Строка на сессию, так что потолок — про число
// сессий, накопившихся, пока доставка не проходит.
export const QUEUE_CAP = 500;

// Сколько ждать лок очереди. Умолчание КОРОТКОЕ: постановку зовут и с края,
// который идёт следом за ходом, а ход ждать нельзя. Работник хранения —
// отсоединённый, его никто не ждёт, и он передаёт свой большой срок сам.
export const QUEUE_WAIT_MS = 300;
export const WORKER_WAIT_MS = 5 * 60 * 1000;

// Цель хранения — ОДНА формула на всех: переопределение, иначе корень того
// чекаута, где лежит сам слой. Две формулы уже разъезжались, и переопределение
// игнорировалось одной из них. Само переопределение читает КРАЙ и передаёт
// сюда готовым значением: окружения общая часть не знает.
export function storeTarget(override = '') {
  return override || repoRootOf(import.meta.url);
}

// Файл очереди лежит в КАТАЛОГЕ СОСТОЯНИЯ, а не в каталоге инструмента доставки:
// сводки сессии — состояние слоя, а не содержимое чекаута, и в чужой рабочей копии
// им делать нечего. Плата за это названа в шапке модуля: каталог состояния
// переживает сессию, но не перезагрузку машины.
//
// Адаптера доставки нет — очереди тоже НЕ ЗАВОДИТСЯ: копить сводки, которые никто
// никогда не увезёт, значит тихо копить мусор вместо явного `unsupported`
// (решение 8). Поэтому адаптер здесь всё ещё спрашивается — но только про то,
// есть ли доставка, а не про то, где лежать очереди.
export function defaultQueue(adapter, target) {
  if (!adapter || typeof adapter.available !== 'function') return '';
  // Адаптер СПРАШИВАЕТСЯ, а не проверяется на существование: доставка бывает
  // невозможна и при живом адаптере (цель — не рабочая копия его инструмента), и
  // тогда очередь копила бы то, что никто никогда не увезёт.
  return adapter.available(target) ? path.join(stateDir(), 'metrics-queue.jsonl') : '';
}

function readQueueText(queueFile) {
  try {
    return fs.readFileSync(queueFile, 'utf8');
  } catch {
    return '';
  }
}

export function parseQueue(text) {
  const out = [];
  eachJsonl(text, (rec) => {
    if (rec.sid) out.push(rec);
  });
  return out;
}

function writeQueue(queueFile, rows) {
  const body = rows.map((r) => JSON.stringify(r)).join('\n');
  return atomicWrite(queueFile, body ? `${body}\n` : '');
}

// Поставить сводку в очередь. Сводка ЗАМЕНЯЕТ прежнюю сводку той же сессии:
// каждый Stop пишет её заново, и хранить все промежуточные незачем.
//
// Причина отказа возвращается ОТДЕЛЬНО от самого отказа: «лок занят» и «запись
// не удалась» — разные беды, и в журнале они прежде выглядели одинаково.
// enqueueSummary(...) → {ok, reason}: reason — 'locked' либо 'write-failed'.
export function enqueueSummary(queueFile, summary, { waitMs = QUEUE_WAIT_MS } = {}) {
  if (!queueFile || !summary || !summary.sid) return { ok: false, reason: 'no-summary' };
  const { locked, value } = withLock(queueFile, () => {
    const bySid = new Map(parseQueue(readQueueText(queueFile)).map((r) => [r.sid, r]));
    bySid.delete(summary.sid);
    bySid.set(summary.sid, summary);
    const rows = [...bySid.values()].slice(-QUEUE_CAP);
    return writeQueue(queueFile, rows);
  }, { waitMs });
  if (!locked) return { ok: false, reason: 'locked' };
  return value ? { ok: true } : { ok: false, reason: 'write-failed' };
}

// Тот же вызов исходом «получилось или нет» — для края, которому причина не
// нужна.
export function enqueue(queueFile, summary, opts = {}) {
  return enqueueSummary(queueFile, summary, opts).ok;
}

// День сводки — по НАЧАЛУ сессии и по UTC: один календарь у всех машин, и одна
// строка на сессию даже когда сессия перешагнула полночь.
export function dayOf(summary) {
  const t = Date.parse(summary.started_at || summary.ended_at || summary.ts || '');
  const d = Number.isFinite(t) ? new Date(t) : new Date();
  return d.toISOString().slice(0, 10);
}

// Строки дня со вставленными сводками: своя строка на сессию, порядок прежних
// строк сохраняется, чужие строки не трогаются.
function upsertLines(text, summaries) {
  const lines = text.split('\n').filter((l) => l.trim());
  const bySid = new Map();
  const order = [];
  for (const line of lines) {
    let sid = '';
    try {
      sid = JSON.parse(line).sid || '';
    } catch { /* чужая строка — остаётся как есть */ }
    const key = sid || `line:${order.length}`;
    if (!bySid.has(key)) order.push(key);
    bySid.set(key, line);
  }
  for (const s of summaries) {
    const key = s.sid;
    if (!bySid.has(key)) order.push(key);
    bySid.set(key, JSON.stringify(s));
  }
  return `${order.map((k) => bySid.get(k)).join('\n')}\n`;
}

// Сделать сводку durable, не выгружая: очередь и есть то, что переживёт этот
// процесс. Зовётся с края, который кончается ПОЗЖЕ последнего Stop сессии
// (фоновый приём реестра): его запись «model» иначе осталась бы только в
// локальной копии сводки, которую уже никто не увезёт.
//
// Срок ожидания короткий и берётся ЗДЕСЬ, а не приходит снаружи: этот вход
// зовут только края, идущие следом за ходом, и ждать им можно ровно столько же,
// сколько ждут метрики внутри хода. Параметр «свой срок» тут был бы мёртвым:
// другого числа ни один вызывающий не просит. Не встали — про это
// говорится строкой в журнале, а не тишиной.
//
// Выключатель, файл очереди и цель приходят от КРАЯ готовыми значениями: их читает
// тот, кто знает окружение. Цель нужна не для МЕСТА очереди (оно в каталоге
// состояния), а для вопроса адаптеру «доставка отсюда вообще возможна». Выключатель гасит и постановку — иначе
// прогон кейсов копил бы очередь.
export function queueSummary(summary, log, adapter, { off = false, queueFile = '', target = '' } = {}) {
  if (off) return false;
  const queue = queueFile || defaultQueue(adapter, target || storeTarget());
  // Очереди нет — значит нет и адаптера хранения. Это тоже пропуск, и назван он
  // возможностью: молчание здесь читалось бы как «сводка уехала».
  if (!queue) {
    if (log) {
      append(log, {
        kind: 'skip', ts: new Date().toISOString(), what: 'queue', capability: 'summary-store',
      });
    }
    return false;
  }
  const { ok, reason } = enqueueSummary(queue, summary, { waitMs: QUEUE_WAIT_MS });
  if (!ok && log) {
    const line = {
      kind: 'skip', ts: new Date().toISOString(), what: 'queue', reason,
    };
    // Срок называется только там, где он и был причиной.
    if (reason === 'locked') line.wait_ms = QUEUE_WAIT_MS;
    append(log, line);
  }
  return ok;
}

// Выгрузить очередь. Возвращает { status, delivered }. Срок ожидания лока
// приходит СНАРУЖИ, как и у постановки: у работника он свой, большой, а у
// края, идущего следом за ходом, — короткий. Умолчание короткое: длинное
// ожидание должен просить тот, кому и правда некуда спешить.
export function flushQueue({
  target, queueFile, adapter, waitMs = QUEUE_WAIT_MS, ...where
}) {
  if (!adapter || typeof adapter.available !== 'function' || !adapter.available(target)) {
    return { status: 'unsupported', capability: 'summary-store', delivered: 0 };
  }
  const { locked, value } = withLock(queueFile, () => {
    const pending = parseQueue(readQueueText(queueFile));
    if (!pending.length) return { status: 'nothing', delivered: 0 };

    const fetched = adapter.fetchBase(target, where);
    if (fetched.status !== 'ok') return { status: fetched.status, delivered: 0 };
    const { base } = fetched;

    // Последняя сводка сессии побеждает; строки раскладываются по дням.
    const latest = new Map();
    for (const s of pending) latest.set(s.sid, s);
    const byDay = new Map();
    for (const s of latest.values()) {
      const day = dayOf(s);
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(s);
    }

    const files = [];
    for (const [day, list] of byDay) {
      const current = adapter.readDay(target, { ...where, base, day });
      if (current.status === 'error') return { status: 'error', delivered: 0 };
      files.push({ day, content: upsertLines(current.text, list) });
    }

    const published = adapter.publish(target, {
      ...where, base, files, days: [...byDay.keys()].sort(), sessions: latest.size,
    });
    if (published.status !== 'ok') return { status: published.status, delivered: 0 };

    // Дописать в очередь во время выгрузки было некому: лок держится с её
    // чтения и до этой строки, поэтому снимается она целиком.
    try {
      fs.rmSync(queueFile, { force: true });
    } catch { /* очередь не снялась — сводки уедут второй раз, строка та же */ }
    return { status: 'stored', delivered: latest.size };
  }, { waitMs });
  return locked ? value : { status: 'locked', delivered: 0 };
}
