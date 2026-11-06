#!/usr/bin/env node
// Пометить и посчитать одним действием: статус, движение по наличию и галочку
// «проведено» — за один раз, чтобы цифры не отставали от жизни.
//
//   node menu/mark.mjs --cook "Болоньезе, ср 9.09" --done
//   node menu/mark.mjs --cook "Плов, сб 5.09" --remaining 2
//   node menu/mark.mjs --purchase Фарш --bought --qty 800
//   node menu/mark.mjs --meal "Ср 9.09 · ужин · Влад" --eaten
//   node menu/mark.mjs --meal "Ср 9.09 · ужин · Оля" --skipped
//
// Готовка тратит продукты и рождает порции, приём эти порции съедает — долю
// своего едока из каждого блюда на тарелке. Закупку без плана заводить не
// обязательно: можно поправить Qty руками, догонялка покажет, как это сказалось.

import { createClient } from "./lib/craft-api.mjs";
import { buildModel, indexModel } from "./lib/model.mjs";
import { movements, portionMoves } from "./lib/stock.mjs";

const KINDS = ["cooks", "meals", "purchases", "recipes", "products", "eaters", "measures"];

const DEEDS = {
  done: { kind: "cooks", status: "сделано" },
  cancelled: { kind: "cooks", status: "отменено" },
  bought: { kind: "purchases", status: "куплено" },
  eaten: { kind: "meals", status: "съеден" },
  skipped: { kind: "meals", status: "пропущен" },
};

export function parseArgs(argv) {
  const args = { kind: null, name: null, deed: null, qty: null, remaining: null, dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--cook" || arg === "--purchase" || arg === "--meal") {
      args.kind = { "--cook": "cooks", "--purchase": "purchases", "--meal": "meals" }[arg];
      args.name = argv[++i];
    } else if (arg === "--qty") args.qty = Number(argv[++i]);
    else if (arg === "--remaining") args.remaining = Number(argv[++i]);
    else if (arg === "--dry-run") args.dryRun = true;
    else if (DEEDS[arg.replace(/^--/, "")]) args.deed = arg.replace(/^--/, "");
    else throw new Error(`неизвестный флаг ${arg}`);
  }
  if (!args.kind || !args.name) throw new Error("нужно --cook, --purchase или --meal с именем");
  if (!args.deed && args.remaining === null) throw new Error("нужно сказать, что случилось");
  if (args.deed && DEEDS[args.deed].kind !== args.kind) {
    throw new Error(`--${args.deed} не про эту таблицу`);
  }
  for (const [flag, value] of [["--qty", args.qty], ["--remaining", args.remaining]]) {
    if (value !== null && !Number.isFinite(value)) throw new Error(`${flag}: нужно число`);
  }
  return args;
}

/** Одна запись по имени: точное совпадение, иначе единственное частичное. */
export function pickOne(items, name) {
  const exact = items.filter((i) => i.name === name);
  if (exact.length === 1) return exact[0];
  const lower = name.toLowerCase();
  const near = items.filter((i) => i.name.toLowerCase().includes(lower));
  if (near.length === 1) return near[0];
  if (near.length === 0) throw new Error(`не нашёл «${name}»`);
  throw new Error(`«${name}» подходит нескольким: ${near.map((i) => i.name).join(", ")}`);
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

  const record = pickOne(ix[args.kind], args.name);
  const status = args.deed ? DEEDS[args.deed].status : record.status;
  const was = record.status;

  // Правим запись в памяти и считаем движения уже по ней: так наличие и статус
  // расходятся не могут — они уезжают в Craft одним заходом.
  record.status = status;
  if (args.qty !== null) record.qty = args.qty;
  if (args.remaining !== null) record.remaining = args.remaining;
  // Сваренное блюдо выходит из плиты целым: остаток равен выходу, дальше его
  // подъедают приёмы.
  if (args.deed === "done" && args.remaining === null) record.remaining = record.portions;

  const { moves, unknown } = movements(ix);
  const mine = moves.filter((m) => m.record.id === record.id);
  const byProduct = new Map();
  for (const m of mine) {
    byProduct.set(m.productId, (byProduct.get(m.productId) ?? 0) + m.qty);
  }
  const byCook = new Map();
  for (const m of portionMoves(ix).filter((m) => m.record.id === record.id)) {
    byCook.set(m.cook, (byCook.get(m.cook) ?? 0) + m.qty);
  }

  console.log(`${record.name}: ${was ?? "—"} → ${status}`);
  if (record.remaining !== undefined && args.kind === "cooks" && record.remaining !== null) {
    console.log(`  остаток ${record.remaining} порций`);
  }
  for (const [cook, delta] of byCook) {
    const now = Math.round(((cook.remaining ?? cook.portions) + delta) * 100) / 100;
    console.log(`  ${cook.name.padEnd(34)} −${Math.round(-delta * 100) / 100} → ${now} порций`);
  }
  for (const [id, delta] of byProduct) {
    const product = ix.productById.get(id);
    const now = Math.round(((product.qty ?? 0) + delta) * 100) / 100;
    const sign = delta >= 0 ? `+${Math.round(delta * 100) / 100}` : `−${Math.round(-delta * 100) / 100}`;
    console.log(`  ${product.name.padEnd(34)} ${sign} → ${now} ${product.unit}`);
  }
  for (const miss of unknown.filter((u) => u.what === record.name)) {
    console.log(`  [мера] ${miss.product.name}: «${miss.measure}» не свести к ${miss.product.unit}`);
  }
  if (args.dryRun) {
    console.log("\nничего не записано");
    return;
  }

  const props = { status };
  if (args.qty !== null) props.qty = args.qty;
  if (args.kind === "cooks" && record.remaining !== null) props.remaining = record.remaining;
  if (mine.length > 0 || byCook.size > 0) props.sys_counted = true;
  await client.updateItems(collections[args.kind], [{ id: record.id, properties: props }]);

  if (byCook.size > 0) {
    await client.updateItems(
      collections.cooks,
      [...byCook].map(([cook, delta]) => ({
        id: cook.id,
        properties: {
          remaining: Math.round(((cook.remaining ?? cook.portions) + delta) * 100) / 100,
        },
      })),
    );
  }

  if (byProduct.size > 0) {
    await client.updateItems(
      collections.products,
      [...byProduct].map(([id, delta]) => {
        const product = ix.productById.get(id);
        return {
          id,
          properties: { qty: Math.round(((product.qty ?? 0) + delta) * 100) / 100 },
        };
      }),
    );
  }
  const touched = [
    byProduct.size > 0 ? `продуктов ${byProduct.size}` : "",
    byCook.size > 0 ? `готовок ${byCook.size}` : "",
  ].filter(Boolean).join(", ");
  console.log(`\nзаписано${touched ? `, поправлено: ${touched}` : ""}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err.message);
    process.exitCode = 2;
  });
}
