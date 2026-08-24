// Окно разрешений план-гейта: последние 5 записей, которыми Влад что-то
// разрешил, — пары «вопрос + выбранный ответ» и его прямые реплики-указания.
// Пишут в него два хука, читает классификатор гейта, поэтому и форма записи, и
// правило вытеснения живут здесь: разъехавшиеся копии дали бы окно, в котором
// половина записей вытесняется по одному правилу, половина по другому.
//
// Запись начинается строкой «## Запись»; всё до первой такой строки — хвост
// прошлого окна и вытесняется первым.
import fs from 'node:fs';

// Оставить последние `keep` записей текста. Строки до первой записи считаются
// нулевой — она уходит, как только записей набирается больше лимита.
export function lastRecords(text, keep = 5) {
  const lines = text.split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  let total = 0;
  const marks = lines.map((line) => {
    if (line.startsWith('## Запись')) total += 1;
    return total;
  });
  const kept = lines.filter((_, i) => marks[i] > total - keep);
  return kept.length ? `${kept.join('\n')}\n` : '';
}

// Дописать запись в окно и обрезать его до последних `keep`. Окна нет — запись
// становится первой; не записалось — молчим, разрешение просто не запомнится.
export function appendRecord(file, record, keep = 5) {
  if (!file) return;
  let current = '';
  try {
    current = fs.readFileSync(file, 'utf8');
  } catch { /* окна ещё нет */ }
  try {
    fs.writeFileSync(file, lastRecords(current + record, keep));
  } catch { /* не записалось — разрешение не запомнится, гейт спросит заново */ }
}
