// Транскрипт Claude Code и токены хода из него. Формат транскрипта — форма этого
// харнеса целиком: файл JSONL, запись с сообщением, роли, поля usage. Общая часть
// его не читает: обёртка отдаёт ей ФАКТ `tokens` готовыми числами.
//
// tokens: {input, output, cache_read, cache_create, messages}.
import fs from 'node:fs';
import { eachJsonl } from './jsonl.js';

// Размер транскрипта на сейчас; нет файла — ноль. Им засевается граница хода на
// старте сессии: всё, что написано до старта, принадлежит прошлым ходам.
export function transcriptSize(file) {
  if (!file) return 0;
  try {
    return fs.statSync(file).size;
  } catch {
    return 0;
  }
}

// Прочитанный хвост транскрипта: полные строки с байтового смещения и новое
// смещение за последней ПОЛНОЙ строкой. Хвост без перевода строки ещё
// дописывается и будет прочитан в следующий раз.
function tailFrom(file, from) {
  let start = from;
  let fd;
  try {
    const size = fs.statSync(file).size;
    // Файл КОРОЧЕ прежнего смещения — это другой транскрипт (сессия начата
    // заново, файл подменён): читаем с начала, иначе смещение никогда уже не
    // сойдётся и токены до конца сессии останутся нулевыми.
    if (size < start) start = 0;
    if (size <= start) return { text: '', offset: start };
    fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    const text = buf.toString('utf8');
    const lastNl = text.lastIndexOf('\n');
    if (lastNl < 0) return { text: '', offset: start };
    const complete = text.slice(0, lastNl + 1);
    return { text: complete, offset: start + Buffer.byteLength(complete, 'utf8') };
  } catch {
    return { text: '', offset: from };
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

// Сумма usage по записям ответа модели. Один ответ лежит в транскрипте
// несколькими записями с одним message.id (по записи на блок содержимого) и одним
// и тем же usage — считается один раз, по последней записи.
function sumUsage(text) {
  const byId = new Map();
  let anon = 0;
  eachJsonl(text, (entry) => {
    if (entry.type !== 'assistant') return;
    const message = entry.message;
    if (!message || !message.usage || typeof message.usage !== 'object') return;
    const id = typeof message.id === 'string' && message.id ? message.id : `anon-${anon += 1}`;
    byId.set(id, message.usage);
  });
  const usage = {
    input: 0, output: 0, cache_read: 0, cache_create: 0, messages: 0,
  };
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
  for (const u of byId.values()) {
    usage.input += num(u.input_tokens);
    usage.output += num(u.output_tokens);
    usage.cache_read += num(u.cache_read_input_tokens);
    usage.cache_create += num(u.cache_creation_input_tokens);
    usage.messages += 1;
  }
  return usage;
}

// Факт `tokens` для хода: сумма usage с байтового смещения и смещение за
// последней полной строкой — его хранит состояние и передаёт в следующий раз.
export function turnUsage(transcript, from = 0) {
  if (!transcript) return { usage: sumUsage(''), offset: from };
  const { text, offset } = tailFrom(transcript, from);
  return { usage: sumUsage(text), offset };
}
