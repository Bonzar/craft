// Хранение сводок сессий: очередь и раскладка по дням. Про инструмент, которым
// сводки уезжают, этот файл не знает ничего — команды живут в адаптере
// (metrics-store-git.js), сюда приходят данные.
//
// Файл на стороне хранилища — `summaries/<дата UTC>.jsonl`, по строке на
// сессию; день берётся по НАЧАЛУ сессии, чтобы сессия, перешагнувшая полночь,
// не оставила две строки в двух файлах и не посчиталась дважды.
//
// Очередь: сводка сначала ложится в локальный файл очереди и уезжает оттуда.
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
// Исходы flushQueue: stored — доставлено, очередь снята; nothing — очередь
// пуста; offline — хранилище недоступно, очередь цела; push-failed — отказ на
// записи; error — сборка не удалась; unsupported — адаптера для этой цели нет;
// locked — лок занят дольше срока, выгрузка не начиналась.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { withLock, atomicWrite } from './lock.js';
import { eachJsonl } from './jsonl.js';
import * as store from './metrics-store-git.js';

// Потолок очереди в строках. Строка на сессию, так что потолок — про число
// сессий, накопившихся, пока доставка не проходит.
export const QUEUE_CAP = 500;

// Ждать лок в хуке нельзя: постановка в очередь стоит в цепочке Stop.
const ENQUEUE_WAIT_MS = 300;

// Файл очереди по умолчанию — в каталоге, который переживает и сессии, и смену
// воркри, а в дерево не попадает. Каталог называет адаптер.
export function defaultQueue(target) {
  return path.join(store.queueDir(target) || os.tmpdir(), 'metrics-queue.jsonl');
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
export function enqueue(queueFile, summary) {
  if (!queueFile || !summary || !summary.sid) return false;
  const { locked, value } = withLock(queueFile, () => {
    const bySid = new Map(parseQueue(readQueueText(queueFile)).map((r) => [r.sid, r]));
    bySid.delete(summary.sid);
    bySid.set(summary.sid, summary);
    const rows = [...bySid.values()].slice(-QUEUE_CAP);
    return writeQueue(queueFile, rows);
  }, { waitMs: ENQUEUE_WAIT_MS });
  return locked ? value : false;
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
export function upsertLines(text, summaries) {
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

// Выгрузить очередь. Возвращает { status, delivered }.
export function flushQueue({
  target, queueFile, branch = 'metrics', remote = 'origin', dir = 'summaries',
}) {
  if (!store.available(target)) return { status: 'unsupported', capability: 'metrics-store', delivered: 0 };
  const { locked, value } = withLock(queueFile, () => {
    const pending = parseQueue(readQueueText(queueFile));
    if (!pending.length) return { status: 'nothing', delivered: 0 };

    const fetched = store.fetchBase(target, { remote, branch });
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
      const file = `${dir}/${day}.jsonl`;
      const current = store.readDay(target, { base, file });
      if (current.status === 'error') return { status: 'error', delivered: 0 };
      files.push({ file, content: upsertLines(current.text, list) });
    }

    const days = [...byDay.keys()].sort().join(', ');
    const published = store.publish(target, {
      base, files, message: `metrics: ${days} — ${latest.size} сводок`, remote, branch,
    });
    if (published.status !== 'ok') return { status: published.status, delivered: 0 };

    // Дописать в очередь во время выгрузки было некому: лок держится с её
    // чтения и до этой строки, поэтому снимается она целиком.
    try {
      fs.rmSync(queueFile, { force: true });
    } catch { /* очередь не снялась — сводки уедут второй раз, строка та же */ }
    return { status: 'stored', delivered: latest.size };
  });
  return locked ? value : { status: 'locked', delivered: 0 };
}
