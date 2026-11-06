#!/usr/bin/env node
// Догнать наличие по записям: закупка «куплено» приносит продукты, готовка
// «сделано» их тратит, приём «съеден» съедает порции готовки. Обычный путь другой — количество меняется вместе со статусом, в тот
// же момент; это догонялка для случая, когда статусы проставлены, а цифры нет.
//
//   node menu/stock.mjs           # показать, ничего не писать
//   node menu/stock.mjs --apply   # записать Qty и отметить движения
//
// Что уже проведено, помнит галочка `sys_Counted` на самой записи, поэтому
// прогон можно повторять: одно движение учтётся ровно один раз.

import { createClient } from "./lib/craft-api.mjs";
import { buildModel, indexModel } from "./lib/model.mjs";
import { ledger } from "./lib/stock.mjs";

const KINDS = ["cooks", "meals", "purchases", "recipes", "products", "eaters", "measures"];

export function parseArgs(argv) {
  const args = { apply: false };
  for (const arg of argv) {
    if (arg === "--apply") args.apply = true;
    else throw new Error(`лишний аргумент ${arg}`);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const client = createClient({ base: process.env.CRAFT_API_BASE });
  const collections = JSON.parse(process.env.CRAFT_MENU_COLLECTIONS ?? "{}");
  const missing = KINDS.filter((k) => !collections[k]);
  if (missing.length > 0) throw new Error(`не заданы коллекции: ${missing.join(", ")}`);

  const raw = {};
  for (const kind of KINDS) raw[kind] = await client.getItems(collections[kind]);
  const ix = indexModel(buildModel(raw));
  const { changed, portions, records, unknown } = ledger(ix);

  if (changed.length === 0 && portions.length === 0 && unknown.length === 0) {
    console.log("наличие сходится с записями: непроведённых движений нет");
    return;
  }

  const width = Math.max(0, ...changed.map((r) => r.product.name.length));
  for (const row of changed) {
    const delta = [row.plus ? `+${row.plus}` : "", row.minus ? `−${row.minus}` : ""]
      .filter(Boolean)
      .join(" ");
    console.log(
      `${row.product.name.padEnd(width)}  было ${String(row.was).padStart(6)}  ${delta.padEnd(16)} стало ${row.now} ${row.product.unit}   ${row.from.join(", ")}`,
    );
  }
  const pw = Math.max(0, ...portions.map((r) => r.cook.name.length));
  for (const row of portions) {
    console.log(
      `${row.cook.name.padEnd(pw)}  было ${String(row.was).padStart(6)}  −${String(row.minus).padEnd(15)} стало ${row.now} порций   ${row.from.join(", ")}`,
    );
  }
  for (const miss of unknown) {
    console.log(`\n[мера] ${miss.product.name}: «${miss.measure}» не свести к ${miss.product.unit} — ${miss.what}`);
  }

  const count = `${records.cooks.length} готовок, ${records.purchases.length} закупок, ${records.meals.length} приёмов`;
  if (!args.apply) {
    console.log(`\nнепроведённого: ${count}; затронуто продуктов ${changed.length}, готовок ${portions.length}; чтобы записать — --apply`);
    return;
  }
  await client.updateItems(
    collections.products,
    changed.map((r) => ({ id: r.product.id, properties: { qty: r.now } })),
  );
  await client.updateItems(
    collections.cooks,
    portions.map((r) => ({ id: r.cook.id, properties: { remaining: r.now } })),
  );
  for (const kind of ["cooks", "purchases", "meals"]) {
    await client.updateItems(
      collections[kind],
      records[kind].map((id) => ({ id, properties: { sys_counted: true } })),
    );
  }
  console.log(`\nзаписано: ${changed.length} продуктов, ${portions.length} готовок; проведено ${count}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err.message);
    process.exitCode = 2;
  });
}
