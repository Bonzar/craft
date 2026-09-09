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
import { parseArgs, yesterday } from "../../menu/stock.mjs";

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

const one = (ix, through) => ledger(ix, through).changed[0];
const markedOn = (day) => (products) =>
  products.map((p) => item(p.id, p.name, { ...p.properties, qtyon: day }));

test("куплено прибавляет, сделано вычитает", () => {
  const r = one(fixture(), "2026-09-08");
  assert.deepEqual([r.was, r.plus, r.minus, r.now], [175, 800, 400, 575]);
});

test("отсечка продукта отбрасывает уже учтённое, включая свой день", () => {
  // Закупка субботняя, готовка воскресная. Отметка за субботу оставляет расход.
  const sb = one(fixture({ products: markedOn("2026-09-05") }), "2026-09-08");
  assert.deepEqual([sb.plus, sb.minus, sb.now], [0, 400, -225]);

  // Отметка за воскресенье: учтено всё, считать нечего.
  assert.equal(one(fixture({ products: markedOn("2026-09-06") }), "2026-09-08"), undefined);
});

test("день считается целиком и только когда закончился", () => {
  const ix = fixture({
    cooks: (cooks) => [
      ...cooks,
      item("c-sr", "Сырники, ср", {
        date: "2026-09-09", recipe: rel("r-syr"), portions: 2, status: "сделано",
      }),
    ],
  });
  // По вторник среда ещё не в счёт; по среду — уже.
  assert.equal(one(ix, "2026-09-08").minus, 400);
  assert.equal(one(ix, "2026-09-09").minus, 800);
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
  const r = one(ix, "2026-09-10");
  assert.deepEqual([r.plus, r.minus, r.now], [800, 400, 575]);
});

test("продукт, которого ничего не касалось, в отчёт не попадает", () => {
  const ix = fixture({
    products: (products) => [...products, item("p-sol", "Соль", { unit: "г", qty: 500 })],
  });
  assert.deepEqual(ledger(ix, "2026-09-08").changed.map((r) => r.product.name), ["Творог"]);
});

test("непереводимая мера в сумму не идёт и называется отдельно", () => {
  const ix = fixture({
    recipes: () => [
      item("r-syr", "Сырники", { kind: "завтрак" },
        composition(2, ["Творог", "p-tvorog", "ст. л.", 4])),
    ],
  });
  const { changed, unknown } = ledger(ix, "2026-09-08");
  assert.equal(changed[0].minus, 0);
  assert.deepEqual(unknown.map((u) => [u.product.name, u.measure]), [["Творог", "ст. л."]]);
});

test("по умолчанию считается по вчера", () => {
  const now = new Date("2026-09-09T08:00:00Z");
  assert.equal(yesterday(now), "2026-09-08");
  assert.deepEqual(parseArgs([], now), { through: "2026-09-08", apply: false });
  assert.deepEqual(parseArgs(["--through", "2026-09-09", "--apply"], now), {
    through: "2026-09-09",
    apply: true,
  });
  assert.throws(() => parseArgs(["--through", "вчера"], now), /нужна дата/);
});
