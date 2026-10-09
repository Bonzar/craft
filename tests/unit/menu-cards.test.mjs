// Состав блюда читается из карточки, а не из коллекции: таблица сразу за
// заголовком «Ингредиенты». Кейсы взяты с живых карточек — вода без ссылки,
// пометки в скобках, варианты через «или» и косую черту.
//
// Расширение .mjs: в каталоге тестов нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseIngredients, convert, convertible, needFor } from "../../menu/lib/cards.mjs";

const table = (...rows) => ["| Кол-во порций: | 2 | 6 |", "| --- | --- | --- |", ...rows].join("\n");
const link = (title, id) => `[${title}](block://${id})`;

test("базовое число порций берётся из первой строки", () => {
  const { basePortions } = parseIngredients(table(`| ${link("Филе", "a")} (г) | 300 | =x |`));
  assert.equal(basePortions, 2);
});

test("заголовок «Кол-во порций» распознаётся и жирным", () => {
  const md = ["| **Кол-во порций** | **2** | **4** |", "| --- | --- | --- |"].join("\n");
  assert.equal(parseIngredients(md).basePortions, 2);
});

test("строка без ссылки на продукт в состав не идёт", () => {
  const { rows } = parseIngredients(
    table("| Вода (мл кипятка) | 400 | =x |", `| ${link("Филе", "a")} (г) | 300 | =x |`),
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].products[0].title, "Филе");
});

test("варианты пишутся косой чертой и словом «или»", () => {
  const { rows } = parseIngredients(
    table(
      `| ${link("Ракушки", "c1")} / ${link("Бабочки", "c2")} (г) | 200 | =x |`,
      `| ${link("Сметана", "d1")} или ${link("Йогурт", "d2")} (ст. л.) | 2 | =x |`,
    ),
  );
  assert.deepEqual(rows.map((r) => r.products.length), [2, 2]);
  assert.ok(rows.every((r) => r.alternatives));
});

test("порядок вариантов сохраняется — первый предпочтительный", () => {
  const { rows } = parseIngredients(
    table(`| ${link("Ракушки", "c1")} / ${link("Бабочки", "c2")} (г) | 200 | =x |`),
  );
  assert.deepEqual(rows[0].products.map((p) => p.title), ["Ракушки", "Бабочки"]);
});

test("количество словами оставляет строку несчитаемой", () => {
  const { rows } = parseIngredients(table(`| ${link("Зелень", "z")} | по вкусу | по вкусу |`));
  assert.equal(rows[0].countable, false);
  assert.equal(rows[0].qty, null);
});

test("пометка в скобке не мешает узнать единицу продукта", () => {
  const cases = ["г тёртого", "г, 9%", "мл в соус", "шт варёных"];
  for (const measure of cases) {
    const { rows } = parseIngredients(table(`| ${link("Творог", "t")} (${measure}) | 200 | =x |`));
    const unit = measure.slice(0, measure.search(/[^\p{L}]/u) === -1 ? undefined : measure.search(/[^\p{L}]/u));
    assert.deepEqual(convert(rows[0], { id: "t", unit }, []), { qty: 200, unit });
  }
});

test("«горстей» не путается с «г» — сравнение идёт по границе слова", () => {
  const { rows } = parseIngredients(table(`| ${link("Изюм", "i")} (горстей) | 1 | =x |`));
  const withoutBridge = convert(rows[0], { id: "i", unit: "г" }, []);
  assert.equal(withoutBridge.qty, null);
  assert.equal(withoutBridge.unknown, "горстей");

  const bridged = convert(rows[0], { id: "i", unit: "г" }, [
    { productId: "i", measure: "горстей", measureQty: 1, productQty: 30 },
  ]);
  assert.deepEqual(bridged, { qty: 30, unit: "г" });
});

test("мера переводится через мост, хвост «в соус» ему не мешает", () => {
  const { rows } = parseIngredients(
    table(`| ${link("Паста", "p")} (ст. л. в соус) | 2 | =x |`),
  );
  const measures = [{ productId: "p", measure: "ст. л.", measureQty: 1, productQty: 15 }];
  assert.deepEqual(convert(rows[0], { id: "p", unit: "г" }, measures), { qty: 30, unit: "г" });
});

test("мост считает и в обратную сторону: четыре листа в упаковке", () => {
  const { rows } = parseIngredients(table(`| ${link("Листы", "l")} (шт) | 6 | =x |`));
  const measures = [{ productId: "l", measure: "шт", measureQty: 4, productQty: 1 }];
  assert.deepEqual(convert(rows[0], { id: "l", unit: "уп" }, measures), { qty: 1.5, unit: "уп" });
});

test("без записи в мосте перевода нет, и мера названа в находке", () => {
  const { rows } = parseIngredients(table(`| ${link("Мёд", "m")} (ст. л.) | 1 | =x |`));
  const got = convert(rows[0], { id: "m", unit: "г" }, []);
  assert.equal(got.qty, null);
  assert.equal(got.unknown, "ст. л.");
});

test("мост с незаполненной половиной равенства не считается", () => {
  const { rows } = parseIngredients(table(`| ${link("Мёд", "m")} (ст. л.) | 1 | =x |`));
  const product = { id: "m", unit: "г" };
  for (const half of [
    { productId: "m", measure: "ст. л.", measureQty: 1, productQty: null },
    { productId: "m", measure: "ст. л.", measureQty: null, productQty: 20 },
  ]) {
    const got = convert(rows[0], product, [half]);
    assert.equal(got.qty, null);
    assert.equal(got.unknown, "ст. л.");
  }
});

test("мост чужого продукта не применяется", () => {
  const { rows } = parseIngredients(table(`| ${link("Мёд", "m")} (ст. л.) | 1 | =x |`));
  const measures = [{ productId: "другой", measure: "ст. л.", measureQty: 1, productQty: 15 }];
  assert.equal(convert(rows[0], { id: "m", unit: "г" }, measures).qty, null);
});

test("сводимость меры отвечает да только когда есть чем перевести", () => {
  const product = { id: "s", unit: "г" };
  const measures = [{ productId: "s", measure: "уп", measureQty: 1, productQty: 300 }];
  assert.equal(convertible("г", product, measures), true);
  assert.equal(convertible("уп", product, measures), true);
  assert.equal(convertible("шт", product, measures), false);
});

test("нужное количество считается от базовых порций", () => {
  const { basePortions, rows } = parseIngredients(
    table(`| ${link("Филе", "a")} (г) | 300 | =x |`),
  );
  assert.deepEqual(needFor(rows[0], { id: "a", unit: "г" }, [], 6, basePortions), {
    qty: 900,
    unit: "г",
  });
});

test("без базовых порций нужное количество не считается", () => {
  const { rows } = parseIngredients(`| ${link("Филе", "a")} (г) | 300 | =x |`);
  assert.equal(needFor(rows[0], { id: "a", unit: "г" }, [], 6, null).qty, null);
});

test("пустая таблица не роняет разбор", () => {
  assert.deepEqual(parseIngredients(""), { basePortions: null, rows: [] });
  assert.deepEqual(parseIngredients(undefined), { basePortions: null, rows: [] });
});
