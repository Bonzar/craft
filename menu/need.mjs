#!/usr/bin/env node
// Список покупок: сколько продуктов нужно на будущие готовки, сколько дома,
// сколько уже заказано и чего не хватает. Ничего не пишет — только считает.
//
//   node menu/need.mjs --from 2026-09-09          # на готовки от этой даты
//   node menu/need.mjs --from 2026-09-09 --all    # включая то, чего хватает

import { createClient } from "./lib/craft-api.mjs";
import { buildModel, indexModel } from "./lib/model.mjs";
import { incoming, need, shortfall } from "./lib/need.mjs";

const KINDS = ["cooks", "meals", "purchases", "recipes", "products", "eaters", "measures"];

export function parseArgs(argv) {
  const args = { from: null, all: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--all") args.all = true;
    else if (argv[i] === "--from") args.from = argv[++i];
    else throw new Error(`лишний аргумент ${argv[i]}`);
  }
  if (args.from && !/^\d{4}-\d{2}-\d{2}$/.test(args.from)) {
    throw new Error("--from: нужна дата вида 2026-09-09");
  }
  return args;
}

/** Считаем то, что ещё предстоит: готовки в плане, начиная с даты. */
export const planned = (cooks, from) =>
  cooks.filter((c) => c.status === "план" && c.date && (!from || c.date >= from));

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const client = createClient({ base: process.env.CRAFT_API_BASE });
  const collections = JSON.parse(process.env.CRAFT_MENU_COLLECTIONS ?? "{}");
  const missing = KINDS.filter((k) => !collections[k]);
  if (missing.length > 0) throw new Error(`не заданы коллекции: ${missing.join(", ")}`);

  const raw = {};
  for (const kind of KINDS) raw[kind] = await client.getItems(collections[kind]);
  const ix = indexModel(buildModel(raw));

  const cooks = planned(ix.cooks, args.from);
  const { rows, unknown } = need(ix, cooks);
  const ordered = incoming(
    ix,
    ix.purchases.filter((p) => p.status === "план"),
  );
  const short = shortfall(rows, ordered);
  const shown = args.all
    ? rows.map((r) => ({
        ...r,
        have: r.product.qty ?? 0,
        ordered: Math.round((ordered.get(r.product.id) ?? 0) * 100) / 100,
        short: 0,
      }))
    : short;

  console.log(`готовок в плане: ${cooks.length}${args.from ? ` c ${args.from}` : ""}\n`);
  const width = Math.max(0, ...shown.map((r) => r.product.name.length));
  for (const row of shown.sort((a, b) => b.short - a.short)) {
    const line = [
      row.product.name.padEnd(width),
      `надо ${row.qty}`,
      `дома ${row.have}`,
      `заказано ${row.ordered}`,
      row.short > 0 ? `— не хватает ${row.short} ${row.product.unit}` : "",
    ];
    console.log(line.filter(Boolean).join("  "));
  }
  if (short.length === 0 && !args.all) console.log("всего хватает");

  for (const miss of unknown) {
    console.log(
      `\n[мера] ${miss.product.name}: «${miss.measure}» не сводится к ${miss.product.unit} — ${miss.cook.name}`,
    );
  }
  process.exitCode = short.length === 0 ? 0 : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err.message);
    process.exitCode = 2;
  });
}
