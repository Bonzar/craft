// Хранение сводок сессий в ветке `metrics` репозитория системы.
//
// Ветка не выкачивается и не чекаутится: коммит собирается plumbing-командами
// гита поверх свежего origin/metrics — временный индекс, write-tree,
// commit-tree, push одного коммита. Рабочее дерево и ветка сессии не трогаются.
// Файл на ветке — `summaries/<дата UTC>.jsonl`, по строке на сессию; день
// берётся по НАЧАЛУ сессии, чтобы сессия, перешагнувшая полночь, не оставила
// две строки в двух файлах и не посчиталась дважды.
//
// Очередь: сводка сначала ложится в локальный файл очереди и уезжает оттуда.
// Нет сети — очередь копится и доезжает на следующем Stop. Очередь держит по
// ОДНОЙ строке на сессию (сводка каждого Stop заменяет прежнюю) и обрезана
// сверху: иначе на репозитории, куда push запрещён навсегда, она росла бы
// строкой на каждый ход до конца жизни чекаута.
//
// И постановка в очередь, и выгрузка идут под ОДНИМ локом (lib/lock.js): пока
// выгрузка держит лок, дописать в очередь некому, поэтому после удачного пуша
// очередь снимается целиком, без сверки со снимком. Лок ждут, а не бросают:
// брошенная выгрузка означала бы сводку, которую уже никто не увезёт.
// Fail quiet: сломанное хранение не трогает ход.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { withLock, atomicWrite } from './lock.js';
import { eachJsonl } from './metrics.js';
import { commonDir } from './git.js';

const IDENTITY = {
  GIT_AUTHOR_NAME: 'metrics', GIT_AUTHOR_EMAIL: 'metrics@craft',
  GIT_COMMITTER_NAME: 'metrics', GIT_COMMITTER_EMAIL: 'metrics@craft',
};

// Потолок на команду гита: сетевой вызов, повисший навсегда, держал бы лок и
// не давал выгрузиться никому.
const GIT_TIMEOUT_MS = 120000;

// Потолок очереди в строках. Строка на сессию, так что потолок — про число
// сессий, накопившихся, пока push не проходит.
export const QUEUE_CAP = 500;

function gitIn(target, extraEnv = {}) {
  return (...args) => {
    const res = spawnSync('git', ['-C', target, ...args], {
      encoding: 'utf8',
      env: { ...process.env, LC_ALL: 'C.UTF-8', ...IDENTITY, ...extraEnv },
      maxBuffer: 64 * 1024 * 1024,
      timeout: GIT_TIMEOUT_MS,
    });
    return {
      ok: res.status === 0,
      out: (res.stdout || '').replace(/\n+$/, ''),
      err: (res.stderr || '').replace(/\n+$/, ''),
    };
  };
}

// Файл очереди по умолчанию — в общем git-каталоге чекаута: он переживает и
// сессии, и смену воркри, а в дерево не попадает.
export function defaultQueue(target) {
  const common = commonDir(target);
  return path.join(common || os.tmpdir(), 'metrics-queue.jsonl');
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
  return withLock(queueFile, () => {
    const bySid = new Map(parseQueue(readQueueText(queueFile)).map((r) => [r.sid, r]));
    bySid.delete(summary.sid);
    bySid.set(summary.sid, summary);
    const rows = [...bySid.values()].slice(-QUEUE_CAP);
    return writeQueue(queueFile, rows);
  });
}

// День сводки — по НАЧАЛУ сессии и по UTC: один календарь у всех машин, и одна
// строка на сессию даже когда сессия перешагнула полночь.
export function dayOf(summary) {
  const t = Date.parse(summary.started_at || summary.ended_at || summary.ts || '');
  const d = Number.isFinite(t) ? new Date(t) : new Date();
  return d.toISOString().slice(0, 10);
}

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

// Пора ли выгружать: сеть и коммит на КАЖДОМ Stop дали бы сотни коммитов в
// ветке за день и столько же fetch. Очередь при этом пополняется всегда, так
// что отложенная выгрузка ничего не теряет — она лишь увозит пачкой.
// Интервал в секундах; 0 — выгружать всегда (так гоняются тесты).
export function dueForFlush(queueFile, intervalSec) {
  const seconds = Number(intervalSec);
  if (!Number.isFinite(seconds) || seconds <= 0) return true;
  try {
    return Date.now() - fs.statSync(`${queueFile}.stamp`).mtimeMs >= seconds * 1000;
  } catch {
    return true; // отметки ещё нет — первая выгрузка идёт сразу
  }
}

// Отметка ПОПЫТКИ, а не удачи: интервал ограничивает поход в сеть. Отмечать
// только успех значило бы, что при недоступном origin или отклонённом push
// каждый следующий Stop снова лезет в сеть — а fetch там до двух минут, и
// работники копятся на локе очереди всю аварию.
function markFlushed(queueFile) {
  try {
    fs.writeFileSync(`${queueFile}.stamp`, '');
  } catch { /* без отметки интервал просто не сработает */ }
}

// Выгрузить очередь в ветку. Возвращает { status, delivered }:
//   stored — коммит ушёл, очередь снята; nothing — очередь пуста;
//   offline — origin недоступен, очередь цела; push-failed — push отклонён
//   (гонка или права), очередь цела; error — сборка коммита не удалась.
export function flushQueue({
  target, queueFile, branch = 'metrics', remote = 'origin', dir = 'summaries',
  intervalSec = 0,
}) {
  return withLock(queueFile, () => {
    // «Пора ли» проверяется ПОД ЛОКОМ, вместе с чтением очереди. Снаружи это
    // решение успевало устареть: пока второй работник ждал лок, первый успевал
    // выгрузиться и поставить отметку, а ждавший всё равно шёл в сеть — то есть
    // интервал не соблюдался ровно тогда, когда работников больше одного.
    if (!dueForFlush(queueFile, intervalSec)) return { status: 'queued', delivered: 0 };
    const pending = parseQueue(readQueueText(queueFile));
    if (!pending.length) return { status: 'nothing', delivered: 0 };
    // Отметка ставится ДО сети: интервал ограничивает попытки, а не удачи.
    markFlushed(queueFile);

    const indexFile = path.join(os.tmpdir(), `metrics-index.${process.pid}.${Date.now()}`);
    const g = gitIn(target, { GIT_INDEX_FILE: indexFile });
    try {
      const remoteRef = `refs/remotes/${remote}/${branch}`;
      const fetched = g('fetch', '--quiet', remote, `+refs/heads/${branch}:${remoteRef}`);
      let base = '';
      if (fetched.ok) {
        base = g('rev-parse', remoteRef).out;
      } else if (!/couldn't find remote ref|Couldn't find remote ref|invalid refspec/.test(fetched.err)) {
        return { status: 'offline', delivered: 0 };
      }

      // Последняя сводка сессии побеждает; строки раскладываются по дням.
      const latest = new Map();
      for (const s of pending) latest.set(s.sid, s);
      const byDay = new Map();
      for (const s of latest.values()) {
        const day = dayOf(s);
        if (!byDay.has(day)) byDay.set(day, []);
        byDay.get(day).push(s);
      }

      if (base) {
        if (!g('read-tree', base).ok) return { status: 'error', delivered: 0 };
      } else {
        try { fs.rmSync(indexFile, { force: true }); } catch { /* индекса ещё нет */ }
        if (!g('read-tree', '--empty').ok) return { status: 'error', delivered: 0 };
      }

      for (const [day, list] of byDay) {
        const file = `${dir}/${day}.jsonl`;
        const current = base ? g('show', `${base}:${file}`) : { ok: false, out: '' };
        const content = upsertLines(current.ok ? `${current.out}\n` : '', list);
        const blob = spawnSync('git', ['-C', target, 'hash-object', '-w', '--stdin'], {
          input: content,
          encoding: 'utf8',
          env: { ...process.env, LC_ALL: 'C.UTF-8' },
          timeout: GIT_TIMEOUT_MS,
        });
        const oid = (blob.stdout || '').trim();
        if (blob.status !== 0 || !oid) return { status: 'error', delivered: 0 };
        if (!g('update-index', '--add', '--cacheinfo', `100644,${oid},${file}`).ok) {
          return { status: 'error', delivered: 0 };
        }
      }

      const tree = g('write-tree').out;
      if (!tree) return { status: 'error', delivered: 0 };
      const days = [...byDay.keys()].sort().join(', ');
      const message = `metrics: ${days} — ${latest.size} сводок`;
      const commitArgs = ['commit-tree', tree, '-m', message];
      if (base) commitArgs.push('-p', base);
      const commit = g(...commitArgs).out;
      if (!commit) return { status: 'error', delivered: 0 };

      if (!g('push', '--quiet', remote, `${commit}:refs/heads/${branch}`).ok) {
        return { status: 'push-failed', delivered: 0 };
      }
      // Дописать в очередь во время выгрузки было некому: лок держится с её
      // чтения и до этой строки, поэтому снимается она целиком.
      try {
        fs.rmSync(queueFile, { force: true });
      } catch { /* очередь не снялась — сводки уедут второй раз, строка та же */ }
      return { status: 'stored', delivered: latest.size };
    } finally {
      try { fs.rmSync(indexFile, { force: true }); } catch { /* индекса нет */ }
    }
  });
}
