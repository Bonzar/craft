#!/usr/bin/env node
// Пересчёт наличия: закупка «куплено» прибавляет, готовка «сделано» вычитает.
//
//   node menu/stock.mjs --from 2026-09-05             # показать, ничего не писать
//   node menu/stock.mjs --from 2026-09-05 --apply     # записать Qty
//
// Окно задаёт человек и применяет ОДИН раз: у продукта нет поля «на какое
// число верно наличие», поэтому второй прогон того же окна посчитает всё дважды.

import { createClient } from "./lib/craft-api.mjs";
import { buildModel, indexModel } from "./lib/model.mjs";
import { ledger } from "./lib/stock.mjs";

const KINDS = ["cooks", "meals", "purchases", "recipes", "products", "eaters", "measures"];

export function parseArgs(argv) {
  const args = { from: null, apply: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--apply") args.apply = true;
    else if (argv[i] === "--from") args.from = argv[++i];
    else throw new Error(`лишний аргумент ${argv[i]}`);
  }
  if (!args.from) throw new Error("нужен --from <дата>: окно пересчёта задаёт человек");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(args.from)) throw new Error("--from: нужна дата вида 2026-09-09");
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
  const { moved, unknown } = ledger(ix, args.from);

  const width = Math.max(0, ...moved.map((r) => r.product.name.length));
  for (const row of moved) {
    const delta = [row.plus ? `+${row.plus}` : "", row.minus ? `−${row.minus}` : ""].filter(Boolean).join(" ");
    console.log(
      `${row.product.name.padEnd(width)}  было ${String(row.was).padStart(6)}  ${delta.padEnd(16)} стало ${row.now} ${row.product.unit}`,
    );
  }
  for (const miss of unknown) {
    console.log(`\n[мера] ${miss.product.name}: «${miss.measure}» не свести к ${miss.product.unit} — ${miss.cook.name}`);
  }

  if (!args.apply) {
    console.log(`\nсчитано с ${args.from}, продуктов затронуто ${moved.length}; чтобы записать — --apply`);
    return;
  }
  await client.updateItems(
    collections.products,
    moved.map((r) => ({ id: r.product.id, properties: { qty: r.now } })),
  );
  console.log(`\nзаписано: ${moved.length} продуктов, окно с ${args.from}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err.message);
    process.exitCode = 2;
  });
}
