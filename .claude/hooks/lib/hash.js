// Хеши. Bash-версии считали их внешними командами и брали ПЕРВЫЙ токен вывода:
// `sha256sum | cut -d' ' -f1`, а на маке — `shasum -a 256`, потому что основной
// команды там нет. Здесь обе ветки не нужны — важен только результат, и он
// обязан совпадать с прежним посимвольно: на этих хешах стоят метки уступки,
// отметка обкатки плана и маркеры факт-гейта, которые пишут и читают ещё не
// перенесённые хуки.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

export function sha256(text) {
  return createHash('sha256').update(text).digest('hex');
}

// Контрольная сумма формата POSIX cksum: «‹сумма› ‹размер›». Своя реализация
// нужна ровно потому, что значение попадает в файл состояния, который читают
// следующие вызовы, — любая другая сумма обнулила бы дедуп прошлых блокировок.
const CKSUM_TABLE = new Int32Array(256);
for (let i = 0; i < 256; i += 1) {
  let c = i << 24;
  for (let k = 0; k < 8; k += 1) c = (c & 0x80000000) ? ((c << 1) ^ 0x04c11db7) : (c << 1);
  CKSUM_TABLE[i] = c;
}

export function cksum(text) {
  const buf = Buffer.from(text, 'utf8');
  let crc = 0;
  for (const byte of buf) crc = (crc << 8) ^ CKSUM_TABLE[((crc >>> 24) ^ byte) & 0xff];
  let rest = buf.length;
  while (rest > 0) {
    crc = (crc << 8) ^ CKSUM_TABLE[((crc >>> 24) ^ (rest & 0xff)) & 0xff];
    rest = Math.floor(rest / 256);
  }
  return `${(~crc) >>> 0} ${buf.length}`;
}

// Хеш файла. Файла нет или он нечитаем — пустая строка: у bash-версий в этом
// случае выходил пустой хеш, и гейт просто не пропускал показ, а не падал.
export function sha256File(file) {
  try {
    return createHash('sha256').update(readFileSync(file)).digest('hex');
  } catch {
    return '';
  }
}
