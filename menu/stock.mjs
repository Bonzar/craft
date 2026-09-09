#!/usr/bin/env node
// Пересчёт наличия: закупка «куплено» прибавляет, готовка «сделано» вычитает.
//
//   node menu/stock.mjs                       # показать, ничего не писать
//   node menu/stock.mjs --apply               # записать Qty и QtyOn
//   node menu/stock.mjs --through 2026-09-09  # включить и сегодняшний день
//
// Отсечка у каждого продукта своя — колонка `QtyOn`, последний целиком учтённый
// день. День применяется целиком и только когда закончился, поэтому по
// умолчанию считается по вчера: сегодня ещё могут сварить и купить.

import { createClient } from "./lib/craft-api.mjs";
import { buildModel, indexModel } from "./lib/model.mjs";
import { ledger } from "./lib/stock.mjs";

const KINDS = ["cooks", "meals", "purchases", "recipes", "products", "eaters", "measures"];

const DAY = 86400000;
export const yesterday = (now = new Date()) =>
  new Date(now.getTime() - DAY).toISOString().slice(0, 10);

export function parseArgs(argv, now = new Date()) {
  const args = { through: yesterday(now), apply: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--apply") args.apply = true;
    else if (argv[i] === "--through") args.through = argv[++i];
    else throw new Error(`лишний аргумент ${argv[i]}`);
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(args.through)) {
    throw new Error("--through: нужна дата вида 2026-09-09");
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
  const { changed, unknown } = ledger(ix, args.through);

  const width = Math.max(0, ...changed.map((r) => r.product.name.length));
  for (const row of changed) {
    const delta = [row.plus ? `+${row.plus}` : "", row.minus ? `−${row.minus}` : ""]
      .filter(Boolean)
      .join(" ");
    console.log(
      `${row.product.name.padEnd(width)}  c ${row.from ?? "начала"}  было ${String(row.was).padStart(6)}  ${delta.padEnd(16)} стало ${row.now} ${row.product.unit}`,
    );
  }
  for (const miss of unknown) {
    console.log(`\n[мера] ${miss.product.name}: «${miss.measure}» не свести к ${miss.product.unit} — ${miss.what}`);
  }

  if (!args.apply) {
    console.log(`\nпо ${args.through} включительно, продуктов затронуто ${changed.length}; чтобы записать — --apply`);
    return;
  }
  await client.updateItems(
    collections.products,
    changed.map((r) => ({ id: r.product.id, properties: { qty: r.now, qtyon: args.through } })),
  );
  console.log(`\nзаписано: ${changed.length} продуктов, учтено по ${args.through}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err.message);
    process.exitCode = 2;
  });
}
