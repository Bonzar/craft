// Мелочи, общие свёрткам сводки.
//
// ms(строка) → миллисекунды из ISO-времени записи (NaN, если времени нет).
// byTurn(записи, pick) → Map «номер хода → последнее непустое значение pick»:
//   у одного хода записей бывает несколько, и решает последняя.
export const ms = Date.parse;

export function byTurn(records, pick) {
  const out = new Map();
  for (const r of records) {
    const value = pick(r);
    if (value !== undefined) out.set(r.turn, value);
  }
  return out;
}
