// Правила недели держат ровно те находки, которые Влад ловил руками на неделе
// 5–11 сентября: перерасход, повтор блюда, просроченный продукт, незакрытый
// приём. Фикстура — маленькая неделя, где у каждой находки свой повод, чтобы
// падение показывало, какое правило сломалось, а не «что-то в модели».
//
// Расширение .mjs: в каталоге тестов нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildModel, mealPoint } from "../../menu/lib/model.mjs";
import { checkCooks, checkMeals, checkPurchases, runRules } from "../../menu/lib/rules.mjs";
import { isAfter, parseArgs, parseFrom } from "../../menu/recheck.mjs";

const rel = (...ids) => ({ relations: ids.map((blockId) => ({ blockId })) });
const item = (id, name, properties) => ({ id, name, properties });

/** Минимальная неделя: двое едоков, три рецепта, по одной готовке на блюдо. */
function fixture(over = {}) {
  const raw = {
    eaters: [
      item("e-vlad", "Влад", { share: 1.25, cooks: true }),
      item("e-olya", "Оля", { share: 1, cooks: false }),
    ],
    recipes: [
      item("r-plov", "Плов", { kind: "горячее", keepdays: 3, products: rel("p-rice") }),
      item("r-salad", "Салат", { kind: "салат", keepdays: 0, products: rel("p-lavash") }),
      item("r-puree", "Пюре", { kind: "гарнир", keepdays: 3 }),
    ],
    products: [
      item("p-rice", "Рис", { unit: "г", qty: 500 }),
      item("p-lavash", "Лаваш", { unit: "уп", bestbefore: "2026-09-07" }),
    ],
    cooks: [
      item("c-plov", "Плов, сб", {
        date: "2026-09-05", when: "вечер", recipe: rel("r-plov"),
        yield: 4.5, storage: "холодильник", status: "сделано",
      }),
      item("c-salad", "Салат, сб", {
        date: "2026-09-05", when: "вечер", recipe: rel("r-salad"),
        yield: 2.25, storage: "холодильник", status: "сделано",
      }),
      item("c-puree", "Пюре, сб", {
        date: "2026-09-05", when: "вечер", recipe: rel("r-puree"),
        yield: 8, storage: "холодильник", status: "сделано",
      }),
    ],
    meals: [
      item("m-sb-d-v", "Сб · ужин · Влад", {
        date: "2026-09-05", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), side: rel("c-puree"), extra: rel("c-salad"),
        take: 1.25, status: "съеден",
      }),
      item("m-sb-d-o", "Сб · ужин · Оля", {
        date: "2026-09-05", slot: "ужин", eater: rel("e-olya"), where: "дома",
        hot: rel("c-plov"), side: rel("c-puree"), extra: rel("c-salad"),
        take: 1, status: "съеден",
      }),
    ],
    purchases: [
      item("b-rice", "Рис", {
        date: "2026-09-05", product: rel("p-rice"), qty: 500, unit: "г",
        for: rel("c-plov"), status: "куплено",
      }),
    ],
  };
  for (const [key, value] of Object.entries(over)) raw[key] = value(raw[key]);
  return buildModel(raw);
}

const messages = (findings, id) => findings.filter((f) => f.id === id).map((f) => f.message);

test("чистая неделя не даёт находок", () => {
  const model = fixture();
  assert.deepEqual(runRules(model).size, 0);
});

test("перерасход считается от выхода готовки", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-o-v", "Вс · обед · Влад", {
        date: "2026-09-06", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), take: 1.25, status: "план",
      }),
      item("m-vs-u-v", "Вс · ужин · Влад", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), extra: rel("c-salad"), take: 1.25, status: "план",
      }),
    ],
  });
  assert.deepEqual(messages(checkCooks(model), "c-plov"), [
    "расход 4.75 из 4.5 порций — не хватает 0.25",
  ]);
});

test("факт остатка сдвигает точку отсчёта: приёмы до неё не считаются", () => {
  const model = fixture({
    cooks: (cooks) =>
      cooks.map((c) =>
        c.id === "c-plov"
          ? item(c.id, c.name, { ...c.properties, left: 2, lefton: "2026-09-05", leftafter: "ужин" })
          : c,
      ),
  });
  assert.deepEqual(messages(checkCooks(model), "c-plov"), []);
});

test("отменённый приём расхода не создаёт", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-u-o", "Вс · ужин · Оля", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-olya"), where: "вне",
        take: 0, status: "отменён",
      }),
    ],
  });
  assert.deepEqual(runRules(model).size, 0);
});

test("приём без горячего не закрыт, а ужин без салата подсвечивается отдельно", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-z-v", "Вс · завтрак · Влад", {
        date: "2026-09-06", slot: "завтрак", eater: rel("e-vlad"), where: "дома",
        take: 1.25, status: "пропущен",
      }),
      item("m-vs-u-v", "Вс · ужин · Влад", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-puree"), take: 1.25, status: "план",
      }),
    ],
  });
  const found = checkMeals(model);
  assert.deepEqual(messages(found, "m-vs-z-v"), ["приём не закрыт: горячего нет"]);
  assert.deepEqual(messages(found, "m-vs-u-v"), ["на ужин нет салата"]);
});

test("продукт с истёкшим сроком ловится по составу рецепта", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vt-z-v", "Вт · завтрак · Влад", {
        date: "2026-09-08", slot: "завтрак", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-salad"), take: 1.25, status: "план",
      }),
    ],
  });
  assert.deepEqual(messages(checkMeals(model), "m-vt-z-v"), [
    "Лаваш: годен до 07.09, приём 08.09",
  ]);
});

test("повтор горячего внутри дня считается по каждому едоку отдельно", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-sb-o-v", "Сб · обед · Влад", {
        date: "2026-09-05", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), take: 1.25, status: "съеден",
      }),
    ],
  });
  const found = checkMeals(model);
  assert.deepEqual(messages(found, "m-sb-o-v"), ["повтор горячего у Влад: Плов"]);
  assert.deepEqual(messages(found, "m-sb-d-o"), []);
});

test("три дня подряд одного блюда — находка на каждом приёме серии", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-u-v", "Вс · ужин · Влад", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), extra: rel("c-salad"), take: 1.25, status: "план",
      }),
      item("m-pn-u-v", "Пн · ужин · Влад", {
        date: "2026-09-07", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), extra: rel("c-salad"), take: 1.25, status: "план",
      }),
    ],
  });
  const streak = checkMeals(model).filter((f) => f.message.includes("подряд"));
  assert.equal(streak.length, 4);
  assert.match(streak[0].message, /Плов подряд 3 дня: 05\.09—07\.09/);
});

test("с собой из морозилки нельзя", () => {
  const model = fixture({
    cooks: (cooks) =>
      cooks.map((c) =>
        c.id === "c-plov" ? item(c.id, c.name, { ...c.properties, storage: "морозилка" }) : c,
      ),
    meals: (meals) =>
      meals.map((m) =>
        m.id === "m-sb-d-v"
          ? item(m.id, m.name, { ...m.properties, where: "с собой" })
          : m,
      ),
  });
  assert.ok(messages(checkMeals(model), "m-sb-d-v").includes("с собой из морозилки: Плов, сб"));
});

test("готовка в слот, где повара нет дома", () => {
  const model = fixture({
    meals: (meals) =>
      meals.map((m) =>
        m.id === "m-sb-d-v" ? item(m.id, m.name, { ...m.properties, where: "вне" }) : m,
      ),
  });
  assert.ok(messages(checkCooks(model), "c-plov").includes("в этот слот я не дома"));
});

test("готовку, которую никто не ест, ловим — кроме отправленной в морозилку", () => {
  const model = fixture({
    cooks: (cooks) => [
      ...cooks,
      item("c-zapas", "Плов в морозилку", {
        date: "2026-09-05", when: "вечер", recipe: rel("r-plov"),
        yield: 2, storage: "морозилка", status: "план",
      }),
      item("c-nikto", "Пюре, вс", {
        date: "2026-09-06", when: "вечер", recipe: rel("r-puree"),
        yield: 2, storage: "холодильник", status: "план",
      }),
    ],
  });
  const found = checkCooks(model);
  assert.deepEqual(messages(found, "c-zapas"), []);
  assert.ok(messages(found, "c-nikto").includes("никто не ест"));
});

test("срок хранения считается от даты готовки плюс keepDays рецепта", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-ct-o-v", "Ср · обед · Влад", {
        date: "2026-09-09", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), take: 1.25, status: "план",
      }),
    ],
  });
  assert.ok(
    messages(checkCooks(model), "c-plov").some((m) => m.startsWith("срок до 08.09, приём")),
  );
});

test("закупка: единица, срок захода и привязка к готовке", () => {
  const model = fixture({
    purchases: (purchases) => [
      ...purchases,
      item("b-lavash", "Лаваш", {
        date: "2026-09-06", product: rel("p-lavash"), qty: 1, unit: "шт",
        for: rel("c-salad"), status: "план",
      }),
      item("b-sirota", "Сахар", { date: "2026-09-05", qty: 1, unit: "уп", status: "план" }),
    ],
  });
  const found = checkPurchases(model);
  assert.deepEqual(messages(found, "b-lavash"), [
    "единица не та же, что у продукта: закупка в шт, Лаваш в уп",
    "нужен к 05.09, заход 06.09",
  ]);
  assert.deepEqual(messages(found, "b-sirota"), ["не привязан ни к одной готовке"]);
});

test("отменённая закупка без привязки — не находка", () => {
  const model = fixture({
    purchases: (purchases) => [
      ...purchases,
      item("b-snyato", "Творог", { date: "2026-09-08", qty: 720, unit: "г", status: "отменено" }),
    ],
  });
  assert.deepEqual(messages(checkPurchases(model), "b-snyato"), []);
});

test("runRules склеивает находки одной записи в одну строку без повторов", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vt-u-v", "Вт · ужин · Влад", {
        date: "2026-09-08", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-salad"), take: 1.25, status: "план",
      }),
    ],
  });
  const line = runRules(model).get("meals:m-vt-u-v");
  assert.equal(line, "Лаваш: годен до 07.09, приём 08.09; на ужин нет салата");
});

test("--from отсекает прошлое, но пропускает готовку без даты", () => {
  const since = parseFrom("2026-09-08/обед");
  assert.equal(since, mealPoint("2026-09-08", "обед"));
  assert.equal(isAfter({ date: "2026-09-08", slot: "завтрак" }, "meals", since), false);
  assert.equal(isAfter({ date: "2026-09-08", slot: "обед" }, "meals", since), true);
  assert.equal(isAfter({ date: "2026-09-07", slot: "ужин" }, "meals", since), false);
  assert.equal(isAfter({ date: "2026-09-09" }, "cooks", since), true);
  assert.equal(isAfter({ date: null }, "cooks", since), true);
  assert.equal(isAfter({ date: "2026-09-05" }, "cooks", null), true);
});

test("--from без слота берёт день целиком", () => {
  const since = parseFrom("2026-09-08");
  assert.equal(isAfter({ date: "2026-09-08", slot: "завтрак" }, "meals", since), true);
  assert.equal(isAfter({ date: "2026-09-07", slot: "ужин" }, "meals", since), false);
});

test("разбор аргументов", () => {
  const args = parseArgs(["--cooks", "c1", "--meals", "m1", "--from", "2026-09-08", "--dry-run"]);
  assert.deepEqual(args.collections, { cooks: "c1", meals: "m1" });
  assert.equal(args.from, "2026-09-08");
  assert.equal(args.dryRun, true);
  assert.throws(() => parseArgs(["--батч", "x"]), /неизвестный флаг/);
  assert.throws(() => parseFrom("вчера"), /нужна дата/);
});
