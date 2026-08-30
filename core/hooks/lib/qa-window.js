// Окно последних записей: файл-накопитель, из которого старое вытесняется, а
// последние N записей остаются входом для классификатора план-гейта. Так живут
// два файла — окно разрешений (пары «вопрос + выбранный ответ» и прямые
// реплики-указания Влада) и накопитель одобренных планов.
//
// Правило вытеснения общее и живёт здесь: в окно разрешений пишут два хука, и
// разъехавшиеся копии дали бы файл, половина записей которого вытесняется по
// одному правилу, половина по другому.
//
// Запись начинается своей строкой-разделителем; всё до первой такой строки —
// хвост прошлого окна и вытесняется первым.
import fs from 'node:fs';

const QA_MARK = '## Запись';

function matcher(mark) {
  return typeof mark === 'string' ? (line) => line.startsWith(mark) : (line) => mark.test(line);
}

// Оставить последние `keep` записей текста. Строки до первой записи считаются
// нулевой — она уходит, как только записей набирается больше лимита.
export function lastRecords(text, keep = 5, mark = QA_MARK) {
  const isMark = matcher(mark);
  const lines = text.split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  let total = 0;
  const marks = lines.map((line) => {
    if (isMark(line)) total += 1;
    return total;
  });
  const kept = lines.filter((_, i) => marks[i] > total - keep);
  return kept.length ? `${kept.join('\n')}\n` : '';
}

// Дописать запись в накопитель и обрезать его до последних `keep`. Файла нет —
// запись становится первой; не записалось — молчим, разрешение просто не
// запомнится и гейт спросит заново.
export function appendRecord(file, record, keep = 5, mark = QA_MARK) {
  if (!file) return;
  let current = '';
  try {
    current = fs.readFileSync(file, 'utf8');
  } catch { /* накопителя ещё нет */ }
  try {
    fs.writeFileSync(file, lastRecords(current + record, keep, mark));
  } catch { /* не записалось — запись не запомнится */ }
}
