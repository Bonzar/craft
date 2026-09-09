// Наличие считается арифметикой: закупка «куплено» прибавляет, готовка
// «сделано» вычитает. Кейсы — те, на которых оно могло бы соврать: план ещё
// не случился, отменённое не случится вовсе, а окно отсекает прошлое.
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

const one = (ix, from) => ledger(ix, from).moved[0];

test("куплено прибавляет, сделано вычитает", () => {
  const r = one(fixture(), "2026-09-05");
  assert.deepEqual([r.was, r.plus, r.minus, r.now], [175, 800, 400, 575]);
});

test("окно отсекает то, что уже учтено раньше", () => {
  // Закупка субботняя, готовка воскресная: с понедельника видно только расход.
  const r = one(fixture(), "2026-09-07");
  assert.equal(r, undefined);
  const sPon = one(fixture(), "2026-09-06");
  assert.deepEqual([sPon.plus, sPon.minus, sPon.now], [0, 400, -225]);
});

test("план и отменённое в счёт не идут", () => {
  const ix = fixture({
    cooks: (cooks) => [
      ...cooks,
      item("c-sr", "Сырники, ср", {
        date: "2026-09-09", recipe: rel("r-syr"), portions: 2, status: "план",
      }),
      item("c-otm", "Сырники, чт", {
        date: "2026-09-10", recipe: rel("r-syr"), portions: 2, status: "отменено",
      }),
    ],
    purchases: (purchases) => [
      ...purchases,
      item("b-plan", "Творог", {
        date: "2026-09-08", product: rel("p-tvorog"), qty: 720, unit: "г",
        for: rel("c-sr"), status: "план",
      }),
      item("b-otm", "Творог", {
        date: "2026-09-08", product: rel("p-tvorog"), qty: 500, unit: "г",
        for: rel("c-sr"), status: "отменено",
      }),
    ],
  });
  const r = one(ix, "2026-09-05");
  assert.deepEqual([r.plus, r.minus, r.now], [800, 400, 575]);
});

test("продукт, которого ничего не касалось, в отчёт не попадает", () => {
  const ix = fixture({
    products: (products) => [...products, item("p-sol", "Соль", { unit: "г", qty: 500 })],
  });
  assert.deepEqual(ledger(ix, "2026-09-05").moved.map((r) => r.product.name), ["Творог"]);
});

test("окно обязательно: без него код не знает, что Qty уже видел", () => {
  assert.throws(() => parseArgs([]), /окно пересчёта задаёт человек/);
  assert.throws(() => parseArgs(["--from", "вчера"]), /нужна дата/);
  assert.deepEqual(parseArgs(["--from", "2026-09-05", "--apply"]), {
    from: "2026-09-05",
    apply: true,
  });
});
