// Колонка «нужно» в карточке блюда: четвёртый столбец под порции ближайшей
// готовки, пересчёт отдан формулам Craft.
//
// Расширение .mjs: в каталоге тестов нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { test } from "node:test";
import assert from "node:assert/strict";

import { setPortionsColumn, parseIngredients } from "../../menu/lib/cards.mjs";
import { parseArgs, nextCooks, ingredientBlock } from "../../menu/portions.mjs";

const table = [
  "| Кол-во порций: | 2 | 4 |",
  "| --- | --- | --- |",
  "| [Творог](block://p-t) (г) | 400 | =B2*(C1/B1) |",
  "| [Изюм](block://p-i) (горсть) | по вкусу | по вкусу |",
].join("\n");

test("столбец четвёртый, в шапке голое число — иначе формулы его не прочтут", () => {
  const out = setPortionsColumn(table, 1.8).split("\n");
  assert.equal(out[0], "| Кол-во порций: | 2 | 4 | 1.8 |");
  assert.equal(out[1], "| --- | --- | --- | --- |");
  assert.equal(out[2], "| [Творог](block://p-t) (г) | 400 | =B2*(C1/B1) | =B2*(D1/B1) |");
});

test("столбцы правее переживают прогон — заведённое руками не срезается", () => {
  const wide = [
    "| Кол-во порций: | 2 | 4 | 3.5 | моё |",
    "| --- | --- | --- | --- | --- |",
    "| [Творог](block://p-t) (г) | 400 | =B2*(C1/B1) | =B2*(D1/B1) | своё |",
  ].join("\n");
  const out = setPortionsColumn(wide, 1.8).split("\n");
  assert.equal(out[0], "| Кол-во порций: | 2 | 4 | 1.8 | моё |");
  assert.equal(out[2], "| [Творог](block://p-t) (г) | 400 | =B2*(C1/B1) | =B2*(D1/B1) | своё |");
});

test("таблица без колонки пересчёта пропускается, а не дырявится", () => {
  // Колонка встала бы четвёртой, третья осталась бы пустой посреди состава.
  const narrow = ["| Кол-во порций: | 2 |", "| --- | --- |", "| [Творог](block://p-t) (г) | 400 |"];
  assert.equal(setPortionsColumn(narrow.join("\n"), 1.8), null);
  assert.equal(setPortionsColumn("", 1.8), null);
});

test("количество словами переносится как есть — формула по нему не считается", () => {
  const out = setPortionsColumn(table, 1.8).split("\n");
  assert.equal(out[3], "| [Изюм](block://p-i) (горсть) | по вкусу | по вкусу | по вкусу |");
});

test("прогон второй раз ничего не сдвигает", () => {
  const once = setPortionsColumn(table, 1.8);
  assert.equal(setPortionsColumn(once, 1.8), once);
});

test("состав читается тем же разбором и после правки", () => {
  const { basePortions, rows } = parseIngredients(setPortionsColumn(table, 1.8));
  assert.equal(basePortions, 2);
  assert.deepEqual(rows.map((r) => [r.products[0].title, r.qty, r.countable]), [
    ["Творог", 400, true],
    ["Изюм", null, false],
  ]);
});

test("из нескольких готовок одного блюда берётся ближайшая", () => {
  const cooks = [
    { id: "c-2", name: "Макароны, пт", recipeId: "r-mak", status: "план", date: "2026-09-11", when: "вечер", portions: 2.25 },
    { id: "c-1", name: "Макароны, чт", recipeId: "r-mak", status: "план", date: "2026-09-10", when: "утро", portions: 1.25 },
    { id: "c-x", name: "Макароны, ср", recipeId: "r-mak", status: "сделано", date: "2026-09-09", when: "вечер", portions: 3.5 },
  ];
  assert.equal(nextCooks(cooks).get("r-mak").id, "c-1");
});

test("слот внутри дня решает, какая готовка ближе", () => {
  const cooks = [
    { id: "c-v", name: "Рис, вечер", recipeId: "r-ris", status: "план", date: "2026-09-10", when: "вечер", portions: 2 },
    { id: "c-u", name: "Рис, утро", recipeId: "r-ris", status: "план", date: "2026-09-10", when: "утро", portions: 4 },
  ];
  assert.equal(nextCooks(cooks).get("r-ris").id, "c-u");
});

test("таблица берётся та, что за заголовком «Ингредиенты»", () => {
  const content = [
    { type: "table", id: "t-chuzhaya", markdown: "| Отзыв | 5 |" },
    { type: "text", markdown: "### Ингредиенты" },
    { type: "table", id: "t-sostav", markdown: table },
  ];
  assert.equal(ingredientBlock(content).id, "t-sostav");
  assert.equal(ingredientBlock([]), null);
});

test("флаги разбираются, лишний отвергается", () => {
  assert.deepEqual(parseArgs(["--dry-run"]), { dryRun: true });
  assert.throws(() => parseArgs(["--kuda"]), /неизвестный флаг/);
});
