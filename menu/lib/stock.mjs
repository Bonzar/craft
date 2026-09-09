// Наличие продуктов как арифметика, а не как память человека: закупка со
// статусом «куплено» прибавляет, готовка со статусом «сделано» вычитает.

import { incoming, need } from "./need.mjs";

const round = (x) => Math.round(x * 100) / 100;

/** Случилось после точки отсчёта: дата не раньше и дело сделано. */
const since = (from, date) => Boolean(date) && (!from || date >= from);

/**
 * Приход и расход по каждому продукту за окно.
 * Окно задаёт человек: у продукта нет поля «на какое число верно наличие»,
 * и без него код не знает, что из записей `Qty` уже видел.
 */
export function ledger(ix, from) {
  const bought = incoming(
    ix,
    ix.purchases.filter((p) => p.status === "куплено" && since(from, p.date)),
  );
  const { rows, unknown } = need(
    ix,
    ix.cooks.filter((c) => c.status === "сделано" && since(from, c.date)),
  );
  const spent = new Map(rows.map((r) => [r.product.id, r.qty]));

  const moved = [...new Set([...bought.keys(), ...spent.keys()])].map((id) => {
    const product = ix.productById.get(id);
    const plus = round(bought.get(id) ?? 0);
    const minus = round(spent.get(id) ?? 0);
    return { product, was: product.qty ?? 0, plus, minus, now: round((product.qty ?? 0) + plus - minus) };
  });

  return { moved: moved.sort((a, b) => a.product.name.localeCompare(b.product.name, "ru")), unknown };
}
