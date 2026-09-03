// Лок на ЦИКЛ правки файла состояния и атомарная запись.
//
// withLock(файл, действие, {waitMs}) → {locked, value}. locked: false значит,
// что лок не достался за отведённый срок и ДЕЙСТВИЕ НЕ ВЫПОЛНЯЛОСЬ — вызывающий
// обязан это заметить и сказать вслух, а не считать работу сделанной.
// atomicWrite(файл, текст) → true/false: запись через tmp и переименование.
//
// Инварианты:
// — ожидание всегда ограничено waitMs: хук не имеет права зависнуть на чужом
//   локе, а метрика не стоит того, чтобы её ждал ход Влада;
// — лок, который держат дольше абсолютного потолка, отбирается даже у живого
//   хозяина: иначе один повисший процесс запирает состояние навсегда;
// — снимается только СВОЙ лок, по имени хозяина внутри каталога;
// — вложенный вызов на ТОТ ЖЕ путь лок повторно не берёт (иначе правка поверх
//   общей клинила бы сама себя); на другой путь — берёт, поэтому брать два
//   разных лока вложенно можно только в одном и том же порядке.
//
// Каталог как примитив блокировки: mkdir либо создал, либо застал чужой, и так
// на любой файловой системе.
import fs from 'node:fs';
import path from 'node:path';

// Сколько ждать чужой лок по умолчанию. Вызывающий, которому ждать нельзя
// (метрики стоят в цепочке хода), передаёт своё, меньшее число.
export const LOCK_WAIT_MS = 5000;

// Возраст, после которого лок без известного хозяина считается брошенным:
// каталог мог остаться от процесса, не успевшего записать своё имя.
const LOCK_STALE_MS = 300000;

// Абсолютный потолок: столько лок не держит никакая честная работа, включая
// выгрузку метрик с её сетевыми вызовами. Дальше он отбирается и у ЖИВОГО
// хозяина — иначе повисший процесс (или чужой процесс, занявший его номер после
// перезагрузки) запирает состояние до конца жизни машины.
const LOCK_MAX_AGE_MS = 900000;

const STEP_MS = 50;

// Синхронная пауза без форка: execFileSync('sleep') стоил процесса на каждые
// 50 мс ожидания, и на занятом локе хук съедал их сотнями.
function pause(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function lockDir(file) {
  return `${file}.lock`;
}

function ownerFile(dir) {
  return path.join(dir, 'owner');
}

// Хозяин лока. 0 — неизвестен: каталог мог быть создан мгновение назад, а имя
// хозяина ещё не записано.
function ownerPid(dir) {
  try {
    const pid = Number(fs.readFileSync(ownerFile(dir), 'utf8').trim());
    return Number.isInteger(pid) && pid > 0 ? pid : 0;
  } catch {
    return 0;
  }
}

// Жив ли процесс. Сигнал 0 ничего не шлёт, только проверяет; EPERM значит, что
// процесс есть, но чужой. Номера переиспользуются, поэтому признак не
// абсолютный — от вечного ожидания на переиспользованном номере спасает
// LOCK_MAX_AGE_MS.
function alive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return Boolean(err) && err.code === 'EPERM';
  }
}

// Возраст каталога лока; -1 значит, что лока уже нет.
function ageOf(dir) {
  try {
    return Date.now() - fs.statSync(dir).mtimeMs;
  } catch {
    return -1;
  }
}

function drop(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch { /* уже снят кем-то ещё */ }
}

// Каталог взятого лока или пустая строка, если за waitMs он не достался.
function takeLock(file, waitMs) {
  const dir = lockDir(file);
  const deadline = Date.now() + Math.max(0, waitMs);
  for (;;) {
    try {
      // Без recursive: с ним mkdir НЕ бросает на существующем каталоге, и лок
      // перестаёт быть локом — два процесса заходят внутрь одновременно.
      fs.mkdirSync(dir);
      try {
        fs.writeFileSync(ownerFile(dir), String(process.pid));
      } catch {
        // Лок без имени хозяина не снимет никто, кроме времени: держать его
        // значит запереть файл на LOCK_STALE_MS. Отдаём сразу.
        drop(dir);
        return '';
      }
      return dir;
    } catch (err) {
      if (err && err.code !== 'EEXIST') return '';
      const age = ageOf(dir);
      if (age < 0) continue; // лок исчез между попыткой и замером
      const owner = ownerPid(dir);
      // Мёртвый хозяин — лок брошен наверняка, возраста ждать незачем.
      // Хозяин неизвестен — по LOCK_STALE_MS. Живой — только по абсолютному.
      if (owner && !alive(owner)) {
        drop(dir);
        continue;
      }
      if (age > (owner ? LOCK_MAX_AGE_MS : LOCK_STALE_MS)) {
        drop(dir);
        continue;
      }
      const left = deadline - Date.now();
      if (left <= 0) return '';
      pause(Math.min(STEP_MS, left));
    }
  }
}

// Снимается ТОЛЬКО свой лок: чужой каталог на этом же месте — уже другой лок
// (наш отобрали как брошенный), и снести его значило бы выпустить внутрь
// третьего, пока второй работает.
function freeLock(dir) {
  if (ownerPid(dir) !== process.pid) return;
  drop(dir);
}

// Пути, залоченные ЭТИМ процессом. Множество, а не флаг: с одним флагом
// вложенный лок на ДРУГОЙ файл молча не брался вовсе, и второе состояние
// оставалось без защиты.
const held = new Set();

export function withLock(file, run, { waitMs = LOCK_WAIT_MS } = {}) {
  const key = path.resolve(file);
  if (held.has(key)) return { locked: true, value: run() };
  const dir = takeLock(file, waitMs);
  if (!dir) return { locked: false, value: undefined };
  held.add(key);
  try {
    return { locked: true, value: run() };
  } finally {
    held.delete(key);
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
