// Список покупок складывает составы будущих готовок и вычитает то, что дома и
// что уже заказано. Кейсы — те, на которых он мог бы соврать: пересчёт на
// порции, две готовки одного продукта, закупка в упаковках, мера без моста.
//
// Расширение .mjs: в каталоге тестов нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildModel, indexModel } from "../../menu/lib/model.mjs";
import { incoming, need, shortfall } from "../../menu/lib/need.mjs";
import { parseArgs, planned } from "../../menu/need.mjs";

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
    eaters: [],
    recipes: [
      item("r-bol", "Болоньезе", { kind: "горячее" },
        composition(2, ["Фарш", "p-farsh", "г", 300], ["Паста", "p-pasta", "ст. л.", 2])),
      item("r-perec", "Перцы", { kind: "горячее" },
        composition(2, ["Фарш", "p-farsh", "г", 350])),
    ],
    products: [
      item("p-farsh", "Фарш", { unit: "г", qty: 500 }),
      item("p-pasta", "Томатная паста", { unit: "г" }),
    ],
    measures: [
      item("u-pasta", "Томатная паста · ст. л.", {
        product: rel("p-pasta"), measure: "ст. л.", measureqty: 1, productqty: 15,
      }),
    ],
    cooks: [
      item("c-bol", "Болоньезе, ср", {
        date: "2026-09-09", recipe: rel("r-bol"), portions: 4.5, status: "план",
      }),
      item("c-perec", "Перцы, ср", {
        date: "2026-09-09", recipe: rel("r-perec"), portions: 4.5, status: "план",
      }),
      item("c-plov", "Плов, сб", {
        date: "2026-09-05", recipe: rel("r-bol"), portions: 4, status: "сделано",
      }),
    ],
    meals: [],
    purchases: [
      item("b-farsh", "Фарш", {
        date: "2026-09-08", product: rel("p-farsh"), qty: 1000, unit: "г", status: "план",
      }),
    ],
  };
  for (const [key, value] of Object.entries(over)) raw[key] = value(raw[key]);
  return indexModel(buildModel(raw));
}

const qtyOf = (rows, name) => rows.find((r) => r.product.name === name)?.qty;

test("считаются только будущие готовки в плане", () => {
  const ix = fixture();
  assert.deepEqual(planned(ix.cooks, "2026-09-09").map((c) => c.name), [
    "Болоньезе, ср",
    "Перцы, ср",
  ]);
  assert.deepEqual(planned(ix.cooks, null).map((c) => c.name), ["Болоньезе, ср", "Перцы, ср"]);
});

test("продукт из двух готовок складывается, каждая на своё число порций", () => {
  const ix = fixture();
  const { rows } = need(ix, planned(ix.cooks, "2026-09-09"));
  // 300 г на 2 порции → 675 на 4.5; 350 → 787.5. Вместе 1462.5.
  assert.equal(qtyOf(rows, "Фарш"), 1462.5);
  assert.deepEqual(rows.find((r) => r.product.name === "Фарш").cooks, [
    "Болоньезе, ср",
    "Перцы, ср",
  ]);
});

test("кухонная мера переводится в единицу продукта через «Меры»", () => {
  const ix = fixture();
  const { rows, unknown } = need(ix, planned(ix.cooks, "2026-09-09"));
  assert.equal(qtyOf(rows, "Томатная паста"), 67.5); // 2 ст. л. = 30 г на 2 порции
  assert.deepEqual(unknown, []);
});

test("мера без моста в сумму не идёт, а называется отдельно", () => {
  const ix = fixture({ measures: () => [] });
  const { rows, unknown } = need(ix, planned(ix.cooks, "2026-09-09"));
  assert.equal(qtyOf(rows, "Томатная паста"), undefined);
  assert.deepEqual(
    unknown.map((u) => [u.product.name, u.measure]),
    [["Томатная паста", "ст. л."]],
  );
});

test("заказанное считается в единице продукта и вычитается вместе с домашним", () => {
  const ix = fixture();
  const { rows } = need(ix, planned(ix.cooks, "2026-09-09"));
  const ordered = incoming(ix, ix.purchases.filter((p) => p.status === "план"));
  assert.equal(ordered.get("p-farsh"), 1000);

  const short = shortfall(rows, ordered);
  const farsh = short.find((r) => r.product.name === "Фарш");
  // 1462.5 надо, 500 дома, 1000 заказано — не хватает нисколько, строки нет.
  assert.equal(farsh, undefined);
});

test("закупка в упаковках вычитается через мост, а не как есть", () => {
  const ix = fixture({
    purchases: () => [
      item("b-pasta", "Томатная паста", {
        date: "2026-09-08", product: rel("p-pasta"), qty: 2, unit: "ст. л.", status: "план",
      }),
    ],
  });
  const ordered = incoming(ix, ix.purchases);
  assert.equal(ordered.get("p-pasta"), 30);
});

test("не хватает — это надо минус дома минус заказанное", () => {
  const ix = fixture({ purchases: () => [] });
  const { rows } = need(ix, planned(ix.cooks, "2026-09-09"));
  const short = shortfall(rows, new Map());
  assert.deepEqual(
    short.map((r) => [r.product.name, r.qty, r.have, r.short]),
    [
      ["Фарш", 1462.5, 500, 962.5],
      ["Томатная паста", 67.5, 0, 67.5],
    ],
  );
});

test("из вариантов берётся тот, что есть дома", () => {
  const withPasta = (over) =>
    indexModel(
      buildModel({
        eaters: [], meals: [], purchases: [], measures: [],
        recipes: [
          item("r-garnir", "Гарнир", { kind: "гарнир" },
            composition(2, ["Ракушки", "p-rakushki", "г", 200])),
        ],
        products: [
          item("p-rakushki", "Ракушки", { unit: "г", qty: over.rakushki }),
          item("p-babochki", "Бабочки", { unit: "г", qty: over.babochki }),
        ],
        cooks: [
          item("c-garnir", "Гарнир, ср", {
            date: "2026-09-09", recipe: rel("r-garnir"), portions: 2, status: "план",
          }),
        ],
      }),
    );

  // Две ссылки в одной ячейке — варианты одного ингредиента.
  const two = (ix) => {
    const recipe = ix.recipes[0];
    recipe.ingredients[0].products = [
      { id: "p-rakushki", title: "Ракушки" },
      { id: "p-babochki", title: "Бабочки" },
    ];
    return ix;
  };

  const est = two(withPasta({ rakushki: 0, babochki: 450 }));
  assert.equal(need(est, est.cooks).rows[0].product.name, "Бабочки");

  const netNichego = two(withPasta({ rakushki: 0, babochki: 0 }));
  assert.equal(need(netNichego, netNichego.cooks).rows[0].product.name, "Ракушки");
});

test("количество словами в сумму не идёт и дырой в мерах не считается", () => {
  const ix = indexModel(
    buildModel({
      eaters: [], meals: [], purchases: [], measures: [],
      recipes: [
        item("r-kotlety", "Котлеты", { kind: "горячее" }, [
          { type: "text", markdown: "### Ингредиенты" },
          {
            type: "table",
            markdown: [
              "| Кол-во порций: | 2 | 2 |",
              "| --- | --- | --- |",
              "| [Зелень](block://p-zelen) | по вкусу | по вкусу |",
            ].join("\n"),
          },
        ]),
      ],
      products: [item("p-zelen", "Зелень", { unit: "пучок" })],
      cooks: [
        item("c-kotlety", "Котлеты, ср", {
          date: "2026-09-09", recipe: rel("r-kotlety"), portions: 2, status: "план",
        }),
      ],
    }),
  );
  const got = need(ix, ix.cooks);
  assert.deepEqual(got.rows, []);
  assert.deepEqual(got.unknown, []);
});

test("разбор аргументов", () => {
  assert.deepEqual(parseArgs(["--from", "2026-09-09", "--all"]), {
    from: "2026-09-09",
    all: true,
  });
  assert.throws(() => parseArgs(["--from", "вчера"]), /нужна дата/);
  assert.throws(() => parseArgs(["--батч"]), /лишний аргумент/);
});
