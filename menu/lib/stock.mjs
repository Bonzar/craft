// Наличие как арифметика — и продуктов, и порций. Закупка «куплено» приносит
// продукты, готовка «сделано» их тратит и рождает порции, приём «съеден»
// порции съедает. Долю приёма задаёт коэффициент едока.
//
// Обычно количество меняется вместе со статусом, одной операцией, и считать
// нечего. Это догонялка для случая, когда статусы проставлены, а цифры отстали.
//
// Что уже учтено, помнит сама запись — галочка `sys_Counted`. Поэтому ни
// порядок, ни время, ни дата значения не имеют: движение проводится ровно один
// раз, а прогон можно повторять хоть подряд. Галочку ставит только код: снять
// её значило бы сказать «не проводили», и второе списание пошло бы теми
// числами, что в записи стоят сейчас, а не теми, какими списывали.

import { convert, pickProduct } from "./cards.mjs";

const round = (x) => Math.round(x * 100) / 100;

/** Движение продукта: приход из закупки, расход из готовки. */
export function movements(ix) {
  const moves = [];
  const unknown = [];

  for (const purchase of ix.purchases) {
    const product = ix.productById.get(purchase.productId);
    if (purchase.status !== "куплено" || purchase.counted) continue;
    if (!product || purchase.qty === null) continue;
    const { qty } = convert({ measure: purchase.unit, qty: purchase.qty }, product, ix.measures);
    if (qty === null) unknown.push({ product, measure: purchase.unit, what: purchase.name });
    else moves.push({ record: purchase, kind: "purchases", productId: product.id, qty });
  }

  for (const cook of ix.cooks) {
    const recipe = ix.recipeById.get(cook.recipeId);
    if (cook.status !== "сделано" || cook.counted) continue;
    if (!recipe || !recipe.basePortions) continue;
    for (const row of recipe.ingredients) {
      if (!row.countable) continue;
      const product = pickProduct(row, ix.productById);
      if (!product) continue;
      const got = convert(row, product, ix.measures);
      if (got.qty === null) {
        unknown.push({ product, measure: got.unknown ?? row.measure, what: cook.name });
        continue;
      }
      const need = (got.qty * cook.portions) / recipe.basePortions;
      moves.push({ record: cook, kind: "cooks", productId: product.id, qty: -need });
    }
  }

  return { moves, unknown };
}

/** Порции: приём «съеден» вычитает долю едока из каждой готовки на тарелке. */
export function portionMoves(ix) {
  const moves = [];
  for (const meal of ix.meals) {
    if (meal.status !== "съеден" || meal.counted) continue;
    const share = ix.eaterById.get(meal.eaterId)?.share ?? 0;
    for (const id of new Set([...meal.hot, ...meal.side, ...meal.extra])) {
      const cook = ix.cookById.get(id);
      if (cook) moves.push({ record: meal, cook, qty: -share });
    }
  }
  return moves;
}

/** Что станет с наличием, если провести все непроведённые движения. */
export function ledger(ix) {
  const { moves, unknown } = movements(ix);
  const by = new Map();
  const records = { cooks: new Set(), purchases: new Set(), meals: new Set() };

  for (const m of moves) {
    records[m.kind].add(m.record.id);
    const product = ix.productById.get(m.productId);
    const acc = by.get(m.productId) ?? { product, plus: 0, minus: 0, from: [] };
    if (m.qty >= 0) acc.plus += m.qty;
    else acc.minus -= m.qty;
    if (!acc.from.includes(m.record.name)) acc.from.push(m.record.name);
    by.set(m.productId, acc);
  }

  const changed = [...by.values()].map((r) => ({
    product: r.product,
    was: r.product.qty ?? 0,
    plus: round(r.plus),
    minus: round(r.minus),
    now: round((r.product.qty ?? 0) + r.plus - r.minus),
    from: r.from,
  }));

  const eaten = new Map();
  for (const m of portionMoves(ix)) {
    records.meals.add(m.record.id);
    const acc = eaten.get(m.cook.id) ?? { cook: m.cook, minus: 0, from: [] };
    acc.minus -= m.qty;
    if (!acc.from.includes(m.record.name)) acc.from.push(m.record.name);
    eaten.set(m.cook.id, acc);
  }
  const portions = [...eaten.values()].map((r) => ({
    cook: r.cook,
    was: r.cook.remaining ?? r.cook.portions,
    minus: round(r.minus),
    now: round((r.cook.remaining ?? r.cook.portions) - r.minus),
    from: r.from,
  }));

  return {
    changed: changed.sort((a, b) => a.product.name.localeCompare(b.product.name, "ru")),
    portions: portions.sort((a, b) => a.cook.name.localeCompare(b.cook.name, "ru")),
    records: {
      cooks: [...records.cooks],
      purchases: [...records.purchases],
      meals: [...records.meals],
    },
    unknown,
  };
}
