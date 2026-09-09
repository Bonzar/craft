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
import {
  checkCooks,
  checkMeals,
  checkPurchases,
  consecutiveRuns,
  runRules,
} from "../../menu/lib/rules.mjs";
import { horizons, isAfter, parseArgs, parseFrom } from "../../menu/recheck.mjs";

const rel = (...ids) => ({ relations: ids.map((blockId) => ({ blockId })) });
const item = (id, name, properties, content) => ({ id, name, properties, content });

/** Состав рецепта лежит в его теле — таблицей за заголовком «Ингредиенты». */
const composition = (...products) => [
  { type: "text", markdown: "### Ингредиенты" },
  {
    type: "table",
    markdown: [
      "| Кол-во порций: | 2 | 2 |",
      "| --- | --- | --- |",
      ...products.map(([title, id, unit, qty]) =>
        `| [${title}](block://${id}) (${unit}) | ${qty} | =x |`),
    ].join("\n"),
  },
];

/** Минимальная неделя: двое едоков, три рецепта, по одной готовке на блюдо. */
function fixture(over = {}) {
  const raw = {
    eaters: [
      item("e-vlad", "Влад", { share: 1.25, cooks: true }),
      item("e-olya", "Оля", { share: 1, cooks: false }),
    ],
    recipes: [
      // Плов просит гарнира — в чистой неделе к нему и стоит пюре.
      item("r-plov", "Плов", { kind: "горячее", keepdays: 3, needsside: true },
        composition(["Рис", "p-rice", "г", 200])),
      item("r-salad", "Салат", { kind: "салат", keepdays: 0 },
        composition(["Лаваш", "p-lavash", "уп", 1])),
      item("r-puree", "Пюре", { kind: "гарнир", keepdays: 3 }),
    ],
    products: [
      item("p-rice", "Рис", { unit: "г", qty: 500 }),
      item("p-lavash", "Лаваш", { unit: "уп", bestbefore: "2026-09-07" }),
    ],
    measures: [
      item("u-rice", "Рис · уп", {
        product: rel("p-rice"), measure: "уп", measureqty: 1, productqty: 900,
      }),
    ],
    cooks: [
      item("c-plov", "Плов, сб", {
        date: "2026-09-05", when: "вечер", recipe: rel("r-plov"),
        portions: 4.5, storage: "холодильник", status: "сделано",
      }),
      item("c-salad", "Салат, сб", {
        date: "2026-09-05", when: "вечер", recipe: rel("r-salad"),
        portions: 2.25, storage: "холодильник", status: "сделано",
      }),
      item("c-puree", "Пюре, сб", {
        date: "2026-09-05", when: "вечер", recipe: rel("r-puree"),
        portions: 8, storage: "холодильник", status: "сделано",
      }),
    ],
    meals: [
      item("m-sb-d-v", "Сб · ужин · Влад", {
        date: "2026-09-05", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), side: rel("c-puree"), extra: rel("c-salad"), status: "съеден",
      }),
      item("m-sb-d-o", "Сб · ужин · Оля", {
        date: "2026-09-05", slot: "ужин", eater: rel("e-olya"), where: "дома",
        hot: rel("c-plov"), side: rel("c-puree"), extra: rel("c-salad"), status: "съеден",
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

test("не хватит на то, что впереди, — находка; съеденное уже вычтено из остатка", () => {
  const ahead = (over) =>
    fixture({
      cooks: (cooks) =>
        cooks.map((c) =>
          c.id === "c-plov" ? item(c.id, c.name, { ...c.properties, ...over }) : c,
        ),
      meals: (meals) => [
        ...meals,
        item("m-vs-o-v", "Вс · обед · Влад", {
          date: "2026-09-06", slot: "обед", eater: rel("e-vlad"), where: "дома",
          hot: rel("c-plov"), side: rel("c-puree"), status: "план",
        }),
        item("m-vs-u-v", "Вс · ужин · Влад", {
          date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
          hot: rel("c-plov"), side: rel("c-puree"), extra: rel("c-salad"), status: "план",
        }),
      ],
    });

  // Остатка нет — считаем от выхода: 4.5 порции, впереди две твоих по 1.25.
  assert.deepEqual(messages(checkCooks(ahead({})), "c-plov"), []);

  // Осталось две порции, а впереди 2.5 — вот теперь не хватает.
  assert.deepEqual(messages(checkCooks(ahead({ remaining: 2 })), "c-plov"), [
    "впереди 2.5 порций из 2 — не хватает 0.5",
  ]);
});

test("уже съеденное впереди не числится и находки не даёт", () => {
  // Остаток нулевой, но все приёмы с пловом — «съеден»: спрашивать не о чем.
  const model = fixture({
    cooks: (cooks) =>
      cooks.map((c) =>
        c.id === "c-plov" ? item(c.id, c.name, { ...c.properties, remaining: 0 }) : c,
      ),
  });
  assert.deepEqual(messages(checkCooks(model), "c-plov"), []);
});

test("отменённый приём расхода не создаёт", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-u-o", "Вс · ужин · Оля", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-olya"), where: "вне", status: "отменён",
      }),
    ],
  });
  assert.deepEqual(runRules(model).size, 0);
});

test("пропущенный приём расхода не создаёт и закрывать его нечем", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-u-v", "Вс · ужин · Влад", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), status: "пропущен",
      }),
    ],
  });
  // Ни перерасхода у плова, ни «на ужин нет салата» у самого приёма.
  assert.deepEqual(runRules(model).size, 0);
});

test("статус решает, а не дата: съеденное вычтено, плановое спрашивается", () => {
  const withLeft = (over) =>
    fixture({
      cooks: (cooks) =>
        cooks.map((c) =>
          c.id === "c-plov" ? item(c.id, c.name, { ...c.properties, remaining: 0 }) : c,
        ),
      meals: (meals) => [...meals, over],
    });

  // Съеденный приём из остатка уже вычтен — какого бы числа он ни был.
  const dojeli = withLeft(
    item("m-vs-o-v", "Вс · обед · Влад", {
      date: "2026-09-06", slot: "обед", eater: rel("e-vlad"), where: "дома",
      hot: rel("c-plov"), side: rel("c-puree"), status: "съеден",
    }),
  );
  assert.deepEqual(messages(checkCooks(dojeli), "c-plov"), []);

  // А плановый — впереди, и на него ничего не осталось.
  const jeschoSobiraemsya = withLeft(
    item("m-vs-u-v", "Вс · ужин · Влад", {
      date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
      hot: rel("c-plov"), side: rel("c-puree"), extra: rel("c-salad"), status: "план",
    }),
  );
  assert.deepEqual(messages(checkCooks(jeschoSobiraemsya), "c-plov"), [
    "впереди 1.25 порций из 0 — не хватает 1.25",
  ]);
});

test("приём без горячего не закрыт, а ужин без салата подсвечивается отдельно", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-z-v", "Вс · завтрак · Влад", {
        date: "2026-09-06", slot: "завтрак", eater: rel("e-vlad"), where: "дома", status: "план",
      }),
      item("m-vs-u-v", "Вс · ужин · Влад", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-puree"), status: "план",
      }),
    ],
  });
  const found = checkMeals(model);
  assert.deepEqual(messages(found, "m-vs-z-v"), ["приём не закрыт: горячего нет"]);
  assert.deepEqual(messages(found, "m-vs-u-v"), ["на ужин нет салата"]);
});

test("гарнир к блюду, которое его не просит, — двойной счёт", () => {
  // Салат гарнира не просит: у его рецепта needsSide не стоит.
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-o-v", "Вс · обед · Влад", {
        date: "2026-09-06", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-salad"), side: rel("c-puree"), status: "план",
      }),
    ],
  });
  assert.deepEqual(messages(checkMeals(model), "m-vs-o-v"), [
    "гарнир лишний: Салат его не просит",
  ]);
});

test("горячее, которое просит гарнира, без гарнира — тоже находка", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-o-v", "Вс · обед · Влад", {
        date: "2026-09-06", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), status: "план",
      }),
    ],
  });
  assert.deepEqual(messages(checkMeals(model), "m-vs-o-v"), ["Плов без гарнира"]);
});

test("продукт с истёкшим сроком ловится по составу рецепта", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vt-z-v", "Вт · завтрак · Влад", {
        date: "2026-09-08", slot: "завтрак", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-salad"), status: "план",
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
        hot: rel("c-plov"), status: "съеден",
      }),
    ],
  });
  const found = checkMeals(model).filter((f) => f.message.includes("повтор"));
  assert.deepEqual(messages(found, "m-sb-o-v"), ["повтор горячего у Влад: Плов"]);
  assert.deepEqual(messages(found, "m-sb-d-o"), []);
});

test("три дня подряд одного блюда — находка на каждом приёме серии", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-vs-u-v", "Вс · ужин · Влад", {
        date: "2026-09-06", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), extra: rel("c-salad"), status: "план",
      }),
      item("m-pn-u-v", "Пн · ужин · Влад", {
        date: "2026-09-07", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), extra: rel("c-salad"), status: "план",
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
        portions: 2, storage: "морозилка", status: "план",
      }),
      item("c-nikto", "Пюре, вс", {
        date: "2026-09-06", when: "вечер", recipe: rel("r-puree"),
        portions: 2, storage: "холодильник", status: "план",
      }),
    ],
  });
  const found = checkCooks(model);
  assert.deepEqual(messages(found, "c-zapas"), []);
  assert.ok(messages(found, "c-nikto").includes("никто не ест"));
});

test("списали, а потом передумали — это видно", () => {
  const model = fixture({
    cooks: (cooks) =>
      cooks.map((c) =>
        c.id === "c-puree"
          ? item(c.id, c.name, { ...c.properties, status: "отменено", sys_counted: true })
          : c,
      ),
    purchases: (purchases) =>
      purchases.map((b) =>
        item(b.id, b.name, { ...b.properties, status: "отменено", sys_counted: true }),
      ),
  });
  assert.ok(
    messages(checkCooks(model), "c-puree").includes(
      "продукты списаны, а готовка уже не «сделано» — проверь наличие",
    ),
  );
  assert.deepEqual(messages(checkPurchases(model), "b-rice"), [
    "приход учтён, а закупка уже не «куплено» — проверь наличие",
  ]);
});

test("отменённая готовка молчит, пока её кто-нибудь не съест", () => {
  const cancel = (cooks) =>
    cooks.map((c) =>
      c.id === "c-salad" ? item(c.id, c.name, { ...c.properties, status: "отменено" }) : c,
    );

  const nikto = fixture({
    cooks: cancel,
    meals: (meals) =>
      meals.map((m) => item(m.id, m.name, { ...m.properties, extra: undefined, slot: "обед" })),
  });
  assert.deepEqual(messages(checkCooks(nikto), "c-salad"), []);

  const jedyat = fixture({ cooks: cancel });
  assert.deepEqual(messages(checkCooks(jedyat), "c-salad"), [
    "готовка отменена, а её ест Сб · ужин · Влад",
    "готовка отменена, а её ест Сб · ужин · Оля",
  ]);
});

test("срок хранения считается от даты готовки плюс keepDays рецепта", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-ct-o-v", "Ср · обед · Влад", {
        date: "2026-09-09", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), status: "план",
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
      item("b-sirota", "Сахар", {
        date: "2026-09-05", qty: 1, unit: "уп", for: rel("c-plov"), status: "план",
      }),
    ],
  });
  const found = checkPurchases(model);
  assert.deepEqual(messages(found, "b-lavash"), [
    "закупка в шт, Лаваш в уп — в «Мерах» нет заполненной строки «шт → уп»",
    "нужен к 05.09, заход 06.09",
  ]);
  assert.deepEqual(messages(found, "b-sirota"), ["не указан продукт"]);
});

test("закупка в упаковках сходится с продуктом в граммах через «Меры»", () => {
  const model = fixture({
    purchases: (purchases) => [
      ...purchases,
      item("b-rice-up", "Рис", {
        date: "2026-09-05", product: rel("p-rice"), qty: 1, unit: "уп",
        for: rel("c-plov"), status: "куплено",
      }),
    ],
  });
  assert.deepEqual(messages(checkPurchases(model), "b-rice-up"), []);
});

test("закупка без количества — такая же дыра, как без единицы", () => {
  const model = fixture({
    purchases: (purchases) => [
      ...purchases,
      item("b-nol", "Морковь", {
        date: "2026-09-05", product: rel("p-rice"), qty: 0, unit: "г",
        for: rel("c-plov"), status: "куплено",
      }),
    ],
  });
  assert.deepEqual(messages(checkPurchases(model), "b-nol"), [
    "не проставлено количество в г",
  ]);
});

test("закупка без готовки — тоже находка", () => {
  const model = fixture({
    purchases: (purchases) => [
      ...purchases,
      item("b-sam", "Рис", {
        date: "2026-09-05", product: rel("p-rice"), qty: 500, unit: "г", status: "план",
      }),
    ],
  });
  assert.deepEqual(messages(checkPurchases(model), "b-sam"), ["не привязан ни к одной готовке"]);
});

test("отменённая закупка не спрашивается ни о чём", () => {
  const model = fixture({
    purchases: (purchases) => [
      ...purchases,
      // Ни привязки к готовке, ни сводимой единицы, ни срока — и всё равно молчок.
      item("b-snyato", "Лаваш", {
        date: "2026-09-08", product: rel("p-lavash"), qty: 1, unit: "шт", status: "отменено",
      }),
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
        hot: rel("c-salad"), status: "план",
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

test("--from не отсекает то, что ещё в плане", () => {
  const since = parseFrom("2026-09-09");
  assert.equal(isAfter({ date: "2026-09-08", status: "план" }, "purchases", since), true);
  assert.equal(isAfter({ date: "2026-09-08", status: "куплено" }, "purchases", since), false);
  assert.equal(
    isAfter({ date: "2026-09-08", slot: "ужин", status: "план" }, "meals", since),
    true,
  );
});

test("готовка прошлым не становится, пока её едят впереди", () => {
  const model = fixture({
    meals: (meals) => [
      ...meals,
      item("m-cht-o-v", "Чт · обед · Влад", {
        date: "2026-09-10", slot: "обед", eater: rel("e-vlad"), where: "дома",
        side: rel("c-puree"), hot: rel("c-plov"), status: "план",
      }),
    ],
  });
  const reach = horizons(model);
  const since = parseFrom("2026-09-09");

  // Пюре сварено в субботу, а гарниром идёт в четверг — отсекать его рано.
  assert.equal(reach.cooks.get("c-puree"), mealPoint("2026-09-10", "обед"));
  assert.equal(isAfter({ date: "2026-09-05", status: "сделано" }, "cooks",
    since, reach.cooks.get("c-puree")), true);

  // Салат съеден в субботу и больше нигде — это прошлое.
  assert.equal(reach.cooks.get("c-salad"), mealPoint("2026-09-05", "ужин"));
  assert.equal(isAfter({ date: "2026-09-05", status: "сделано" }, "cooks",
    since, reach.cooks.get("c-salad")), false);

  // Закупка дотягивается до готовки, под которую взята.
  assert.equal(reach.purchases.get("b-rice"), mealPoint("2026-09-10", "обед"));
});

test("--from без слота берёт день целиком", () => {
  const since = parseFrom("2026-09-08");
  assert.equal(isAfter({ date: "2026-09-08", slot: "завтрак" }, "meals", since), true);
  assert.equal(isAfter({ date: "2026-09-07", slot: "ужин" }, "meals", since), false);
});

test("две несмежные серии длиннее двух дней — находки у обеих", () => {
  const at = (date, slot, id) =>
    item(`m-${date}-${slot}`, `${date} · ${slot} · Влад`, {
      date, slot, eater: rel("e-vlad"), where: "дома",
      hot: rel("c-plov"), extra: rel("c-salad"), status: "план",
    });
  const model = fixture({
    meals: () => [
      at("2026-09-05", "ужин"), at("2026-09-06", "ужин"), at("2026-09-07", "ужин"),
      at("2026-09-09", "ужин"), at("2026-09-10", "ужин"),
      at("2026-09-11", "ужин"), at("2026-09-12", "ужин"),
    ],
  });
  const spans = new Set(
    checkMeals(model)
      .filter((f) => f.message.includes("подряд"))
      .map((f) => f.message),
  );
  assert.deepEqual([...spans].sort(), [
    "Плов подряд 3 дня: 05.09—07.09",
    "Плов подряд 4 дня: 09.09—12.09",
  ]);
});

test("серии считаются по календарным дням, дубли дат не удлиняют", () => {
  assert.deepEqual(consecutiveRuns([]), []);
  assert.deepEqual(consecutiveRuns(["2026-09-05", "2026-09-05", "2026-09-06"]), [
    ["2026-09-05", "2026-09-06"],
  ]);
  assert.deepEqual(consecutiveRuns(["2026-09-07", "2026-09-05"]), [
    ["2026-09-05"],
    ["2026-09-07"],
  ]);
});

test("повтор горячего сравнивает обед с ужином, завтрак не в счёт", () => {
  const zavtrakILunch = fixture({
    meals: () => [
      item("m-z", "Сб · завтрак · Влад", {
        date: "2026-09-05", slot: "завтрак", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), status: "съеден",
      }),
      item("m-o", "Сб · обед · Влад", {
        date: "2026-09-05", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), status: "съеден",
      }),
    ],
  });
  assert.deepEqual(
    checkMeals(zavtrakILunch).filter((f) => f.message.includes("повтор")),
    [],
  );

  const obedIUzhin = fixture({
    meals: () => [
      item("m-o", "Сб · обед · Влад", {
        date: "2026-09-05", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), status: "съеден",
      }),
      item("m-u", "Сб · ужин · Влад", {
        date: "2026-09-05", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), extra: rel("c-salad"), status: "съеден",
      }),
    ],
  });
  assert.deepEqual(
    messages(checkMeals(obedIUzhin).filter((f) => f.message.includes("повтор")), "m-o"),
    ["повтор горячего у Влад: Плов"],
  );
});

test("гарнир не повторяется за день целиком", () => {
  const model = fixture({
    meals: () => [
      item("m-o", "Сб · обед · Влад", {
        date: "2026-09-05", slot: "обед", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-plov"), side: rel("c-puree"), status: "съеден",
      }),
      item("m-u", "Сб · ужин · Влад", {
        date: "2026-09-05", slot: "ужин", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-salad"), side: rel("c-puree"), extra: rel("c-salad"), status: "съеден",
      }),
    ],
  });
  assert.ok(messages(checkMeals(model), "m-o").includes("повтор гарнира у Влад: Пюре"));
});

test("--from с неизвестным слотом отвергается, а не проходит молча", () => {
  assert.throws(() => parseFrom("2026-09-08/обде"), /неизвестный слот/);
  assert.throws(() => parseFrom("2026-09-08/"), /неизвестный слот/);
  assert.equal(parseFrom("2026-09-08/ужин"), mealPoint("2026-09-08", "ужин"));
});

test("разбор аргументов", () => {
  const args = parseArgs(["--cooks", "c1", "--meals", "m1", "--from", "2026-09-08", "--dry-run"]);
  assert.deepEqual(args.collections, { cooks: "c1", meals: "m1" });
  assert.equal(args.from, "2026-09-08");
  assert.equal(args.dryRun, true);
  assert.throws(() => parseArgs(["--батч", "x"]), /неизвестный флаг/);
  assert.throws(() => parseFrom("вчера"), /нужна дата/);
});
