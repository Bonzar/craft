// Нормализация ответов connect-API в плоскую модель недели.
// Дальше правила работают только с ней и ничего не знают про HTTP.

import { parseIngredients } from "./cards.mjs";

const relations = (prop) => (prop?.relations ?? []).map((r) => r.blockId);

/**
 * Таблица ингредиентов в теле рецепта — первая таблица за заголовком
 * «Ингредиенты». Заголовок ищется явно: ниже по телу бывают другие таблицы,
 * и без него состав однажды соберётся из чужой.
 */
function ingredientTable(content = []) {
  const blocks = content ?? [];
  const start = blocks.findIndex((b) => /^#+\s*Ингредиенты/i.test(b.markdown ?? ""));
  const table = blocks.slice(start === -1 ? 0 : start + 1).find((b) => b.type === "table");
  return table?.markdown ?? "";
}
const title = (item) => item.name ?? item.product ?? "";
const num = (v) => (typeof v === "number" ? v : null);

export const SLOTS = ["завтрак", "обед", "ужин"];
export const slotIndex = (slot) => SLOTS.indexOf(slot);

/** Точка приёма как сравнимый ключ: сначала дата, потом слот дня. */
export const mealPoint = (date, slot) => `${date ?? ""}#${slotIndex(slot)}`;

export function buildModel(raw) {
  const eaters = (raw.eaters ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    share: num(i.properties?.share) ?? 0,
    cooks: i.properties?.cooks === true,
  }));

  const recipes = (raw.recipes ?? []).map((i) => {
    // Состав живёт в теле рецепта — таблицей за заголовком «Ингредиенты», как
    // Влад его и ведёт. Отдельного поля-связи под него нет: оно дублировало бы
    // ту же таблицу и разъезжалось бы с ней.
    const { basePortions, rows } = parseIngredients(ingredientTable(i.content));
    return {
      id: i.id,
      name: title(i),
      kind: i.properties?.kind ?? null,
      keepDays: num(i.properties?.keepdays),
      freezable: i.properties?.freezable === true,
      needsSide: i.properties?.needsside === true,
      basePortions,
      ingredients: rows,
      productIds: rows.flatMap((r) => r.products.map((p) => p.id)),
    };
  });

  const products = (raw.products ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    unit: i.properties?.unit ?? null,
    qty: num(i.properties?.qty),
    bestBefore: i.properties?.bestbefore ?? null,
  }));

  const cooks = (raw.cooks ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    date: i.properties?.date ?? null,
    when: i.properties?.when ?? null,
    recipeId: relations(i.properties?.recipe)[0] ?? null,
    portions: num(i.properties?.portions) ?? 0,
    storage: i.properties?.storage ?? null,
    remaining: num(i.properties?.remaining),
    remainingOn: i.properties?.remainingon ?? null,
    status: i.properties?.status ?? null,
  }));

  const meals = (raw.meals ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    date: i.properties?.date ?? null,
    slot: i.properties?.slot ?? null,
    eaterId: relations(i.properties?.eater)[0] ?? null,
    where: i.properties?.where ?? null,
    hot: relations(i.properties?.hot),
    side: relations(i.properties?.side),
    extra: relations(i.properties?.extra),
    status: i.properties?.status ?? null,
  }));

  // Мост кухонной меры к единице продукта: строка читается как равенство
  // MeasureQty мер = ProductQty единиц. Единицу берёт у продукта по связи.
  const measures = (raw.measures ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    productId: relations(i.properties?.product)[0] ?? null,
    measure: i.properties?.measure ?? null,
    measureQty: num(i.properties?.measureqty),
    productQty: num(i.properties?.productqty),
  }));

  // Связь «закупка — готовка» умеет разъезжаться: половина, что живёт у закупки,
  // однажды перестала читаться, а половина у готовки осталась цела. Поэтому
  // берутся обе и складываются — потерять привязку дороже, чем прочитать дважды.
  const cooksOfPurchase = new Map();
  for (const cook of raw.cooks ?? []) {
    for (const id of relations(cook.properties?.purchases)) {
      if (!cooksOfPurchase.has(id)) cooksOfPurchase.set(id, []);
      cooksOfPurchase.get(id).push(cook.id);
    }
  }

  const purchases = (raw.purchases ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    date: i.properties?.date ?? null,
    productId: relations(i.properties?.product)[0] ?? null,
    qty: num(i.properties?.qty),
    unit: i.properties?.unit ?? null,
    forIds: [...new Set([...relations(i.properties?.for), ...(cooksOfPurchase.get(i.id) ?? [])])],
    status: i.properties?.status ?? null,
  }));

  return { eaters, recipes, products, cooks, meals, purchases, measures };
}

export function indexModel(model) {
  const byId = (list) => new Map(list.map((x) => [x.id, x]));
  return {
    ...model,
    eaterById: byId(model.eaters),
    recipeById: byId(model.recipes),
    productById: byId(model.products),
    cookById: byId(model.cooks),
  };
}
