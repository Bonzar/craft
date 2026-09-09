// Наличие продуктов как арифметика, а не как память человека: закупка со
// статусом «куплено» прибавляет, готовка со статусом «сделано» вычитает.
//
// Обычно количество меняется вместе со статусом, одной операцией, и считать
// нечего. Это догонялка для случая, когда статусы проставлены, а цифры отстали.
// Случилось или нет — решает статус, а не дата. Отсечка `QtyOn` у каждого
// продукта своя и нужна затем, чтобы не посчитать одно и то же дважды.

import { convert } from "./cards.mjs";

const round = (x) => Math.round(x * 100) / 100;

/**
 * Все движения по продуктам: приход из закупок, расход из готовок.
 * Дата у движения своя, потому что отсечка у каждого продукта своя.
 */
export function movements(ix) {
  const moves = [];
  const unknown = [];

  for (const purchase of ix.purchases) {
    const product = ix.productById.get(purchase.productId);
    if (purchase.status !== "куплено" || !product || purchase.qty === null) continue;
    const { qty } = convert({ measure: purchase.unit, qty: purchase.qty }, product, ix.measures);
    if (qty === null) unknown.push({ product, measure: purchase.unit, what: purchase.name });
    else moves.push({ productId: product.id, date: purchase.date, qty });
  }

  for (const cook of ix.cooks) {
    const recipe = ix.recipeById.get(cook.recipeId);
    if (cook.status !== "сделано" || !recipe || !recipe.basePortions) continue;
    for (const row of recipe.ingredients) {
      if (!row.countable) continue;
      const product = ix.productById.get(row.products[0]?.id);
      if (!product) continue;
      const got = convert(row, product, ix.measures);
      if (got.qty === null) {
        unknown.push({ product, measure: got.unknown ?? row.measure ?? "без меры", what: cook.name });
        continue;
      }
      const need = (got.qty * cook.portions) / recipe.basePortions;
      moves.push({ productId: product.id, date: cook.date, qty: -need });
    }
  }

  return { moves, unknown };
}

/**
 * Что стало с наличием, если досчитать движения после отсечки продукта
 * и по день `through` включительно. Продукты без движений не возвращаются.
 */
export function ledger(ix, through) {
  const { moves, unknown } = movements(ix);
  const by = new Map();
  for (const m of moves) {
    if (!m.date || m.date > through) continue;
    const product = ix.productById.get(m.productId);
    if (product.qtyOn && m.date <= product.qtyOn) continue;
    const acc = by.get(m.productId) ?? { product, plus: 0, minus: 0 };
    if (m.qty >= 0) acc.plus += m.qty;
    else acc.minus -= m.qty;
    by.set(m.productId, acc);
  }

  const changed = [...by.values()].map((r) => ({
    product: r.product,
    was: r.product.qty ?? 0,
    from: r.product.qtyOn,
    plus: round(r.plus),
    minus: round(r.minus),
    now: round((r.product.qty ?? 0) + r.plus - r.minus),
  }));

  return {
    changed: changed.sort((a, b) => a.product.name.localeCompare(b.product.name, "ru")),
    unknown,
  };
}
