// Адаптер хранения сводок под git: единственное место, где хранение знает
// команды git. Общая часть (metrics-store.js) держит очередь и склейку строк и
// получает отсюда только ДАННЫЕ — есть ли база, что лежит в файле дня, ушёл ли
// коммит, — и ни одной строки git не видит.
//
// available(target) → есть ли под каталогом git-чекаут. Нет — у возможности
//   «хранение» нет адаптера, и хранение отвечает unsupported, а не error.
// queueDir(target) → каталог, переживающий смену воркри, или ''.
// fetchBase(target, {remote, branch}) → {status: 'ok'|'offline', base}
//   base — пустая строка, когда ветки на сервере ещё нет: это не ошибка.
// readDay(target, {base, file}) → {status: 'ok'|'missing'|'error', text}
//   missing и error различаются обязательно: сбой, принятый за «файла нет»,
//   заменяет дневной файл сервера своими строками и стирает чужие сессии.
// publish(target, {base, files, message, remote, branch}) → {status}
//   ok | error | push-failed. files — [{file, content}].
//
// Ветка не выкачивается и не чекаутится: коммит собирается plumbing-командами
// поверх свежего origin/<branch> во временном индексе. Рабочее дерево и ветка
// сессии не трогаются.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { commonDir } from './git.js';

const IDENTITY = {
  GIT_AUTHOR_NAME: 'metrics', GIT_AUTHOR_EMAIL: 'metrics@craft',
  GIT_COMMITTER_NAME: 'metrics', GIT_COMMITTER_EMAIL: 'metrics@craft',
};

// Потолок на команду: сетевой вызов, повисший навсегда, держал бы лок очереди и
// не давал выгрузиться никому.
const GIT_TIMEOUT_MS = 120000;

function gitIn(target, extraEnv = {}) {
  return (args, input) => {
    const res = spawnSync('git', ['-C', target, ...args], {
      encoding: 'utf8',
      input,
      env: { ...process.env, LC_ALL: 'C.UTF-8', ...IDENTITY, ...extraEnv },
      maxBuffer: 64 * 1024 * 1024,
      timeout: GIT_TIMEOUT_MS,
    });
    return {
      ok: res.status === 0,
      code: res.status,
      out: (res.stdout || '').replace(/\n+$/, ''),
      err: (res.stderr || '').replace(/\n+$/, ''),
    };
  };
}

export function available(target) {
  return Boolean(target) && Boolean(commonDir(target));
}

export function queueDir(target) {
  return commonDir(target);
}

export function fetchBase(target, { remote = 'origin', branch = 'metrics' } = {}) {
  const g = gitIn(target);
  const remoteRef = `refs/remotes/${remote}/${branch}`;
  const fetched = g(['fetch', '--quiet', remote, `+refs/heads/${branch}:${remoteRef}`]);
  if (fetched.ok) return { status: 'ok', base: g(['rev-parse', remoteRef]).out };
  // Ветки на сервере ещё нет — это первая выгрузка, а не потеря сети.
  if (/couldn't find remote ref|invalid refspec/i.test(fetched.err)) return { status: 'ok', base: '' };
  return { status: 'offline', base: '' };
}

// Содержимое файла дня в базе. Отсутствие пути проверяется ОТДЕЛЬНО от чтения:
// у `git show` любой сбой — таймаут, переполненный буфер — выглядит так же, как
// «такого файла нет», и молча превращает дополнение дневного файла в замену.
export function readDay(target, { base, file }) {
  if (!base) return { status: 'missing', text: '' };
  const g = gitIn(target);
  // Наличие пути спрашивается через ls-tree, а не через `cat-file -e`: у
  // последнего отсутствующий путь даёт тот же ненулевой код, что и сбой. У
  // ls-tree отсутствие — это УСПЕХ с пустым выводом, и сбой от него отличим.
  const listed = g(['ls-tree', '--name-only', base, '--', file]);
  if (!listed.ok) return { status: 'error', text: '' };
  if (!listed.out) return { status: 'missing', text: '' };
  const shown = g(['show', `${base}:${file}`]);
  return shown.ok ? { status: 'ok', text: `${shown.out}\n` } : { status: 'error', text: '' };
}

export function publish(target, {
  base = '', files = [], message = '', remote = 'origin', branch = 'metrics',
} = {}) {
  const indexFile = path.join(os.tmpdir(), `metrics-index.${process.pid}.${Date.now()}`);
  const g = gitIn(target, { GIT_INDEX_FILE: indexFile });
  try {
    if (base) {
      if (!g(['read-tree', base]).ok) return { status: 'error' };
    } else {
      try { fs.rmSync(indexFile, { force: true }); } catch { /* индекса ещё нет */ }
      if (!g(['read-tree', '--empty']).ok) return { status: 'error' };
    }

    for (const { file, content } of files) {
      const blob = g(['hash-object', '-w', '--stdin'], content);
      if (!blob.ok || !blob.out) return { status: 'error' };
      if (!g(['update-index', '--add', '--cacheinfo', `100644,${blob.out},${file}`]).ok) {
        return { status: 'error' };
      }
    }

    const tree = g(['write-tree']).out;
    if (!tree) return { status: 'error' };
    const commitArgs = ['commit-tree', tree, '-m', message];
    if (base) commitArgs.push('-p', base);
    const commit = g(commitArgs).out;
    if (!commit) return { status: 'error' };
    if (!g(['push', '--quiet', remote, `${commit}:refs/heads/${branch}`]).ok) {
      return { status: 'push-failed' };
    }
    return { status: 'ok' };
  } finally {
    try { fs.rmSync(indexFile, { force: true }); } catch { /* индекса нет */ }
  }
}
