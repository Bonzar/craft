// «Пометил и посчитал» — одна операция: статус, движение по наличию и галочка
// «проведено». Кейсы про то, чем она может соврать: не тот флаг не к той
// таблице, имя, подходящее нескольким записям, и приём, который наличия не
// касается вовсе.
//
// Расширение .mjs: в каталоге тестов нет манифеста модулей, и .js читался бы
// как обычный скрипт, которому импорт недоступен.
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseArgs, pickOne } from "../../menu/mark.mjs";

test("разбор: что помечаем и что случилось", () => {
  assert.deepEqual(parseArgs(["--cook", "Болоньезе, ср 9.09", "--done"]), {
    kind: "cooks", name: "Болоньезе, ср 9.09", deed: "done",
    qty: null, remaining: null, dryRun: false,
  });
  assert.deepEqual(parseArgs(["--purchase", "Фарш", "--bought", "--qty", "800"]), {
    kind: "purchases", name: "Фарш", deed: "bought",
    qty: 800, remaining: null, dryRun: false,
  });
  assert.deepEqual(parseArgs(["--meal", "Ср · ужин · Влад", "--skipped", "--dry-run"]), {
    kind: "meals", name: "Ср · ужин · Влад", deed: "skipped",
    qty: null, remaining: null, dryRun: true,
  });
});

test("остаток можно записать и не меняя статуса", () => {
  const args = parseArgs(["--cook", "Плов, сб 5.09", "--remaining", "2"]);
  assert.equal(args.deed, null);
  assert.equal(args.remaining, 2);
});

test("флаг не к той таблице отвергается, а не делает вид", () => {
  assert.throws(() => parseArgs(["--cook", "Плов", "--bought"]), /не про эту таблицу/);
  assert.throws(() => parseArgs(["--meal", "Обед", "--done"]), /не про эту таблицу/);
  assert.throws(() => parseArgs(["--cook", "Плов"]), /что случилось/);
  assert.throws(() => parseArgs(["--done"]), /с именем/);
  assert.throws(() => parseArgs(["--cook", "Плов", "--сварил"]), /неизвестный флаг/);
  assert.throws(() => parseArgs(["--purchase", "Фарш", "--bought", "--qty", "много"]), /нужно число/);
});

test("имя ищется точно, потом частью, и молчать про двусмысленность нельзя", () => {
  const items = [
    { id: "1", name: "Конверты, вт 8.09" },
    { id: "2", name: "Конверты, до недели" },
    { id: "3", name: "Плов, сб 5.09" },
  ];
  assert.equal(pickOne(items, "Плов").id, "3");
  assert.equal(pickOne(items, "плов, сб").id, "3");
  assert.equal(pickOne(items, "Конверты, вт 8.09").id, "1");
  assert.throws(() => pickOne(items, "Конверты"), /подходит нескольким/);
  assert.throws(() => pickOne(items, "Борщ"), /не нашёл/);
});
