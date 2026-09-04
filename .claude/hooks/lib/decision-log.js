// Журнал решений хуков одного события: КАНАЛ между хуками цепочки.
//
// Пишет тот, кто решает, и строка ложится ДО печати и до выхода из процесса:
// упавший следом хук своего решения не теряет, и видит его любой процесс, а не
// только тот, кого позвали следом. Читают журнал диспетчер (оборвать ли цепочку) и
// хук метрик (каким был исход вызова).
//
// Строка: {ts, sid, call_id, event, hook, outcome, reason_class}. Текста причины
// в журнале НЕТ — только класс: журнал переживает сессию, а причина отказа
// содержит куски работы Влада.
import fs from 'node:fs';
import { decisionLog } from './paths.js';
import { eachJsonl } from './jsonl.js';

// Путь журнала: сессия и КАТАЛОГ СОСТОЯНИЯ берутся из самого события — оба поля
// ядра. Своей копии формулы каталога здесь нет.
function logOf(event) {
  return decisionLog(event && event.session_id, event && event.state_dir);
}

// Ключ строки — ПОЯВЛЕНИЕ события: его номер, имя и идентификатор вызова.
//
// Номер появления обязателен. Без него у событий без вызова (реплика, конец хода)
// ключ был бы один на всю сессию, и «последняя подходящая строка» приносила бы
// решение ПРОШЛОГО хода: второй конец хода читал бы блокировку первого, признак
// инцидента держался бы на каждой следующей реплике, а диспетчер, найдя чужое
// решение, пропускал бы оставшиеся гварды цепочки. Номер даёт обёртка — он свой у
// каждого прочтения события (event-claude.js).
function keyOf(event) {
  // Сессия в ключе обязательна: при пустом идентификаторе путь журнала общий
  // (`decisions.default.jsonl`), и без сверки отказ одной сессии читался бы как
  // решение другой — а канал несёт `deny` и `block`, обрывающие цепочку.
  return [event.sid || event.session_id || '', event.occurrence || '', event.event || '', event.call_id || ''].join('|');
}

// Одна запись на все три вида строк: собрать общую часть, дописать, назвать
// неудачу. Три копии этого тела уже разъезжались бы по составу полей.
function appendRecord(event, kind, extra) {
  const file = logOf(event);
  if (!file) return false;
  const record = {
    kind,
    ts: new Date().toISOString(),
    occurrence: (event && event.occurrence) || '',
    sid: (event && event.session_id) || '',
    call_id: (event && event.call_id) || '',
    event: (event && event.event) || '',
    ...extra,
  };
  try {
    fs.appendFileSync(file, `${JSON.stringify(record)}\n`);
    return true;
  } catch {
    // Журнал не пополнился. Решение всё равно напечатано и ход цел, поэтому
    // здесь не падение, а «нет» вызывающему: пострадала одна метрика, и её
    // отсутствие видно пустотой в сводке.
    return false;
  }
}

export function appendDecision(event, {
  outcome, hook = '', reasonClass = '', h = '', tool = '',
}) {
  return appendRecord(event, 'decision', {
    hook,
    outcome,
    class: reasonClass,
    // Хеш вызова: по нему сводка узнаёт «тот же вызов, прошедший после отказа».
    // Без него отказ и последующий проход не сшить — у отказанного вызова записи
    // метрик нет вовсе, цепочка обрывается на решении.
    h,
    tool,
  });
}

// Признак, замеченный одним хуком цепочки для другого (сегодня — «в реплике есть
// инцидент»). Тот же канал, что и решение: пишет заметивший, читает наблюдатель.
export function appendFlag(event, flag) {
  return appendRecord(event, 'flag', { flag });
}

// Замеры времени хуков цепочки: одна строка на событие, пишет диспетчер перед
// последним хуком. Одна, а не по строке на хук: цепочка стоит в ходе, и лишние
// записи на диск в ней платит Влад ожиданием.
export function appendTimings(event, hooks) {
  return appendRecord(event, 'timing', { hooks });
}

// Строки журнала по ЭТОМУ событию, последняя каждого вида. Берётся последняя:
// цепочка обрывается на первом решении, но при повторном запуске события (харнес
// шлёт его заново) свежая строка обязана перекрыть прежнюю.
// Журнал растёт всю сессию, а нужны из него всегда ПОСЛЕДНИЕ строки — те, что
// написала цепочка текущего события. Поэтому читается хвост, а не файл целиком:
// иначе к концу долгой сессии каждый хук цепочки перечитывал бы тысячи строк, и
// платил бы за это Влад ожиданием в ходе. Обрезанная первая строка отсеется
// разбором JSONL как битая.
const TAIL_BYTES = 64 * 1024;

function readTail(file) {
  let fd;
  try {
    const size = fs.statSync(file).size;
    const start = size > TAIL_BYTES ? size - TAIL_BYTES : 0;
    fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    return buf.toString('utf8');
  } catch {
    // Журнала нет или он не читается. Вернуть пустоту здесь безопасно и НЕ
    // означает «решения не было»: решение пишется до печати и до выхода, так что
    // нечитаемый журнал — это сбой диска, а не проход. Цепочка в этом случае
    // просто не оборвётся раньше времени, а решение уже напечатано.
    return '';
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function lastByKind(event) {
  const file = logOf(event);
  const out = { decision: null, timing: null, flags: new Set() };
  if (!file) return out;
  const text = readTail(file);
  if (!text) return out;
  const want = keyOf(event || {});
  eachJsonl(text, (rec) => {
    if (keyOf(rec) !== want) return;
    if (rec.kind === 'decision') out.decision = rec;
    else if (rec.kind === 'timing') out.timing = rec;
    else if (rec.kind === 'flag' && rec.flag) out.flags.add(rec.flag);
  });
  return out;
}

// Поставлен ли признак по этому событию.
export function hasFlag(event, flag) {
  return lastByKind(event).flags.has(flag);
}

// Отметка журнала: его размер. Диспетчер стоит в цепочке ХОДА и спрашивает
// «решили ли уже» после каждого хука; разбирать ради этого хвост каждый раз —
// 0.76 мс на вызов при журнале в 962 КиБ, то есть 3.8 мс на цепочку из пяти
// хуков. Отметка стоит 0.002 мс, и разбор нужен только когда журнал ВЫРОС.
export function journalStamp(event) {
  const file = logOf(event);
  if (!file) return -1;
  try {
    return fs.statSync(file).size;
  } catch {
    return 0; // файла ещё нет — это тоже отметка, и она изменится с первой записью
  }
}

// Решение по этому событию, либо null. null значит «решения не было» — то есть
// проход: молчащий гвард строки не пишет.
export function decisionFor(event) {
  return lastByKind(event).decision;
}

// Замеры времени по этому событию: {имя хука: мс}. Пусто — цепочка их не писала.
export function timingsFor(event) {
  const rec = lastByKind(event).timing;
  return rec && rec.hooks && typeof rec.hooks === 'object' ? rec.hooks : {};
}
