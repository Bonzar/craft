// Лок на ЦИКЛ правки файла состояния и атомарная запись.
//
// Живёт отдельным модулем, потому что нужен двум разным контурам — реестру
// одобренного и журналу метрик, — а разъехавшиеся копии дали бы состояние,
// которое у одного защищено, а у другого нет.
//
// Сама запись атомарна переименованием, а «прочитал — поправил — записал»
// вокруг неё нет: два параллельных хука читают одно состояние, и второй
// затирает правку первого. Каталог — примитивная блокировка на любой файловой
// системе: mkdir либо создал, либо застал чужой.
//
// Занят — ЖДЁМ, а не пропускаем: пропущенная запись роняет ту работу, ради
// которой лок и берётся. Своего потолка у ожидания нет; снимается только лок,
// брошенный упавшим процессом, — по возрасту каталога.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const LOCK_STALE_MS = 300000;

function lockDir(file) {
  return `${file}.lock`;
}

function takeLock(file) {
  const dir = lockDir(file);
  for (;;) {
    try {
      // Без recursive: с ним mkdir НЕ бросает на существующем каталоге, и лок
      // перестаёт быть локом — два процесса заходят внутрь одновременно.
      fs.mkdirSync(dir);
      return dir;
    } catch (err) {
      if (err && err.code !== 'EEXIST') return '';
      let age = 0;
      try {
        age = Date.now() - fs.statSync(dir).mtimeMs;
      } catch {
        continue; // лок исчез между попыткой и замером — пробуем снова
      }
      if (age > LOCK_STALE_MS) {
        try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* уже снят */ }
        continue;
      }
      try {
        execFileSync('sleep', ['0.05'], { stdio: 'ignore' });
      } catch {
        return '';
      }
    }
  }
}

function freeLock(dir) {
  if (!dir) return;
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch { /* лок не снялся — его добьёт следующий по возрасту */ }
}

// withLock(файл, действие) — единственная точка взятия лока. Вложенные вызовы
// лок повторно НЕ берут: иначе правка, сделанная поверх общей, клинила бы сама
// себя. Флаг живёт на процесс, как и сам вложенный вызов.
let held = false;

export function withLock(file, run) {
  if (held) return run();
  const dir = takeLock(file);
  held = true;
  try {
    return run();
  } finally {
    held = false;
    freeLock(dir);
  }
}

// Запись через временный файл и переименование: читатель видит либо прежнее
// содержимое, либо новое, но никогда половину и никогда пустоту.
export function atomicWrite(file, text) {
  const tmp = `${file}.tmp.${process.pid}`;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(tmp, text);
    fs.renameSync(tmp, file);
    return true;
  } catch {
    try { fs.rmSync(tmp, { force: true }); } catch { /* и убрать не вышло */ }
    return false;
  }
}
