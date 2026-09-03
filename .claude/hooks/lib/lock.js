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

// Возраст — крайняя мера, а не основной признак: работа под локом бывает
// заведомо долгой (выгрузка метрик — fetch и цепочка команд гита, каждая со
// своим потолком в две минуты), и по одному возрасту живой лок отбирали бы у
// работающего процесса — второй заходил бы в критическую секцию рядом с первым,
// а первый, выходя, снимал бы уже чужой лок. Основной признак — ХОЗЯИН: пока
// процесс, взявший лок, жив, лок не отбирается вовсе, сколько бы он ни держался.
const LOCK_STALE_MS = 300000;

function lockDir(file) {
  return `${file}.lock`;
}

function ownerFile(dir) {
  return path.join(dir, 'owner');
}

// Хозяин лока. 0 — неизвестен: каталог мог быть создан мгновение назад, а имя
// хозяина ещё не записано, или запись не удалась.
function ownerPid(dir) {
  try {
    const pid = Number(fs.readFileSync(ownerFile(dir), 'utf8').trim());
    return Number.isInteger(pid) && pid > 0 ? pid : 0;
  } catch {
    return 0;
  }
}

// Жив ли процесс. Сигнал 0 ничего не шлёт, только проверяет; EPERM значит, что
// процесс есть, но чужой. Номера переиспользуются, поэтому признак не абсолютный
// — но ошибается он в сторону ОЖИДАНИЯ, а не отбора живого лока.
function alive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return Boolean(err) && err.code === 'EPERM';
  }
}

function takeLock(file) {
  const dir = lockDir(file);
  for (;;) {
    try {
      // Без recursive: с ним mkdir НЕ бросает на существующем каталоге, и лок
      // перестаёт быть локом — два процесса заходят внутрь одновременно.
      fs.mkdirSync(dir);
      try {
        fs.writeFileSync(ownerFile(dir), String(process.pid));
      } catch { /* хозяин неизвестен — такой лок снимется по возрасту */ }
      return dir;
    } catch (err) {
      if (err && err.code !== 'EEXIST') return '';
      const owner = ownerPid(dir);
      if (owner && !alive(owner)) {
        // Хозяин умер, не сняв лок: отбираем сразу, возраста не ждём.
        try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* уже снят */ }
        continue;
      }
      if (!owner) {
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
      }
      try {
        execFileSync('sleep', ['0.05'], { stdio: 'ignore' });
      } catch {
        return '';
      }
    }
  }
}

// Снимается ТОЛЬКО свой лок: чужой каталог на этом же месте — уже другой лок
// (наш отобрали как брошенный), и снести его значило бы выпустить внутрь
// третьего, пока второй работает.
function freeLock(dir) {
  if (!dir) return;
  if (ownerPid(dir) !== process.pid) return;
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
