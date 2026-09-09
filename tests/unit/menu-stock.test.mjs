// Наличие считается арифметикой: закупка «куплено» прибавляет, готовка
// «сделано» вычитает. Отсечка у каждого продукта своя — `QtyOn`, последний
// целиком учтённый день, и движения этого дня в счёт уже не идут.
//
// Расширение .mjs: в каталоге тестов нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildModel, indexModel } from "../../menu/lib/model.mjs";
import { ledger } from "../../menu/lib/stock.mjs";
import { parseArgs } from "../../menu/stock.mjs";

const rel = (...ids) => ({ relations: ids.map((blockId) => ({ blockId })) });
const item = (id, name, properties, content) => ({ id, name, properties, content });

const composition = (base, ...products) => [
  { type: "text", markdown: "### Ингредиенты" },
  {
    type: "table",
    markdown: [
      `| Кол-во порций: | ${base} | ${base} |`,
      "| --- | --- | --- |",
      ...products.map(([title, id, unit, qty]) =>
        `| [${title}](block://${id}) (${unit}) | ${qty} | =x |`),
    ].join("\n"),
  },
];

function fixture(over = {}) {
  const raw = {
    eaters: [], meals: [], measures: [],
    recipes: [
      item("r-syr", "Сырники", { kind: "завтрак" },
        composition(2, ["Творог", "p-tvorog", "г", 400])),
    ],
    products: [item("p-tvorog", "Творог", { unit: "г", qty: 175 })],
    cooks: [
      item("c-vs", "Сырники, вс", {
        date: "2026-09-06", recipe: rel("r-syr"), portions: 2, status: "сделано",
      }),
    ],
    purchases: [
      item("b-sb", "Творог", {
        date: "2026-09-05", product: rel("p-tvorog"), qty: 800, unit: "г",
        for: rel("c-vs"), status: "куплено",
      }),
    ],
  };
  for (const [key, value] of Object.entries(over)) raw[key] = value(raw[key]);
  return indexModel(buildModel(raw));
}

const one = (ix) => ledger(ix).changed[0];
const counted = (over) => (rows) =>
  rows.map((r) => (over.includes(r.id) ? item(r.id, r.name, { ...r.properties, sys_counted: true }) : r));

test("куплено прибавляет, сделано вычитает", () => {
  const r = one(fixture());
  assert.deepEqual([r.was, r.plus, r.minus, r.now], [175, 800, 400, 575]);
});

test("проведённое движение второй раз не считается", () => {
  const bezPrihoda = one(fixture({ purchases: counted(["b-sb"]) }));
  assert.deepEqual([bezPrihoda.plus, bezPrihoda.minus, bezPrihoda.now], [0, 400, -225]);

  // Отмечено всё — считать нечего, и прогон можно повторять сколько угодно.
  const ix = fixture({ purchases: counted(["b-sb"]), cooks: counted(["c-vs"]) });
  assert.deepEqual(ledger(ix).changed, []);
});

test("движения возвращаются вместе с записями, которые их породили", () => {
  const { records } = ledger(fixture());
  assert.deepEqual(records, { cooks: ["c-vs"], purchases: ["b-sb"], meals: [] });
});

test("съеденный приём подъедает порции каждого блюда на тарелке", () => {
  const ix = fixture({
    eaters: () => [item("e-vlad", "Влад", { share: 1.25, cooks: true })],
    cooks: (cooks) =>
      cooks.map((c) => item(c.id, c.name, { ...c.properties, portions: 4, remaining: 4 })),
    meals: () => [
      item("m-zavtrak", "Вс · завтрак · Влад", {
        date: "2026-09-06", slot: "завтрак", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-vs"), status: "съеден",
      }),
    ],
  });
  const { portions, records } = ledger(ix);
  assert.deepEqual(
    portions.map((p) => [p.cook.name, p.was, p.minus, p.now]),
    [["Сырники, вс", 4, 1.25, 2.75]],
  );
  assert.deepEqual(records.meals, ["m-zavtrak"]);

  // Отмеченный приём второй раз не подъедает.
  const done = fixture({
    eaters: () => [item("e-vlad", "Влад", { share: 1.25, cooks: true })],
    meals: () => [
      item("m-zavtrak", "Вс · завтрак · Влад", {
        date: "2026-09-06", slot: "завтрак", eater: rel("e-vlad"), where: "дома",
        hot: rel("c-vs"), status: "съеден", sys_counted: true,
      }),
    ],
  });
  assert.deepEqual(ledger(done).portions, []);
});

test("план и отменённое в счёт не идут", () => {
  const ix = fixture({
    cooks: (cooks) => [
      ...cooks,
      item("c-plan", "Сырники, ср", {
        date: "2026-09-09", recipe: rel("r-syr"), portions: 2, status: "план",
      }),
      item("c-otm", "Сырники, чт", {
        date: "2026-09-10", recipe: rel("r-syr"), portions: 2, status: "отменено",
      }),
    ],
    purchases: (purchases) => [
      ...purchases,
      item("b-plan", "Творог", {
        date: "2026-09-08", product: rel("p-tvorog"), qty: 720, unit: "г", status: "план",
      }),
      item("b-otm", "Творог", {
        date: "2026-09-08", product: rel("p-tvorog"), qty: 500, unit: "г", status: "отменено",
      }),
    ],
  });
  const r = one(ix);
  assert.deepEqual([r.plus, r.minus, r.now], [800, 400, 575]);
});

test("продукт, которого ничего не касалось, в отчёт не попадает", () => {
  const ix = fixture({
    products: (products) => [...products, item("p-sol", "Соль", { unit: "г", qty: 500 })],
  });
  assert.deepEqual(ledger(ix).changed.map((r) => r.product.name), ["Творог"]);
});

test("непереводимая мера в сумму не идёт и называется отдельно", () => {
  const ix = fixture({
    recipes: () => [
      item("r-syr", "Сырники", { kind: "завтрак" },
        composition(2, ["Творог", "p-tvorog", "ст. л.", 4])),
    ],
  });
  const { changed, unknown } = ledger(ix);
  assert.equal(changed[0].minus, 0);
  assert.deepEqual(unknown.map((u) => [u.product.name, u.measure]), [["Творог", "ст. л."]]);
});

test("дат у догонялки больше нет: что учтено, помнит сама запись", () => {
  assert.deepEqual(parseArgs([]), { apply: false });
  assert.deepEqual(parseArgs(["--apply"]), { apply: true });
  assert.throws(() => parseArgs(["--through", "2026-09-08"]), /лишний аргумент/);
});
