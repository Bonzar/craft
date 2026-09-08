// Нормализация ответов connect-API в плоскую модель недели.
// Дальше правила работают только с ней и ничего не знают про HTTP.

const relations = (prop) => (prop?.relations ?? []).map((r) => r.blockId);
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

  const recipes = (raw.recipes ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    kind: i.properties?.kind ?? null,
    keepDays: num(i.properties?.keepdays),
    freezable: i.properties?.freezable === true,
    needsSide: i.properties?.needsside === true,
    productIds: relations(i.properties?.products),
  }));

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
    yield: num(i.properties?.yield) ?? 0,
    storage: i.properties?.storage ?? null,
    left: num(i.properties?.left),
    leftOn: i.properties?.lefton ?? null,
    leftAfter: i.properties?.leftafter ?? null,
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
    take: num(i.properties?.take) ?? 0,
    status: i.properties?.status ?? null,
  }));

  const purchases = (raw.purchases ?? []).map((i) => ({
    id: i.id,
    name: title(i),
    date: i.properties?.date ?? null,
    productId: relations(i.properties?.product)[0] ?? null,
    unit: i.properties?.unit ?? null,
    forIds: relations(i.properties?.for),
    status: i.properties?.status ?? null,
  }));

  return { eaters, recipes, products, cooks, meals, purchases };
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
