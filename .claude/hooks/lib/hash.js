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

// Хеш файла. Файла нет или он нечитаем — пустая строка: у bash-версий в этом
// случае выходил пустой хеш, и гейт просто не пропускал показ, а не падал.
export function sha256File(file) {
  try {
    return createHash('sha256').update(readFileSync(file)).digest('hex');
  } catch {
    return '';
  }
}
