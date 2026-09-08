// Правила недели из алгоритма «Собрать меню на неделю».
// Чистые функции: на вход модель, на выход находки. Ни сети, ни записи.

import { indexModel, mealPoint, slotIndex } from "./model.mjs";

const WHEN_TO_SLOT = { утро: "завтрак", день: "обед", вечер: "ужин" };
const ddmm = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}` : "?");
const plusDays = (iso, days) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
const dayGap = (a, b) =>
  Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);

const finding = (collection, id, message) => ({ collection, id, message });

/** Приёмы, которые действительно едят: отменённые расход не создают. */
const eaten = (model) => model.meals.filter((m) => m.status !== "отменён");

/** Готовка -> приёмы, в которых она съедается, в любой роли. */
function mealsByCook(model) {
  const map = new Map();
  for (const meal of eaten(model)) {
    for (const id of [...meal.hot, ...meal.side, ...meal.extra]) {
      if (!map.has(id)) map.set(id, []);
      map.get(id).push(meal);
    }
  }
  return map;
}

export function checkCooks(model) {
  const ix = indexModel(model);
  const byCook = mealsByCook(model);
  const out = [];

  for (const cook of model.cooks) {
    const all = byCook.get(cook.id) ?? [];

    if (!cook.date) {
      out.push(finding("cooks", cook.id, "готовка вне этой недели, даты нет"));
    } else if (cook.when) {
      // Готовить может только тот, кто в этот слот дома.
      const slot = WHEN_TO_SLOT[cook.when];
      const atHome = model.meals.some(
        (m) =>
          m.date === cook.date &&
          m.slot === slot &&
          m.where === "дома" &&
          ix.eaterById.get(m.eaterId)?.cooks === true,
      );
      if (!atHome) out.push(finding("cooks", cook.id, "в этот слот я не дома"));
    }

    // Расход считается от последнего факта остатка, а до него — от выхода.
    const hasFact = cook.left !== null;
    const since = hasFact ? mealPoint(cook.leftOn, cook.leftAfter) : null;
    const counted = hasFact ? all.filter((m) => mealPoint(m.date, m.slot) > since) : all;
    const cap = hasFact ? cook.left : cook.yield;
    const spent = counted.reduce((sum, m) => sum + m.take, 0);
    if (spent > cap + 1e-9) {
      const short = Math.round((spent - cap) * 100) / 100;
      out.push(
        finding("cooks", cook.id, `расход ${spent} из ${cap} порций — не хватает ${short}`),
      );
    }

    if (all.length === 0 && cook.storage !== "морозилка") {
      out.push(finding("cooks", cook.id, "никто не ест"));
    }

    const keepDays = ix.recipeById.get(cook.recipeId)?.keepDays;
    if (cook.date && cook.storage === "холодильник" && typeof keepDays === "number") {
      const last = plusDays(cook.date, keepDays);
      const late = all.find((m) => m.date > last);
      if (late) {
        out.push(finding("cooks", cook.id, `срок до ${ddmm(last)}, приём ${late.name}`));
      }
    }
  }
  return out;
}

export function checkMeals(model) {
  const ix = indexModel(model);
  const out = [];
  const recipeOf = (cookId) => ix.recipeById.get(ix.cookById.get(cookId)?.recipeId);

  for (const meal of model.meals) {
    if (meal.where === "вне") continue;

    if (meal.hot.length === 0) {
      out.push(finding("meals", meal.id, "приём не закрыт: горячего нет"));
    } else if (meal.slot === "ужин") {
      const hasSalad = meal.extra.some((id) => recipeOf(id)?.kind === "салат");
      if (!hasSalad) out.push(finding("meals", meal.id, "на ужин нет салата"));
    }

    if (meal.where === "с собой") {
      for (const id of [...meal.hot, ...meal.side, ...meal.extra]) {
        const cook = ix.cookById.get(id);
        if (cook?.storage === "морозилка") {
          out.push(finding("meals", meal.id, `с собой из морозилки: ${cook.name}`));
        }
      }
    }

    for (const id of [...meal.hot, ...meal.side, ...meal.extra]) {
      for (const productId of recipeOf(id)?.productIds ?? []) {
        const product = ix.productById.get(productId);
        if (product?.bestBefore && meal.date && product.bestBefore < meal.date) {
          out.push(
            finding(
              "meals",
              meal.id,
              `${product.name}: годен до ${ddmm(product.bestBefore)}, приём ${ddmm(meal.date)}`,
            ),
          );
        }
      }
    }
  }

  // Повтор блюда внутри дня у одного едока — обязательство перед Олей,
  // у повара то же самое допустимо только с его согласия.
  const perDay = new Map();
  for (const meal of eaten(model)) {
    const key = `${meal.date}|${meal.eaterId}`;
    if (!perDay.has(key)) perDay.set(key, []);
    perDay.get(key).push(meal);
  }
  for (const meals of perDay.values()) {
    const eater = ix.eaterById.get(meals[0].eaterId)?.name ?? "?";
    for (const [role, label] of [
      ["hot", "горячего"],
      ["side", "гарнира"],
    ]) {
      const seen = new Map();
      for (const meal of meals) {
        for (const id of meal[role]) {
          const recipe = recipeOf(id);
          if (!recipe) continue;
          if (!seen.has(recipe.id)) seen.set(recipe.id, []);
          seen.get(recipe.id).push(meal);
        }
      }
      for (const [recipeId, hits] of seen) {
        if (hits.length < 2) continue;
        const name = ix.recipeById.get(recipeId).name;
        for (const meal of hits) {
          out.push(finding("meals", meal.id, `повтор ${label} у ${eater}: ${name}`));
        }
      }
    }
  }

  // Одно блюдо не дольше двух дней подряд.
  const daysOf = new Map();
  for (const meal of eaten(model)) {
    for (const id of meal.hot) {
      const recipe = recipeOf(id);
      if (!recipe) continue;
      if (!daysOf.has(recipe.id)) daysOf.set(recipe.id, new Set());
      daysOf.get(recipe.id).add(meal.date);
    }
  }
  for (const [recipeId, dates] of daysOf) {
    const sorted = [...dates].sort();
    let run = [sorted[0]];
    let best = [sorted[0]];
    for (const date of sorted.slice(1)) {
      run = dayGap(run[run.length - 1], date) === 1 ? [...run, date] : [date];
      if (run.length > best.length) best = [...run];
    }
    if (best.length <= 2) continue;
    const name = ix.recipeById.get(recipeId).name;
    const span = `${ddmm(best[0])}—${ddmm(best[best.length - 1])}`;
    for (const meal of eaten(model)) {
      const hit = meal.hot.some((id) => recipeOf(id)?.id === recipeId);
      if (hit && best.includes(meal.date)) {
        out.push(finding("meals", meal.id, `${name} подряд ${best.length} дня: ${span}`));
      }
    }
  }
  return out;
}

export function checkPurchases(model) {
  const ix = indexModel(model);
  const out = [];
  for (const purchase of model.purchases) {
    const product = ix.productById.get(purchase.productId);
    if (product?.unit && purchase.unit && purchase.unit !== product.unit) {
      out.push(
        finding(
          "purchases",
          purchase.id,
          `единица не та же, что у продукта: закупка в ${purchase.unit}, ${product.name} в ${product.unit}`,
        ),
      );
    }

    const dates = purchase.forIds
      .map((id) => ix.cookById.get(id)?.date)
      .filter(Boolean)
      .sort();
    if (purchase.forIds.length === 0) {
      if (purchase.status !== "отменено") {
        out.push(finding("purchases", purchase.id, "не привязан ни к одной готовке"));
      }
    } else if (dates.length > 0 && purchase.date > dates[0]) {
      out.push(
        finding(
          "purchases",
          purchase.id,
          `нужен к ${ddmm(dates[0])}, заход ${ddmm(purchase.date)}`,
        ),
      );
    }
  }
  return out;
}

/** Все правила разом. Возвращает Map "коллекция:id" -> отсортированный текст. */
export function runRules(model) {
  const all = [...checkCooks(model), ...checkMeals(model), ...checkPurchases(model)];
  const byRecord = new Map();
  for (const f of all) {
    const key = `${f.collection}:${f.id}`;
    if (!byRecord.has(key)) byRecord.set(key, new Set());
    byRecord.get(key).add(f.message);
  }
  return new Map(
    [...byRecord].map(([key, messages]) => [key, [...messages].sort().join("; ")]),
  );
}

export { slotIndex };
