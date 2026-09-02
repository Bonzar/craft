// Хранение сводок сессий в ветке `metrics` репозитория системы.
//
// Ветка не выкачивается и не чекаутится: коммит собирается plumbing-командами
// гита поверх свежего origin/metrics — временный индекс, write-tree,
// commit-tree, push одного коммита. Рабочее дерево и ветка сессии не трогаются.
// Файл на ветке — `summaries/<дата UTC>.jsonl`, по строке на сессию; строка
// сессии на том же дне ЗАМЕНЯЕТСЯ (сводка пишется на каждом Stop заново).
//
// Очередь: сводка сначала ложится в локальный файл очереди и уезжает оттуда.
// Нет сети — очередь копится и доезжает на следующем Stop; гонка с другой
// сессией (push не fast-forward) — то же самое: следующий заход соберёт коммит
// поверх нового origin/metrics. Fail quiet: сломанное хранение не трогает ход.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const IDENTITY = {
  GIT_AUTHOR_NAME: 'metrics', GIT_AUTHOR_EMAIL: 'metrics@craft',
  GIT_COMMITTER_NAME: 'metrics', GIT_COMMITTER_EMAIL: 'metrics@craft',
};

function gitIn(target, extraEnv = {}) {
  return (...args) => {
    const res = spawnSync('git', ['-C', target, ...args], {
      encoding: 'utf8',
      env: { ...process.env, LC_ALL: 'C.UTF-8', ...IDENTITY, ...extraEnv },
      maxBuffer: 64 * 1024 * 1024,
    });
    return {
      ok: res.status === 0,
      out: (res.stdout || '').replace(/\n+$/, ''),
      err: (res.stderr || '').replace(/\n+$/, ''),
    };
  };
}

// Файл очереди по умолчанию — в общем git-каталоге чекаута: он переживает и
// сессии, и перезагрузки, а в дерево не попадает.
export function defaultQueue(target) {
  const g = gitIn(target);
  const common = g('rev-parse', '--path-format=absolute', '--git-common-dir');
  const dir = common.ok && common.out ? common.out : os.tmpdir();
  return path.join(dir, 'metrics-queue.jsonl');
}

export function enqueue(queueFile, summary) {
  try {
    fs.mkdirSync(path.dirname(queueFile), { recursive: true });
    fs.appendFileSync(queueFile, `${JSON.stringify(summary)}\n`);
    return true;
  } catch {
    return false;
  }
}

function readQueue(queueFile) {
  let text = '';
  try {
    text = fs.readFileSync(queueFile, 'utf8');
  } catch {
    return [];
  }
  const out = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const rec = JSON.parse(line);
      if (rec && typeof rec === 'object' && rec.sid) out.push(rec);
    } catch { /* битая строка очереди — пропускается */ }
  }
  return out;
}

// День сводки по UTC: один календарь у всех машин.
export function dayOf(summary) {
  const t = Date.parse(summary.ended_at || summary.ts || '');
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

// Выгрузить очередь в ветку. Возвращает { status, delivered }:
//   stored — коммит ушёл, очередь пуста; nothing — очередь пуста и до этого;
//   offline — origin недоступен, очередь цела; push-failed — push отклонён
//   (гонка или сеть), очередь цела; locked — параллельный заход уже работает;
//   error — сборка коммита не удалась, очередь цела.
export function flushQueue({
  target, queueFile, branch = 'metrics', remote = 'origin', dir = 'summaries',
}) {
  const pending = readQueue(queueFile);
  if (!pending.length) return { status: 'nothing', delivered: 0 };

  const lock = `${queueFile}.lock`;
  try {
    fs.mkdirSync(lock);
  } catch {
    return { status: 'locked', delivered: 0 };
  }
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
        input: content, encoding: 'utf8', env: { ...process.env, LC_ALL: 'C.UTF-8' },
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
    try {
      fs.rmSync(queueFile, { force: true });
    } catch { /* очередь не снялась — сводки уедут второй раз, строка та же */ }
    return { status: 'stored', delivered: latest.size };
  } finally {
    try { fs.rmSync(indexFile, { force: true }); } catch { /* индекса нет */ }
    try { fs.rmSync(lock, { recursive: true, force: true }); } catch { /* замок уже снят */ }
  }
}
