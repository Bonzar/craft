// Правила недели из алгоритма «Собрать меню на неделю».
// Чистые функции: на вход модель, на выход находки. Ни сети, ни записи.

import { convertible } from "./cards.mjs";
import { indexModel, slotIndex } from "./model.mjs";

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

/** Правило про повтор горячего сравнивает обед с ужином, завтрак не в счёт. */
const HOT_SLOTS = new Set(["обед", "ужин"]);

/** Даты разбиваются на серии идущих подряд дней; каждая серия — отдельно. */
export function consecutiveRuns(dates) {
  const sorted = [...new Set(dates)].sort();
  if (sorted.length === 0) return [];
  const runs = [[sorted[0]]];
  for (const date of sorted.slice(1)) {
    const current = runs[runs.length - 1];
    if (dayGap(current[current.length - 1], date) === 1) current.push(date);
    else runs.push([date]);
  }
  return runs;
}

/**
 * Приёмы, которые действительно едят. Отменённый не состоялся, пропущенный
 * состоялся без еды — расхода не создаёт ни тот, ни другой, и закрывать их
 * горячим и салатом уже незачем.
 */
const happened = (meal) => meal.status !== "отменён" && meal.status !== "пропущен";
const eaten = (model) => model.meals.filter(happened);

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

    // Списали продукты, а потом готовку отменили или вернули в план. Галочку
    // код не снимает — вернуть точно то, что списал, он уже не сможет; значит
    // сказать вслух, чтобы наличие поправил тот, кто видит холодильник.
    if (cook.counted && cook.status !== "сделано") {
      out.push(finding("cooks", cook.id, "продукты списаны, а готовка уже не «сделано» — проверь наличие"));
    }

    // Отменённая готовка не состоялась: ни сроков, ни расхода, ни «никто не
    // ест». Спросить с неё можно одно — что её никто уже не ждёт в тарелке.
    if (cook.status === "отменено") {
      for (const meal of all) {
        out.push(finding("cooks", cook.id, `готовка отменена, а её ест ${meal.name}`));
      }
      continue;
    }

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

    // Съеденное из остатка уже вычтено, поэтому спрашивать надо только про
    // будущее: хватит ли того, что осталось, на приёмы, которые ещё в плане.
    // Сколько съедает приём — коэффициент его едока, отдельного поля нет.
    const left = cook.remaining ?? cook.portions;
    const ahead = all
      .filter((m) => m.status === "план")
      .reduce((sum, m) => sum + (ix.eaterById.get(m.eaterId)?.share ?? 0), 0);
    if (ahead > left + 1e-9) {
      const short = Math.round((ahead - left) * 100) / 100;
      out.push(
        finding("cooks", cook.id, `впереди ${ahead} порций из ${left} — не хватает ${short}`),
      );
    }

    // Заготовка кормит не тарелку, а следующую готовку: разморозка, нарезка
    // лука впрок. Спрашивать с неё «кто ест» бессмысленно — её и не едят.
    const recipe = ix.recipeById.get(cook.recipeId);
    const feedsPlate = recipe?.kind !== "заготовка";
    if (all.length === 0 && cook.storage !== "морозилка" && feedsPlate) {
      out.push(finding("cooks", cook.id, "никто не ест"));
    }

    const keepDays = recipe?.keepDays;
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
    if (meal.where === "вне" || !happened(meal)) continue;

    if (meal.hot.length === 0) {
      out.push(finding("meals", meal.id, "приём не закрыт: горячего нет"));
    } else if (meal.slot === "ужин") {
      const hasSalad = meal.extra.some((id) => recipeOf(id)?.kind === "салат");
      if (!hasSalad) out.push(finding("meals", meal.id, "на ужин нет салата"));
    }

    // Гарнир спрашивает само блюдо: болоньезе несёт макароны в составе, и
    // отдельная паста к нему — не сытнее, а тот же продукт, посчитанный дважды.
    const hots = meal.hot.map(recipeOf).filter(Boolean);
    const wants = hots.filter((r) => r.needsSide);
    if (hots.length > 0 && wants.length === 0 && meal.side.length > 0) {
      out.push(finding("meals", meal.id, `гарнир лишний: ${hots[0].name} его не просит`));
    }
    if (wants.length > 0 && meal.side.length === 0) {
      out.push(finding("meals", meal.id, `${wants[0].name} без гарнира`));
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
  // у повара то же самое допустимо только с его согласия. Горячее правило
  // сравнивает обед с ужином: завтрак идёт своей ротацией и в счёт не идёт.
  // Гарнир не повторяется за день целиком — на завтрак его и не бывает.
  const perDay = new Map();
  for (const meal of eaten(model)) {
    const key = `${meal.date}|${meal.eaterId}`;
    if (!perDay.has(key)) perDay.set(key, []);
    perDay.get(key).push(meal);
  }
  for (const meals of perDay.values()) {
    const eater = ix.eaterById.get(meals[0].eaterId)?.name ?? "?";
    for (const [role, label, slots] of [
      ["hot", "горячего", HOT_SLOTS],
      ["side", "гарнира", null],
    ]) {
      const seen = new Map();
      for (const meal of meals) {
        if (slots && !slots.has(meal.slot)) continue;
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
    const name = ix.recipeById.get(recipeId).name;
    // Серий может быть несколько: 5—7 и 9—11 нарушают правило обе, и молчать
    // про вторую только потому, что первая длиннее, — потерять находку.
    for (const run of consecutiveRuns([...dates])) {
      if (run.length <= 2) continue;
      const span = `${ddmm(run[0])}—${ddmm(run[run.length - 1])}`;
      for (const meal of eaten(model)) {
        const hit = meal.hot.some((id) => recipeOf(id)?.id === recipeId);
        if (hit && run.includes(meal.date)) {
          out.push(finding("meals", meal.id, `${name} подряд ${run.length} дня: ${span}`));
        }
      }
    }
  }
  return out;
}

export function checkPurchases(model) {
  const ix = indexModel(model);
  const out = [];
  for (const purchase of model.purchases) {
    if (purchase.counted && purchase.status !== "куплено") {
      out.push(finding("purchases", purchase.id, "приход учтён, а закупка уже не «куплено» — проверь наличие"));
    }

    // Отменённая закупка не состоится: ни единицы у неё спрашивать, ни срока.
    if (purchase.status === "отменено") continue;

    // Магазин меряет упаковками и штуками, продукт — своей единицей. Сводит их
    // коллекция «Меры», а не одинаковая надпись: требовать совпадения значило бы
    // либо врать в закупке, либо переписывать единицу продукта под каждый чек.
    const product = ix.productById.get(purchase.productId);
    // Без продукта закупка не складывается с наличием и молча выпадает
    // из списка покупок — сойдя при этом за проверенную.
    if (!product) out.push(finding("purchases", purchase.id, "не указан продукт"));
    if (product?.unit && purchase.unit && !convertible(purchase.unit, product, model.measures ?? [])) {
      out.push(
        finding(
          "purchases",
          purchase.id,
          `закупка в ${purchase.unit}, ${product.name} в ${product.unit} — в «Мерах» нет заполненной строки «${purchase.unit} → ${product.unit}»`,
        ),
      );
    }

    // Ноль в количестве — не «купил ноль», а «не пересчитал»: магазинное число
    // осталось в заметке, а в единице продукта его никто не мерил.
    if (!purchase.qty) {
      out.push(finding("purchases", purchase.id, `не проставлено количество в ${purchase.unit ?? "единице продукта"}`));
    }

    const dates = purchase.forIds
      .map((id) => ix.cookById.get(id)?.date)
      .filter(Boolean)
      .sort();
    if (purchase.forIds.length === 0) {
      out.push(finding("purchases", purchase.id, "не привязан ни к одной готовке"));
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
