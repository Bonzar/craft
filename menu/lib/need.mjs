// Сколько продуктов нужно на готовки и чего для них не хватает.
// Считает по составам рецептов; что купить — решает человек, код только складывает.

import { needFor, convert } from "./cards.mjs";

const round = (x) => Math.round(x * 100) / 100;

/** Вариант ингредиента: сначала тот, что есть дома, иначе первый по порядку. */
function pick(row, ix) {
  const options = row.products.map((p) => ix.productById.get(p.id)).filter(Boolean);
  return options.find((p) => (p.qty ?? 0) > 0) ?? options[0];
}

/**
 * Потребность по продуктам: составы готовок, каждый пересчитанный на её порции.
 * Строка, чья мера не сводится к единице продукта, в сумму не идёт — она
 * возвращается отдельно, чтобы недостающая запись в «Мерах» не потерялась
 * в аккуратном на вид итоге.
 */
export function need(ix, cooks) {
  const rows = new Map();
  const unknown = [];

  for (const cook of cooks) {
    const recipe = ix.recipeById.get(cook.recipeId);
    if (!recipe) continue;
    for (const row of recipe.ingredients) {
      // Из вариантов берётся тот, что есть дома, и только если дома нет ни
      // одного — первый, самый предпочтительный. Иначе список покупок требовал
      // бы ракушек при полке, забитой спиральками.
      const product = pick(row, ix);
      if (!product) continue;
      const got = needFor(row, product, ix.measures, cook.portions, recipe.basePortions);
      if (got.qty === null) {
        unknown.push({ cook, product, measure: got.unknown ?? row.measure, raw: row.raw });
        continue;
      }
      const known = rows.get(product.id) ?? { product, qty: 0, cooks: [] };
      known.qty += got.qty;
      known.cooks.push(cook.name);
      rows.set(product.id, known);
    }
  }

  return { rows: [...rows.values()].map((r) => ({ ...r, qty: round(r.qty) })), unknown };
}

/** Что уже заказано, но ещё не дома: закупки в статусе «план», в единице продукта. */
export function incoming(ix, purchases) {
  const by = new Map();
  for (const purchase of purchases) {
    const product = ix.productById.get(purchase.productId);
    if (!product || purchase.qty === null) continue;
    const { qty } = convert({ measure: purchase.unit, qty: purchase.qty }, product, ix.measures);
    if (qty === null) continue;
    by.set(product.id, (by.get(product.id) ?? 0) + qty);
  }
  return by;
}

/** Надо минус дома минус заказанное. Ноль и меньше — докупать нечего. */
export function shortfall(rows, ordered) {
  return rows
    .map((r) => ({
      ...r,
      have: r.product.qty ?? 0,
      ordered: round(ordered.get(r.product.id) ?? 0),
    }))
    .map((r) => ({ ...r, short: round(r.qty - r.have - r.ordered) }))
    .filter((r) => r.short > 0);
}
