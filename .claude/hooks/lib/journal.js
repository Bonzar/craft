// Журнал событий сессии: строка на каждое ЧТЕНИЕ и каждую ЗАПИСЬ, плюс строка
// на сигнал хода (ошибка инструмента, сигнал инцидента).
//
// Зачем он есть. Читатели слоя — гвард «не правь то, чего не читал» и сборка
// набора изменений в базе заметок — берут факты ОТСЮДА, а не из транскрипта
// харнеса. Транскрипт есть не у всякого харнеса и у каждого свой; журнал
// строится из КАНОНИЧЕСКОГО события и потому одинаков для всех.
//
// Формат — JSONL, строка на факт. Четыре поля стоят у КАЖДОЙ строки, какой бы ни
// была операция: читателю не приходится знать, у какого `op` какая форма.
//   ts      — когда (ISO-8601 UTC);
//   op      — что за операция (OPS ниже);
//   tool    — чем сделана; строку приносит обёртка, здесь её не разбирают;
//   call_id — идентификатор вызова ИЗ ЯДРА события; пусто там, где харнес его не
//             даёт (реплика).
// Сверх них по смыслу операции: `targets` — над чем (СПИСКОМ: у команды целей
// бывает несколько), `text` — тело сигнала, `unsupported` — имя того, чего не
// хватило.
//
// Имён инструментов здесь нет: область вызова и его форму приносит адаптер
// харнеса (journal-claude.js), разбор команды — адаптер интерпретатора
// (write-targets-bash.js). Общая часть получает их результат данными.
//
// Содержимое правок, команд и ответов инструментов в журнал НЕ идёт: у сигнала
// пишется голова текста, и только у него — по ней инстинкт-контур и различает
// повторяющиеся неудачи.
import fs from 'node:fs';
import { eachJsonl } from './jsonl.js';
import { mutationOf, durableTargets } from './write-targets.js';

export const OPS = Object.freeze({
  READ: 'read',
  WRITE: 'write',
  ERROR: 'error',
  INCIDENT: 'incident',
  // Вопрос «менял ли вызов мир» неразрешим, потому что адаптера нет. Строка всё
  // равно ложится: молчание тут читалось бы как «ничего не делал», а это
  // догадка. Имя недостающего стоит в `unsupported` (решение 8).
  UNKNOWN: 'unknown',
});

// Сигнальные операции: по ним работает инстинкт-контур. Прежде их копил
// отдельный эфемерный буфер; теперь это обычные строки журнала, и читатель
// отличает их по `op`, а не по тому, в каком файле они лежат.
export const SIGNAL_OPS = Object.freeze([OPS.ERROR, OPS.INCIDENT]);

// Голова текста в БАЙТАХ, без обрыва посреди символа и без хвостовых переводов
// строки: сигнал ложится одной строкой, и длина у него ограничена.
export function head(text, limit) {
  const cut = Buffer.from(`${String(text ?? '')}\n`, 'utf8').subarray(0, limit);
  let end = cut.length;
  while (end > 0 && cut[end - 1] === 0x0a) end -= 1;
  return cut.subarray(0, end).toString('utf8').replace(/\n/g, ' ');
}

// Строка журнала: общая часть у всех операций одна, остальное дописывается
// вызывающим. Время приходит числом, чтобы одно событие давало одну метку у
// всех своих строк.
export function fact({
  op, tool = '', callId = '', at = Date.now(),
}, extra = {}) {
  return {
    ts: new Date(at).toISOString(),
    op: String(op || ''),
    tool: String(tool || ''),
    call_id: String(callId || ''),
    ...extra,
  };
}

// Возвращает, ЛЕГЛА ли строка: производителю это безразлично (журнал не
// пополнился, ход цел), а кейсу — нет.
export function appendFact(file, record) {
  if (!file) return false;
  try {
    fs.appendFileSync(file, `${JSON.stringify(record)}\n`);
    return true;
  } catch { return false; }
}

// Что за операция и над чем — по СОСТОЯВШЕМУСЯ вызову.
//
//   scope — область вызова от адаптера харнеса: {reads}|{session}|{};
//   call  — форма вызова оттуда же: {kind:'read', paths} | {kind:'edit', path}
//           | {kind:'command', text} | {};
//   adapters — commandWrites(текст) → {mutates, targets}; ignored(путь).
//
// Пустой `op` значит «строки нет»: вызов, правящий ход САМОЙ сессии (план,
// вопрос, список работы, расписание пробуждения), ни чтением, ни записью мира не
// является, и запись про него была бы не фактом, а шумом. Тем же пустым `op`
// отвечает и вызов, который мир потрогал, но ничего в нём не изменил.
export function factOf(scope = {}, call = {}, adapters = {}) {
  if (scope.session === true) return { op: '' };
  if (scope.reads === true) {
    // Цель чтения называется там, где её назвал адаптер. Не назвал — список
    // пустой: «читал, цель не названа». Пустой список честнее выдуманного —
    // гвард на нём не разрешит ничего, а догадка разрешила бы не то.
    const named = call.kind === 'read' && Array.isArray(call.paths) ? call.paths : [];
    return { op: OPS.READ, targets: named.filter(Boolean).map(String) };
  }
  const mutation = mutationOf(scope, call, adapters);
  if (mutation.status !== 'ok') return { op: OPS.UNKNOWN, unsupported: mutation.capability };
  if (!mutation.mutates) return { op: '' };
  return { op: OPS.WRITE, targets: durableTargets(call, adapters) };
}

// Сигнальные строки журнала ПОСЛЕ отметки и текущий размер журнала. Отметка —
// байтовое смещение: журнал только дописывается, поэтому смещение и есть
// «досюда уже разбирали».
//
// Отметка БОЛЬШЕ файла означает, что журнал начался заново (другая сессия, файл
// подмели): читаем с начала. Иначе новые сигналы молча считались бы разобранными
// — то есть контур замолчал бы ровно там, где обязан говорить.
//
// Журнала нет или он не читается — пустой ответ: это НЕ «сигналов не было», и
// отличает одно от другого вызывающий, у которого есть факт `journal`.
export function readSignals(file, from = 0) {
  if (!file) return { records: [], size: 0 };
  let size = 0;
  let text = '';
  try {
    size = fs.statSync(file).size;
    const start = Number.isFinite(from) && from > 0 && from <= size ? from : 0;
    const fd = fs.openSync(file, 'r');
    try {
      const buf = Buffer.alloc(size - start);
      if (buf.length) fs.readSync(fd, buf, 0, buf.length, start);
      text = buf.toString('utf8');
    } finally {
      fs.closeSync(fd);
    }
  } catch { return { records: [], size: 0 }; }

  const records = [];
  eachJsonl(text, (entry) => {
    if (SIGNAL_OPS.includes(entry.op)) records.push(entry);
  });
  return { records, size };
}
